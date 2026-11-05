import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from worker.detector import _decode, _nms
from worker.footfall import LineCounter
from worker.geometry import iou, point_in_polygon, segments_intersect, signed_distance
from worker.pipeline import CameraConfig
from worker.plates import vote
from worker.sink import BackendSink
from worker.source import SourceError, VideoFileSource
from worker.tracker import Detection, Tracker
from tests.video_gen import make_video


# ------------------------------------------------------------------- geometry
def test_geometry_basics():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert point_in_polygon((0.5, 0.5), sq) and not point_in_polygon((1.5, 0.5), sq)
    tri = [(0, 0), (1, 0), (0, 1)]
    assert point_in_polygon((0.2, 0.2), tri) and not point_in_polygon((0.8, 0.8), tri)
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0)) and not segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))
    assert signed_distance((0.5, 0.2), (0, 0.5), (1, 0.5)) != signed_distance((0.5, 0.8), (0, 0.5), (1, 0.5))
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1 and iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0
    assert 0.3 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 0.4


# -------------------------------------------------------------------- decoding
def test_yolox_cell_offsets_are_decoded_to_pixel_boxes():
    size = 416
    raw = np.zeros((3549, 85), np.float32)
    # Cell (x=3, y=2) at stride 8 is candidate number 2*52+3. Offsets 0.5,0.5 and sizes e^ln(4), e^ln(2) of a stride.
    i = 2 * 52 + 3
    raw[i, :4] = [0.5, 0.5, np.log(4), np.log(2)]
    out = _decode(raw, size)
    assert np.allclose(out[i, :4], [3.5 * 8, 2.5 * 8, 32, 16])
    # A candidate in the stride-16 block (starts after 52*52 = 2704 cells): cell (x=1, y=1) of 26 columns.
    j = 2704 + 1 * 26 + 1
    raw[j, :4] = [0, 0, 0, 0]
    assert np.allclose(_decode(raw, size)[j, :4], [16, 16, 16, 16])
    k = 2704 + 676 + 5 * 13 + 7                                    # stride-32 block, cell (x=7, y=5)
    assert np.allclose(_decode(raw, size)[k, :2], [7 * 32, 5 * 32])


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    assert sorted(_nms(boxes, np.array([0.6, 0.9, 0.5]), 0.45)) == [1, 2]


# --------------------------------------------------------------------- tracker
def det(x, y, s=0.9, label="person", w=30, h=70):
    return Detection((x - w / 2, y - h / 2, x + w / 2, y + h / 2), s, label)


def test_tracker_keeps_identities_apart_and_stable():
    t = Tracker()
    ids = []
    for f in range(8):
        tr = t.update([det(100 + f * 6, 100), det(400 - f * 6, 200)], f * 0.2)
        ids.append(sorted(x.id for x in tr))
    assert ids[0] == []                                                        # not trusted on first sight
    assert len(ids[-1]) == 2 and ids[-1] == ids[-2] == ids[3]                  # two people, same ids from confirmation onward


def test_tracker_bridges_a_short_disappearance_using_motion():
    t = Tracker()
    for f in range(5):
        tr = t.update([det(100 + f * 10, 100)], f * 0.2)
    tid = tr[0].id
    for f in range(5, 8):
        t.update([], f * 0.2)                                                 # hidden for 3 frames (0.6 s)
    tr = t.update([det(100 + 8 * 10, 100)], 8 * 0.2)                          # reappears where it should be
    assert [x.id for x in tr] == [tid]


def test_a_track_survives_the_same_gap_in_seconds_whatever_the_frame_rate():
    for step in (0.5, 0.2, 0.04):                                             # 2, 5 and 25 frames per second
        t = Tracker()
        n = int(1.0 / step)
        for f in range(n):
            tr = t.update([det(100 + f * step * 20, 100)], f * step)           # walking slowly: 20 px per second
        tid = tr[0].id
        last = (n - 1) * step
        tr = t.update([det(100 + (last + 1.5) * 20, 100)], last + 1.5)         # reappears after 1.5 s hidden
        assert [x.id for x in tr] == [tid], f"step {step}"


def test_prediction_uses_elapsed_time_not_frame_count():
    """Learn the speed from frames 0.1 s apart, then sample every 0.5 s. A per-frame prediction would expect a 5 px step and miss
    the real 25 px one; a time-based prediction expects 25 px and keeps the same track."""
    t = Tracker()
    ts, x = 0.0, 100.0
    seen = set()
    for gap in (0.1, 0.1, 0.1, 0.1, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1):
        ts += gap
        x += 50 * gap                                                          # a steady 50 px per second
        for tr in t.update([det(x, 100)], ts):
            seen.add(tr.id)
    assert len(seen) == 1 and len(t.tracks) == 1


def test_dropped_frames_do_not_split_a_mover():
    t = Tracker()
    ids = set()
    for ts in (0.0, 0.2, 0.4, 0.6, 1.2, 1.4, 1.6):                             # the frames at 0.8 and 1.0 were dropped
        for tr in t.update([det(100 + ts * 50, 100)], ts):
            ids.add(tr.id)
    assert len(ids) == 1


def test_a_mover_too_fast_for_the_rate_cannot_be_linked_which_is_what_a_higher_rate_is_for():
    """Tracking links two sightings by overlap. A 30 px wide person moving 40 px between samples never overlaps itself."""
    slow, fast = Tracker(), Tracker()
    for f in range(6):
        slow.update([det(100 + f * 40, 100)], f * 0.5)                         # 80 px/s sampled at 2 fps: 40 px steps, no overlap
        fast.update([det(100 + f * 8, 100)], f * 0.1)                          # the same speed sampled at 10 fps: 8 px steps
    assert not [t for t in slow.tracks if t.confirmed] and len([t for t in fast.tracks if t.confirmed]) == 1


def test_a_weak_detection_keeps_a_track_alive_but_cannot_start_one():
    t = Tracker()
    for f in range(4):
        tr = t.update([det(100, 100)], f * 0.2)
    tid = tr[0].id
    assert [x.id for x in t.update([det(100, 100, s=0.25)], 0.8)] == [tid]     # rescued by a weak detection
    fresh = Tracker()
    assert fresh.update([det(300, 300, s=0.25)], 0) == [] and fresh.tracks == []


def test_ended_tracks_are_reported_once_and_a_new_person_gets_a_new_id():
    t = Tracker(max_gap=0.6)
    for f in range(4):
        tr = t.update([det(100, 100)], f * 0.2)
    first = tr[0].id
    for f in range(4, 10):
        t.update([], f * 0.2)                                                  # gone for over 0.6 s
    done = t.drain_finished()
    assert [x.id for x in done] == [first] and t.drain_finished() == []
    for f in range(10, 13):
        tr = t.update([det(100, 100)], f * 0.2)
    assert tr[0].id != first


def test_flush_ends_everything_confirmed():
    t = Tracker()
    for f in range(4):
        t.update([det(100, 100), det(300, 100)], f * 0.2)
    assert len(t.flush()) == 2 and t.tracks == []


# ---------------------------------------------------------------- line counting
LINE = ((0.1, 0.5), (0.9, 0.5), (0, 1))                                        # horizontal; moving down is "in"


def walk_path(counter, pts, tid=1):
    return [e for p in pts if (e := counter.update(tid, p))]


def test_crossing_downwards_is_in_and_upwards_is_out():
    c = LineCounter(*LINE)
    assert walk_path(c, [(0.5, 0.3), (0.5, 0.45), (0.5, 0.6), (0.5, 0.8)]) == ["in"]
    assert walk_path(c, [(0.5, 0.8), (0.5, 0.55), (0.5, 0.3)], tid=2) == ["out"]


def test_jitter_near_the_line_does_not_count():
    c = LineCounter(*LINE)
    assert walk_path(c, [(0.5, 0.3), (0.5, 0.495), (0.5, 0.505), (0.5, 0.49), (0.5, 0.51), (0.5, 0.3)]) == []


def test_walking_around_the_end_of_the_line_does_not_count():
    c = LineCounter(*LINE)
    assert walk_path(c, [(0.97, 0.3), (0.97, 0.55), (0.97, 0.8)]) == []        # passes beside the line, not through it


def test_turning_back_counts_in_then_out():
    c = LineCounter(*LINE)
    assert walk_path(c, [(0.5, 0.3), (0.5, 0.6), (0.5, 0.3)]) == ["in", "out"]


def test_in_direction_decides_which_way_is_in():
    flipped = LineCounter((0.1, 0.5), (0.9, 0.5), (0, -1))
    assert walk_path(flipped, [(0.5, 0.3), (0.5, 0.6)]) == ["out"]
    default = LineCounter((0.1, 0.5), (0.9, 0.5))
    assert len(walk_path(default, [(0.5, 0.3), (0.5, 0.6)])) == 1


def test_two_people_are_counted_separately():
    c = LineCounter(*LINE)
    evs = [c.update(1, (0.4, 0.3)), c.update(2, (0.6, 0.7)), c.update(1, (0.4, 0.7)), c.update(2, (0.6, 0.3))]
    assert sorted(e for e in evs if e) == ["in", "out"]


# ---------------------------------------------------------------------- voting
@pytest.mark.parametrize("reads,expected", [
    ([("GJ14AW1013", .9), ("1J14AW1013", .9), ("GJ14AW1O13", .9)], "GJ14AW1013"),          # independent errors are outvoted
    ([("GJ14AW1013", .9), ("GJ14AW1013", .8), ("N09CK8830", .5)], "GJ14AW1013"),           # the likelier length wins
    ([("GJ14AW1013", .3), ("GJ14AW1O13", .95), ("GJ14AW1O13", .9)], "GJ14AW1O13"),         # confidence is the weight
    ([("MH12AB1234", .9)], "MH12AB1234"),
])
def test_vote_combines_noisy_reads(reads, expected):
    assert vote(reads)[0] == expected


def test_vote_confidence_reflects_agreement():
    agree = vote([("GJ14AW1013", .9)] * 3)[1]
    disagree = vote([("GJ14AW1013", .9), ("GJ14AW1O13", .9), ("GJ14AW1I13", .9)])[1]
    assert agree == pytest.approx(0.9) and disagree < agree
    assert vote([]) == ("", 0.0)


# ------------------------------------------------------------------------ sink
class Backend:
    """A pretend backend that records what it receives and can be told how to answer."""
    def __init__(self):
        self.calls, self.script = [], []

    def handler(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body["events"]))
        status, payload = self.script.pop(0) if self.script else (200, None)
        if payload is None:
            payload = {"accepted": len(body["events"]), "duplicates": 0, "rejected": []}
        return httpx.Response(status, json=payload)


def make_sink(backend, **kw):
    t = [0.0]
    sleeps = []

    async def sleep(s):
        sleeps.append(s); t[0] += s
    sink = BackendSink(httpx.AsyncClient(transport=httpx.MockTransport(backend.handler), base_url="http://x"), "t" * 40, clock=lambda: t[0], sleep=sleep, **kw)
    return sink, t, sleeps


MIN = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


async def test_sink_splits_batches_at_the_backend_limits():
    b = Backend()
    sink, _, _ = make_sink(b)
    for i in range(1001):
        sink.set_footfall("cam", MIN + timedelta(minutes=i), 1, 0)
    for i in range(201):
        sink.add_plate("cam", {"plate_raw": f"P{i}"})
    assert await sink.flush() is True
    assert [(p.split("/")[-1], len(ev)) for p, ev in b.calls] == [("footfall", 1000), ("footfall", 1), ("plates", 200), ("plates", 1)]
    assert sink.pending == 0


async def test_sink_sends_the_latest_total_for_a_minute_once():
    b = Backend()
    sink, _, _ = make_sink(b)
    for n in range(1, 6):
        sink.set_footfall("cam", MIN, n, n - 1)
    await sink.flush()
    assert len(b.calls) == 1 and b.calls[0][1][0]["people_in"] == 5 and b.calls[0][1][0]["people_out"] == 4


async def test_sink_keeps_events_and_backs_off_when_the_backend_is_down():
    b = Backend()
    b.script = [(503, {}), (503, {}), (200, None)]
    sink, t, _ = make_sink(b)
    sink.set_footfall("cam", MIN, 3, 1)
    assert await sink.flush() is False and sink.pending == 1
    calls = len(b.calls)
    assert await sink.flush() is False and len(b.calls) == calls            # too soon: waits instead of hammering
    t[0] += 3
    assert await sink.flush() is False                                      # second failure, longer wait
    t[0] += 5
    assert await sink.flush() is True and sink.pending == 0 and sink.stats["retries"] == 2


async def test_sink_unreachable_backend_is_retried_until_close_gives_up():
    def boom(request):
        raise httpx.ConnectError("down")
    t = [0.0]
    slept = []

    async def sleep(s):
        slept.append(s)
    sink = BackendSink(httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://x"), "t" * 40, clock=lambda: t[0], sleep=sleep)
    sink.add_plate("cam", {"plate_raw": "GJ14AW1013"})
    assert await sink.close(attempts=3) == 1                                 # reports what was not delivered
    assert len(slept) == 3


async def test_sink_drops_a_batch_the_backend_calls_invalid_instead_of_looping():
    b = Backend()
    b.script = [(422, {"error": {}})]
    sink, _, _ = make_sink(b)
    sink.add_plate("cam", {"plate_raw": "X"})
    assert await sink.flush() is True and sink.stats["dropped"] == 1


async def test_sink_stops_on_a_bad_token_and_on_a_refused_camera():
    b = Backend()
    b.script = [(401, {})]
    sink, _, _ = make_sink(b)
    sink.add_plate("cam", {"plate_raw": "X"})
    await sink.flush()
    assert sink.fatal == "not_authorised"

    b2 = Backend()
    b2.script = [(200, {"accepted": 0, "duplicates": 0, "rejected": [{"index": 0, "reason": "tenant_suspended"}]})]
    s2, _, _ = make_sink(b2)
    s2.add_plate("cam", {"plate_raw": "X"})
    await s2.flush()
    assert s2.fatal == "tenant_suspended" and s2.rejected == Counter({"tenant_suspended": 1})

    b3 = Backend()
    b3.script = [(200, {"accepted": 0, "duplicates": 0, "rejected": [{"index": 0, "reason": "too_old"}]})]
    s3, _, _ = make_sink(b3)
    s3.add_plate("cam", {"plate_raw": "X"})
    await s3.flush()
    assert s3.fatal is None and s3.pending == 0                              # a late event is skipped, not fatal


async def test_sink_buffer_is_bounded_and_counts_what_it_drops():
    sink, _, _ = make_sink(Backend(), max_buffer=5)
    for i in range(8):
        sink.add_plate("cam", {"plate_raw": f"P{i}"})
    assert sink.pending == 5 and sink.stats["dropped"] == 3 and sink.plates[0]["plate_raw"] == "P3"


# ----------------------------------------------------------------------- source
def test_source_samples_to_the_target_rate_with_correct_timestamps(tmp_path):
    p = make_video(tmp_path / "v.mp4", 4, lambda t: ([], []), fps=25)
    start = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
    s = VideoFileSource(p, target_fps=5, start_time=start)
    frames = list(s.frames())
    assert s.step == 5 and s.effective_fps == 5 and abs(s.duration - 4) < 0.1
    assert len(frames) == 20
    assert frames[0][1] == start and (frames[1][1] - frames[0][1]).total_seconds() == pytest.approx(0.2)
    assert frames[-1][0].shape[:2] == (360, 640)


def test_source_places_the_clip_to_end_now_by_default(tmp_path):
    p = make_video(tmp_path / "v.mp4", 2, lambda t: ([], []))
    s = VideoFileSource(p)
    assert abs((datetime.now(timezone.utc) - s.start).total_seconds() - 2) < 1.5


def test_source_rejects_missing_and_damaged_files(tmp_path):
    with pytest.raises(SourceError):
        VideoFileSource(tmp_path / "nope.mp4")
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a video" * 100)
    with pytest.raises(SourceError):
        VideoFileSource(bad)


def test_camera_config_is_read_from_the_backend_answer():
    cfg = CameraConfig.from_backend({"camera_id": "c1", "name": "Gate", "use_footfall": True, "use_anpr": True, "zones": [
        {"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]},
        {"kind": "anpr_zone", "points": [[0, 0.4], [1, 0.4], [1, 1], [0, 1]], "in_direction": [0, 1]}]})
    assert cfg.line == ((0.1, 0.5), (0.9, 0.5), (0, 1)) and len(cfg.zone) == 4 and cfg.zone_direction == (0, 1)
    assert CameraConfig.from_backend({"camera_id": "c2"}).line is None


# ------------------------------------------------------------------------------- evidence pictures
async def test_the_sink_uploads_the_picture_before_the_plate_and_never_sends_it_inline():
    import json as _json
    import httpx as _httpx
    from worker.sink import BackendSink
    seen = []

    def handler(req):
        seen.append((req.url.path, req.content))
        if req.url.path == "/internal/v1/evidence":
            return _httpx.Response(201, json={"evidence_id": "11111111-1111-1111-1111-111111111111"})
        return _httpx.Response(200, json={"accepted": 1, "duplicates": 0, "rejected": []})
    sink = BackendSink(_httpx.AsyncClient(transport=_httpx.MockTransport(handler), base_url="http://b"), "t" * 40)
    sink.add_plate("cam", {"plate_raw": "GJ14AW1013"}, snapshot=b"\xff\xd8\xffJPEG")
    assert await sink.flush(force=True)
    assert [p for p, _ in seen] == ["/internal/v1/evidence", "/internal/v1/events/plates"]
    assert seen[0][1] == b"\xff\xd8\xffJPEG"
    sent = _json.loads(seen[1][1])["events"][0]
    assert sent["evidence_id"] == "11111111-1111-1111-1111-111111111111" and not any(k.startswith("_") for k in sent)
    assert sink.stats["pictures_sent"] == 1


async def test_a_plate_still_goes_out_when_its_picture_cannot_be_uploaded():
    import httpx as _httpx
    from worker.sink import BackendSink
    plates = []

    def handler(req):
        if req.url.path == "/internal/v1/evidence":
            return _httpx.Response(502, json={})
        plates.append(req.content)
        return _httpx.Response(200, json={"accepted": 1, "duplicates": 0, "rejected": []})
    sink = BackendSink(_httpx.AsyncClient(transport=_httpx.MockTransport(handler), base_url="http://b"), "t" * 40, clock=lambda: 1e9)
    sink.add_plate("cam", {"plate_raw": "GJ14AW1013"}, snapshot=b"\xff\xd8\xffJPEG")
    for _ in range(3):
        await sink.flush(force=True)
    assert len(plates) >= 1 and b"evidence_id" not in plates[0] and sink.stats["pictures_dropped"] == 1
