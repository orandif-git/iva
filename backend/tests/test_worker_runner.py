import asyncio
import copy
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import cv2
import pytest

from tests.helpers import now as utcnow, setup_cameras
from tests.video_gen import ColorDetector, ScriptedReader, make_video, walk
from worker.backend import BackendUnavailable, WorkerBackend
from worker.live import Frame
from worker.pipeline import CameraPipeline
from worker.runner import LiveSettings, run_live
from worker.sink import BackendSink, MemorySink

LINE = [[0.1, 0.5], [0.9, 0.5]]
FPS = 10                                                       # the test videos are 10 pictures per second


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Feed:
    """Plays a video as a camera. Pictures come at the rate asked for (like FFmpeg's fps filter). The position is shared, so a
    restarted reader carries on where the last one stopped."""

    def __init__(self, path, start, clock, stop, epoch_at=None):
        self.cap, self.start, self.clock, self.stop, self.i = cv2.VideoCapture(str(path)), start, clock, stop, 0
        self.epoch, self.epoch_at = 1, epoch_at or {}

    def next(self, fps):
        step = max(1, round(FPS / fps))
        for _ in range(step - 1):
            self.cap.grab(); self.i += 1
        ok, img = self.cap.read()
        if not ok:
            self.stop.set()
            return None
        ts = self.start + timedelta(seconds=self.i / FPS)
        self.epoch = self.epoch_at.get(self.i, self.epoch)
        self.i += 1
        self.clock.now = ts.timestamp()
        return Frame(img, ts, self.epoch)


class FakeReader:
    def __init__(self, feed, fps):
        self.feed, self.fps, self.state, self.last_error, self.reconnects = feed, fps, "streaming", None, 0
        self.queue, self.effective_fps = SimpleNamespace(dropped=0), fps

    def start(self): pass
    def stop(self): self.state = "stopped"
    def down_seconds(self): return 0.0
    def get(self, timeout=0.5): return self.feed.next(self.fps)


class SilentReader(FakeReader):
    """A camera that never sends anything; each wait lets one second of (fake) time pass."""
    def __init__(self, clock, stop, limit):
        super().__init__(None, 5)
        self.clock, self.stop_ev, self.limit, self.state = clock, stop, limit, "reconnecting"

    def get(self, timeout=0.5):
        self.clock.now += 1.0
        if self.clock.now > self.limit:
            self.stop_ev.set()
        return None

    def down_seconds(self): return self.clock.now


def cfg_json(*, anpr=False, footfall=True, mode="adaptive", idle=2, active=10, fixed=10, line=LINE, active_flag=True, **extra):
    fps = {"mode": mode, "idle_fps": idle, "active_fps": active, "fixed_fps": fixed, "capture_fps": fixed if mode == "fixed" else active}
    zones = []
    if line:
        zones.append({"kind": "count_line", "points": line, "in_direction": [0, 1]})
    if anpr:
        zones.append({"kind": "anpr_zone", "points": [[0, 0.2], [1, 0.2], [1, 1], [0, 1]], "in_direction": [0, 1]})
    return {"camera_id": "cam", "name": "Test camera", "enabled": True, "use_footfall": footfall, "use_anpr": anpr, "active": active_flag,
            "frame_rate": fps, "zones": zones, "source_set_at": None, **extra}


class FakeBackend:
    def __init__(self, cfg, url="rtsp://u:p@10.0.0.1/x", clock=None):
        self.cfg, self.url, self.clock = cfg, url, clock
        self.statuses, self.config_calls, self.keep_going, self.changes, self.fail_config = [], 0, True, [], 0

    async def config(self):
        self.config_calls += 1
        if self.fail_config and self.config_calls > 1 and self.fail_config >= self.config_calls - 1:
            raise BackendUnavailable("down")
        for at, change in list(self.changes):                   # changes an administrator makes at a given stream time
            if self.clock.now >= at:
                change(self.cfg); self.changes.remove((at, change))
        return None if self.cfg is None else copy.deepcopy(self.cfg)

    async def source(self): return self.url
    async def status(self, state, fps, error, dropped=None):
        self.statuses.append((state, fps, error)); return self.keep_going


def person(x, y0, y1, t0, t1, show):
    p = walk(x, y0, y1, t0, t1)
    return lambda t: p(t) if show[0] <= t <= show[1] else None


@pytest.fixture(scope="module")
def two_crossings(tmp_path_factory):
    """60 s. Empty, then one person walks down across the line (in), empty, then one walks up (out), empty."""
    a, b = person(200, 60, 300, 22, 26, (21, 27)), person(420, 300, 60, 34, 38, (33, 39))
    return make_video(tmp_path_factory.mktemp("v") / "two.mp4", 60, lambda t: ([p for p in (a(t), b(t)) if p], []), fps=FPS)


def vehicle_video(tmp_path, t0=20.3, t1=28.0):
    return make_video(tmp_path / "veh.mp4", 40, lambda t: ([], [(320, 200, "GJ14AW1013")] if t0 <= t < t1 else []), fps=FPS)


async def go(video, backend, *, anpr_reads=None, sink=None, settings=None, reader=None, epoch_at=None, clock=None, start=None):
    clock, stop = clock or Clock(), asyncio.Event()
    backend.clock = clock
    sink = sink or MemorySink()
    detector = ColorDetector()
    feed = Feed(video, start or datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc), clock, stop, epoch_at)
    built, readers, seen = [], [], []

    def reader_factory(url, w, h, fps):
        built.append((url, fps))
        r = reader(clock, stop) if reader else FakeReader(feed, fps)
        readers.append(r)
        return r

    def pipeline_factory(cfg):
        p = CameraPipeline(cfg, detector, ScriptedReader(anpr_reads or ["GJ14AW1013"]) if cfg.use_anpr else None, sink, run_id="r" + uuid.uuid4().hex[:5], min_read_interval=0)
        orig = p.process
        p.process = lambda f, ts: (seen.append(ts.timestamp() - feed.start.timestamp()), orig(f, ts))[1]
        built.append(("pipeline", cfg))
        return p

    st = settings or LiveSettings(config_refresh=5, health_interval=5, flush_interval=1, retry_delay=0.01)
    result = await run_live(backend=backend, sink=sink, reader_factory=reader_factory, pipeline_factory=pipeline_factory, settings=st, stop=stop, clock=clock)
    return SimpleNamespace(result=result, sink=sink, detector=detector, built=built, seen=seen, readers=readers, backend=backend)


def counts(sink):
    return (sum(v[0] for v in sink.footfall.values()), sum(v[1] for v in sink.footfall.values()))


def rate(seen, a, b):
    return sum(a <= t < b for t in seen) / (b - a)


# ----------------------------------------------------------- the main promise
async def test_adaptive_counts_exactly_what_a_constant_rate_counts_with_far_less_work(two_crossings):
    fixed = await go(two_crossings, FakeBackend(cfg_json(mode="fixed", fixed=10)))
    adaptive = await go(two_crossings, FakeBackend(cfg_json(mode="adaptive", idle=2, active=10)))
    assert counts(fixed.sink) == counts(adaptive.sink) == (1, 1)
    assert adaptive.result["processed"] == len(adaptive.seen) and fixed.result["processed"] == len(fixed.seen)      # the summary counts every analysed picture
    assert fixed.detector.calls >= 550
    assert adaptive.detector.calls < 0.5 * fixed.detector.calls                      # under half the detector runs on this clip
    assert 1.7 <= rate(adaptive.seen, 2, 18) <= 2.3 and rate(adaptive.seen, 23, 26) >= 9      # slow when empty, fast with someone there


async def test_the_default_policy_is_adaptive_two_to_five(two_crossings):
    r = await go(two_crossings, FakeBackend(cfg_json(idle=2, active=5)))
    streams = [b for b in r.built if b[0] != "pipeline"]
    assert counts(r.sink) == (1, 1) and streams == [("rtsp://u:p@10.0.0.1/x", 5.0)]     # reads the stream at the active rate and thins it when idle
    assert rate(r.seen, 23, 26) == pytest.approx(5, abs=0.7)


async def test_motion_wakes_it_before_the_next_idle_sample(tmp_path):
    video = vehicle_video(tmp_path)
    off = await go(video, FakeBackend(cfg_json(footfall=False, anpr=True)), settings=LiveSettings(use_motion=False, config_refresh=99, health_interval=99, flush_interval=99))
    on = await go(video, FakeBackend(cfg_json(footfall=False, anpr=True)), settings=LiveSettings(use_motion=True, config_refresh=99, health_interval=99, flush_interval=99))
    first = lambda r: min(t for t in r.seen if t >= 20.3)
    assert first(off) - 20.3 <= 0.65                                                  # within one idle interval
    assert first(on) - 20.3 <= 0.15                                                   # at once: it saw the movement
    assert 1.7 <= rate(on.seen, 2, 18) <= 2.3                                         # and a still scene does not trigger it


async def test_a_vehicle_that_stops_in_view_keeps_the_rate_up_until_it_leaves(tmp_path):
    r = await go(vehicle_video(tmp_path, 10, 30), FakeBackend(cfg_json(footfall=False, anpr=True, idle=2, active=10)))
    assert rate(r.seen, 12, 29) >= 9 and rate(r.seen, 35, 40) <= 2.5                  # fast the whole time it sits there, slow after
    assert len(r.sink.plates) == 1 and r.sink.plates[0]["plate_raw"] == "GJ14AW1013"


# ------------------------------------------------- settings changed in the admin console
async def test_a_frame_rate_change_reaches_a_running_worker_without_a_restart(two_crossings):
    cfg = cfg_json(idle=2, active=5)
    b = FakeBackend(cfg)
    b.changes = [(datetime(2026, 11, 5, 10, 0, 28, tzinfo=timezone.utc).timestamp(),            # after the first person has gone (27 s)
                  lambda c: c["frame_rate"].update({"active_fps": 10, "capture_fps": 10}))]
    r = await go(two_crossings, b)
    assert [f for u, f in r.built if u != "pipeline"] == [5.0, 10.0]                  # the stream was re-opened at the new speed
    assert rate(r.seen, 23, 26) == pytest.approx(5, abs=0.8)                          # first crossing: the old active rate
    assert rate(r.seen, 35, 38) >= 9 and counts(r.sink) == (1, 1)                     # second crossing: the new, faster rate


async def test_switching_a_camera_to_a_fixed_rate_takes_effect(two_crossings):
    b = FakeBackend(cfg_json(idle=2, active=5))
    b.changes = [(datetime(2026, 11, 5, 10, 0, 15, tzinfo=timezone.utc).timestamp(),
                  lambda c: c["frame_rate"].update({"mode": "fixed", "fixed_fps": 4, "capture_fps": 4}))]
    r = await go(two_crossings, b)
    assert rate(r.seen, 20, 58) == pytest.approx(4, abs=0.4)                          # a steady 4 whether anyone is there or not


async def test_new_zones_rebuild_the_analysis_without_stopping(two_crossings):
    b = FakeBackend(cfg_json())
    b.changes = [(datetime(2026, 11, 5, 10, 0, 15, tzinfo=timezone.utc).timestamp(), lambda c: c["zones"][0].update({"points": [[0.1, 0.52], [0.9, 0.52]]}))]
    r = await go(two_crossings, b)
    assert len([x for x in r.built if x[0] == "pipeline"]) == 2 and counts(r.sink) == (1, 1) and r.result["exit"] == "stopped"


async def test_a_short_backend_outage_does_not_stop_the_worker(two_crossings):
    b = FakeBackend(cfg_json())
    b.fail_config = 2                                                                  # the refreshes at 5 s and 10 s cannot reach the backend
    r = await go(two_crossings, b)
    assert r.result["exit"] == "stopped" and counts(r.sink) == (1, 1)


async def test_it_waits_for_the_backend_at_start_up():
    class Late(FakeBackend):
        async def config(self):
            self.config_calls += 1
            if self.config_calls < 3:
                raise BackendUnavailable("not yet")
            return copy.deepcopy(self.cfg)
    stop = asyncio.Event()
    clock = Clock()
    b = Late(cfg_json())
    r = await run_live(backend=b, sink=MemorySink(), reader_factory=lambda *a: SilentReader(clock, stop, 3), pipeline_factory=lambda c: SimpleNamespace(finish=lambda: None, reset=lambda: None),
                       settings=LiveSettings(retry_delay=0.01, health_interval=99, config_refresh=99, flush_interval=99), stop=stop, clock=clock)
    assert b.config_calls >= 3 and r["exit"] == "stopped"


# ------------------------------------------------------------------- interruptions
async def test_a_reconnect_closes_the_open_vehicle_visit_and_starts_tracking_afresh(tmp_path):
    video = vehicle_video(tmp_path, 10, 25)
    r = await go(video, FakeBackend(cfg_json(footfall=False, anpr=True)), epoch_at={150: 2})        # the stream drops at 15 s
    assert len(r.sink.plates) == 2                                                    # one visit before the gap, one after: never stitched across it
    first, second = (datetime.fromisoformat(p["seen_at"].replace("Z", "+00:00")) for p in r.sink.plates)
    start = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    assert (first - start).total_seconds() < 15.1 and (second - start).total_seconds() >= 15


async def test_a_person_mid_crossing_during_a_gap_is_never_counted_twice(two_crossings):
    r = await go(two_crossings, FakeBackend(cfg_json()), epoch_at={240: 2})            # the stream breaks while the first person is halfway
    assert counts(r.sink)[0] <= 1 and counts(r.sink)[1] == 1


async def test_timers_run_while_no_pictures_arrive_and_a_long_outage_is_reported_offline():
    clock, stop = Clock(), asyncio.Event()
    b, sink = FakeBackend(cfg_json(), clock=clock), MemorySink()
    flushes = []
    orig = sink.flush
    sink.flush = lambda force=False: (flushes.append(clock.now), orig())[1]
    r = await run_live(backend=b, sink=sink, reader_factory=lambda *a: SilentReader(clock, stop, 130), pipeline_factory=lambda c: SimpleNamespace(finish=lambda: None, reset=lambda: None),
                       settings=LiveSettings(health_interval=5, flush_interval=2, config_refresh=99), stop=stop, clock=clock)
    states = [s[0] for s in b.statuses]
    assert states[0] == "reconnecting" and "offline" in states[:-1] and states[-1] == "offline"      # down over 60 s becomes offline; the last is the shutdown report
    assert len(flushes) >= 50 and r["exit"] == "stopped"


async def test_the_final_report_says_the_worker_stopped(two_crossings):
    r = await go(two_crossings, FakeBackend(cfg_json()))
    assert r.backend.statuses[-1] == ("offline", None, "the analysis worker stopped")
    assert any(s[0] == "streaming" and s[1] for s in r.backend.statuses[:-1])           # earlier reports carried the real analysis rate


# -------------------------------------------------------------------- stopping
async def test_a_camera_that_is_switched_off_stops_the_worker(two_crossings):
    b = FakeBackend(cfg_json())
    b.keep_going = False
    r = await go(two_crossings, b)
    assert r.result["exit"] == "inactive"
    b2 = FakeBackend(cfg_json())
    b2.changes = [(datetime(2026, 11, 5, 10, 0, 8, tzinfo=timezone.utc).timestamp(), lambda c: c.update(active=False))]
    assert (await go(two_crossings, b2)).result["exit"] == "inactive"


async def test_start_up_problems_end_cleanly(two_crossings):
    assert (await go(two_crossings, FakeBackend(None))).result == {"exit": "unknown_camera"}
    assert (await go(two_crossings, FakeBackend(cfg_json(active_flag=False)))).result == {"exit": "inactive"}
    none_url = FakeBackend(cfg_json())
    none_url.url = None
    assert (await go(two_crossings, none_url)).result == {"exit": "no_source"}


async def test_a_refused_camera_stops_the_worker_through_the_sink(two_crossings):
    class Refusing(MemorySink):
        async def flush(self, force=False):
            self.fatal = "plates_not_enabled"
            return False
    r = await go(two_crossings, FakeBackend(cfg_json()), sink=Refusing())
    assert r.result["exit"] == "sink:plates_not_enabled"


# ----------------------------------------------------- the real backend, end to end
async def test_live_run_against_the_real_backend_reports_health_and_follows_the_admin_console(client, make, su, two_crossings):
    owner = await make.staff("owner")
    t, u, (cam,), _ = await setup_cameras(client, make)
    tech = await make.staff("owner")
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://viewer:S3cret@10.20.0.14/Streaming/Channels/102", "reason": "Site VPN is up"}, headers=tech["headers"])
    await client.put(f"/v1/cameras/{cam}/zones", json={"zones": [{"kind": "count_line", "points": LINE, "in_direction": [0, 1]}]}, headers=u["headers"])
    token = make.service()["Authorization"].split()[1]
    clock = Clock()
    reports, raised = [], []

    start = utcnow() - timedelta(minutes=5)                                          # events must be recent enough for the backend

    class Link(WorkerBackend):
        async def config(self):
            if clock.now >= start.timestamp() + 28 and not raised:
                raised.append(1)                                                      # the administrator raises this camera to a steady 10 fps
                r = await client.put(f"/admin/v1/cameras/{cam}/frame-rate", json={"mode": "fixed", "fixed_fps": 10, "reason": "Fast lane at this gate"}, headers=owner["headers"])
                assert r.status_code == 200
            return await super().config()

        async def status(self, state, fps, error, dropped=None):
            reports.append((state, fps))
            return await super().status(state, fps, error, dropped)

    r = await go(two_crossings, Link(client, token, cam), sink=BackendSink(client, token), start=start, clock=clock)
    assert r.result["exit"] == "stopped" and r.result["unsent"] == 0
    assert [f for u_, f in r.built if u_ != "pipeline"] == [5.0, 10.0]                # default adaptive, then the console's fixed 10
    assert [b[0] for b in r.built if b[0] != "pipeline"][0] == "rtsp://viewer:S3cret@10.20.0.14/Streaming/Channels/102"   # from the backend, decrypted for the worker
    row = await su.fetchrow("SELECT sum(people_in)::int AS i, sum(people_out)::int AS o FROM app.footfall_counts WHERE camera_id=$1", uuid.UUID(cam))
    assert (row["i"], row["o"]) == (1, 1)
    streaming = [fps for st, fps in reports if st == "streaming" and fps]
    assert streaming and min(streaming) < 5 and max(streaming) > 5                    # the reported average moved with the rate
    cam_row = await su.fetchrow("SELECT stream_state, stream_error FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    assert cam_row["stream_state"] == "offline" and cam_row["stream_error"] == "the analysis worker stopped"
    shown = (await client.get(f"/admin/v1/tenants/{t}/cameras", headers=owner["headers"])).json()["cameras"][0]
    assert shown["effective"]["mode"] == "fixed" and shown["effective"]["capture_fps"] == 10 and shown["override"]["mode"] == "fixed"


async def test_suspending_a_tenant_stops_a_running_worker(client, make, su, two_crossings):
    t, u, (cam,), _ = await setup_cameras(client, make)
    tech = await make.staff("owner")
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://10.20.0.14/x", "reason": "Site VPN is up"}, headers=tech["headers"])
    token = make.service()["Authorization"].split()[1]
    clock = Clock()
    start = utcnow() - timedelta(minutes=5)

    class Link(WorkerBackend):
        async def status(self, state, fps, error, dropped=None):
            if clock.now >= start.timestamp() + 12:
                await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
            return await super().status(state, fps, error, dropped)

    r = await go(two_crossings, Link(client, token, cam), sink=BackendSink(client, token), start=start, clock=clock)
    assert r.result["exit"] == "inactive" and r.result["frames_seen"] < 200


async def test_a_change_in_the_streams_state_is_reported_at_once_not_on_the_next_timer(two_crossings):
    class Coming(FakeReader):
        """Starts out without a picture ("reconnecting"), then the stream comes up."""
        n = 0

        def get(self, timeout=0.5):
            if Coming.n < 6:
                Coming.n += 1
                self.state = "reconnecting"
                return None
            self.state = "streaming"
            return self.feed.next(self.fps)

    Coming.n = 0
    start = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    clock, stop = Clock(), asyncio.Event()
    clock.now = start.timestamp()                    # begin at the video's own time, so a 999 s timer really is far away
    b2 = FakeBackend(cfg_json(), clock=clock)
    feed = Feed(two_crossings, start, clock, stop)
    sink = MemorySink()
    await run_live(backend=b2, sink=sink, reader_factory=lambda u, w, h, f: Coming(feed, f), pipeline_factory=lambda c: CameraPipeline(c, ColorDetector(), None, sink),
                   settings=LiveSettings(config_refresh=999, health_interval=999, flush_interval=999), stop=stop, clock=clock)
    states = [x[0] for x in b2.statuses]
    assert states[:2] == ["reconnecting", "streaming"] and states[-1] == "offline"      # "streaming" was reported straight away: the timer was set to 999 s
