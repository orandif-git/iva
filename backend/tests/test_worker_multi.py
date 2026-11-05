"""Many cameras in one worker process: a shared sender that keeps cameras apart, threaded analysis, the supervisor, and the assignment endpoint."""
import asyncio
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from tests.helpers import now as utcnow, setup_cameras
from tests.test_worker_runner import Clock, FakeReader, Feed, LINE, LiveSettings, two_crossings  # noqa: F401  (the fixture builds the test video)
from tests.video_gen import ColorDetector
from worker.backend import BackendUnavailable, FleetBackend, WorkerBackend
from worker.clips import FrameRing
from worker.multi import Fleet
from worker.pipeline import CameraConfig, CameraPipeline
from worker.runner import run_live
from worker.sink import BackendSink


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Server:
    """A stand-in backend that records what it was sent and answers as scripted."""

    def __init__(self, refuse=()):
        self.footfall, self.plates, self.refuse, self.calls = [], [], set(refuse), 0
        self.clips = {}
        self.during = None                        # called once while the first request is in flight

    async def handler(self, req: httpx.Request):
        import json
        await asyncio.sleep(0.01)                 # a real network round trip lets other tasks run, which is when overlapping flushes would collide
        self.calls += 1
        if self.during:
            self.during(); self.during = None
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else {}
        events = body.get("events", [])
        rejected = [{"index": i, "reason": "unknown_camera"} for i, e in enumerate(events) if e["camera_id"] in self.refuse]
        if req.url.path.endswith("/footfall"):
            self.footfall += [e for e in events if e["camera_id"] not in self.refuse]
        else:
            self.plates += [e for e in events if e["camera_id"] not in self.refuse]
        clips = [{"index": i, "alert_id": f"al-{e['camera_id']}", "seconds_before": 5, "seconds_after": 4} for i, e in enumerate(events) if e.get("plate_raw") == "WANTCLIP" and e["camera_id"] not in self.refuse]
        return httpx.Response(200, json={"accepted": len(events) - len(rejected), "duplicates": 0, "rejected": rejected, "clips": clips})


def sink_for(server: Server) -> BackendSink:
    return BackendSink(httpx.AsyncClient(transport=httpx.MockTransport(server.handler), base_url="http://b"), "t" * 40, clock=lambda: 1e9)


T = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------- the shared sender
async def test_a_camera_the_backend_refuses_stops_alone_and_its_waiting_data_is_dropped():
    srv = Server(refuse={"bad"})
    sink = sink_for(srv)
    good, bad = sink.view("good"), sink.view("bad")
    for v, n in ((good, 3), (bad, 3)):
        for i in range(n):
            v.set_footfall(v.camera_id, T + timedelta(minutes=i), 1, 0)
            v.add_plate(v.camera_id, {"seen_at": iso(T), "track_id": f"{v.camera_id}{i}", "plate_raw": "GJ14AW1013"})
    assert await good.flush(force=True)
    assert bad.fatal == "unknown_camera" and good.fatal is None and sink.fatal is None            # only the refused camera is told to stop
    assert {e["camera_id"] for e in srv.footfall + srv.plates} == {"good"} and len(srv.footfall) == 3 and len(srv.plates) == 3
    bad.add_plate("bad", {"seen_at": iso(T), "track_id": "late", "plate_raw": "X"})                # later reads from it are ignored, not queued
    bad.set_footfall("bad", T, 1, 1)
    assert sink.pending == 0
    good.add_plate("good", {"seen_at": iso(T), "track_id": "more", "plate_raw": "X"})
    assert await good.flush(force=True) and len(srv.plates) == 4                                  # the others carry on


async def test_a_single_camera_sink_behaves_as_before():
    srv = Server(refuse={"only"})
    sink = sink_for(srv)
    sink.add_plate("only", {"seen_at": iso(T), "track_id": "a", "plate_raw": "X"})
    await sink.flush(force=True)
    assert sink.fatal == "unknown_camera"                                                          # not shared: the whole sink stops, as the single-camera runner expects


async def test_two_flushes_at_once_never_send_anything_twice():
    srv = Server()
    sink = sink_for(srv)
    views = [sink.view(f"c{i}") for i in range(4)]
    for v in views:
        for i in range(30):
            v.add_plate(v.camera_id, {"seen_at": iso(T), "track_id": f"{v.camera_id}-{i}", "plate_raw": "X"})
            v.set_footfall(v.camera_id, T + timedelta(minutes=i), 1, 0)
    await asyncio.gather(*[v.flush(force=True) for v in views for _ in range(3)])
    assert Counter(e["track_id"] for e in srv.plates).most_common(1)[0][1] == 1 and len(srv.plates) == 120
    assert len({(e["camera_id"], e["bucket_start"]) for e in srv.footfall}) == len(srv.footfall) == 120


async def test_analysis_threads_can_queue_while_the_sender_is_sending():
    srv = Server()
    sink = sink_for(srv)
    stop, errors = threading.Event(), []

    def producer(cam):
        try:
            for i in range(400):
                sink.add_plate(cam, {"seen_at": iso(T), "track_id": f"{cam}-{i}", "plate_raw": "X"})
                sink.set_footfall(cam, T + timedelta(minutes=i), 1, 0)
        except Exception as e:                                                                       # a race would show up as an exception here
            errors.append(e)
    threads = [threading.Thread(target=producer, args=(f"c{i}",)) for i in range(6)]
    [t.start() for t in threads]
    while any(t.is_alive() for t in threads):
        await sink.flush(force=True)
        await asyncio.sleep(0)
    await sink.close()
    assert not errors and len(srv.plates) == 2400 and len({e["track_id"] for e in srv.plates}) == 2400 and len(srv.footfall) == 2400


async def test_clip_requests_go_to_the_camera_they_belong_to():
    srv = Server()
    sink = sink_for(srv)
    got = {"a": [], "b": []}
    a, b = sink.view("a"), sink.view("b")
    a.clip_handler = lambda *x: got["a"].append(x)
    b.clip_handler = lambda *x: got["b"].append(x)
    a.add_plate("a", {"seen_at": iso(T), "track_id": "1", "plate_raw": "WANTCLIP"})
    b.add_plate("b", {"seen_at": iso(T), "track_id": "2", "plate_raw": "WANTCLIP"})
    b.add_plate("b", {"seen_at": iso(T), "track_id": "3", "plate_raw": "NORMAL"})
    await a.flush(force=True)
    assert got["a"] == [("a", iso(T), "al-a", 5, 4)] and got["b"] == [("b", iso(T), "al-b", 5, 4)]


def test_the_picture_buffer_is_safe_to_use_from_threads():
    ring, errors = FrameRing(keep_seconds=5), []

    def writer(base):
        try:
            for i in range(2000):
                ring.add(base + i * 0.01, b"x")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(2000):
                ring.window(0, 1e12); ring.newest
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=writer, args=(1000.0,)), threading.Thread(target=reader), threading.Thread(target=reader)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors


# ---------------------------------------------------------------------------------- the supervisor
class Script:
    def __init__(self):
        self.wanted, self.started, self.events, self.ended, self.fail = [], [], {}, [], False
        self.behaviour = {}                                       # camera id -> "run" (until told to stop) | "exit" (ends at once) | "boom" (raises)

    async def discover(self):
        if self.fail:
            raise BackendUnavailable("down")
        return list(self.wanted)

    async def start(self, cid, ev):
        self.started.append(cid)
        self.events[cid] = ev
        how = self.behaviour.get(cid, "run")
        if how == "boom":
            raise RuntimeError("camera exploded")
        if how == "exit":
            return {"exit": "no_source"}
        await ev.wait()
        self.ended.append(cid)
        return {"exit": "stopped"}


async def tick(n=3):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_the_fleet_starts_new_cameras_and_stops_ones_no_longer_assigned():
    s = Script(); fleet = Fleet(s.discover, s.start)
    s.wanted = ["a", "b"]
    await fleet.step(); await tick()
    assert sorted(s.started) == ["a", "b"] and sorted(fleet.running) == ["a", "b"]
    s.wanted = ["b", "c"]
    await fleet.step(); await tick()
    assert s.ended == ["a"] and "c" in s.started and sorted(fleet.running) == ["a", "b", "c"]     # a is told to stop; reaped at the next round
    await fleet.step(); await tick()
    assert sorted(fleet.running) == ["b", "c"] and s.started.count("b") == 1                      # b was never restarted
    await fleet.shutdown()
    assert fleet.running == {} and sorted(s.ended) == ["a", "b", "c"]


async def test_a_camera_that_ends_by_itself_is_started_again_at_the_next_round_and_one_that_fails_harms_no_other():
    s = Script(); fleet = Fleet(s.discover, s.start)
    s.wanted = ["ok", "short", "boom"]
    s.behaviour = {"short": "exit", "boom": "boom"}
    await fleet.step(); await tick(5)
    assert sorted(s.started) == ["boom", "ok", "short"]
    await fleet.step(); await tick(5)
    assert s.started.count("short") == 2 and s.started.count("boom") == 2 and s.started.count("ok") == 1      # the two that ended come back; ok keeps running
    assert ("short", "no_source") in fleet.history and ("boom", "error") in fleet.history
    assert "ok" in fleet.running and not fleet.running["ok"][1].is_set()
    await fleet.shutdown()


async def test_if_the_backend_cannot_be_asked_the_current_cameras_keep_running():
    s = Script(); fleet = Fleet(s.discover, s.start)
    s.wanted = ["a"]
    await fleet.step(); await tick()
    s.fail = True
    await fleet.step(); await tick()
    assert "a" in fleet.running and not fleet.running["a"][1].is_set()
    s.fail = False; s.wanted = []
    await fleet.step(); await tick()
    assert fleet.running["a"][1].is_set()
    await fleet.shutdown()


async def test_shutdown_stops_everything_even_a_camera_that_ignores_the_stop():
    async def stubborn(cid, ev):
        await asyncio.sleep(3600)
    fleet = Fleet(lambda: asyncio.sleep(0, result=["x", "y"]), stubborn, stop_timeout=0.2)
    await fleet.step(); await tick()
    stop = asyncio.Event(); stop.set()
    t0 = time.monotonic()
    await fleet.run(stop)
    assert time.monotonic() - t0 < 2 and fleet.running == {}


async def test_the_fleet_runs_until_told_to_stop_and_polls_on_its_own():
    s = Script(); fleet = Fleet(s.discover, s.start, poll=0.05)
    stop = asyncio.Event()
    s.wanted = ["a"]
    task = asyncio.create_task(fleet.run(stop))
    await asyncio.sleep(0.02)
    s.wanted = ["a", "b"]
    await asyncio.sleep(0.2)
    assert sorted(s.started) == ["a", "b"]
    stop.set()
    await asyncio.wait_for(task, 2)
    assert sorted(s.ended) == ["a", "b"]


# ---------------------------------------------------------------------------------- the real thing
async def test_three_cameras_in_one_process_each_get_their_own_counts_with_analysis_in_threads(client, make, su, two_crossings):
    tech = await make.staff("technician")
    cams = []
    for _ in range(3):
        t, u, (cam,), _x = await setup_cameras(client, make)
        await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://v:p@10.20.0.14/x", "reason": "Site VPN is up"}, headers=tech["headers"])
        await client.put(f"/v1/cameras/{cam}/zones", json={"zones": [{"kind": "count_line", "points": LINE, "in_direction": [0, 1]}]}, headers=u["headers"])
        cams.append(cam)
    token = make.service()["Authorization"].split()[1]
    sink = BackendSink(client, token)
    cpu = asyncio.Semaphore(2)
    in_flight, peak, threads, lk = [0], [0], set(), threading.Lock()
    start = utcnow() - timedelta(minutes=5)

    async def one(cam):
        clock, stop = Clock(), asyncio.Event()
        feed = Feed(two_crossings, start, clock, stop)
        view = sink.view(cam)

        def pipeline_factory(cfg):
            p = CameraPipeline(cfg, ColorDetector(), None, view, run_id="r" + uuid.uuid4().hex[:5])
            orig = p.process

            def process(frame, ts):
                with lk:
                    in_flight[0] += 1; peak[0] = max(peak[0], in_flight[0]); threads.add(threading.get_ident())
                time.sleep(0.002)
                try:
                    return orig(frame, ts)
                finally:
                    with lk:
                        in_flight[0] -= 1
            p.process = process
            return p
        return await run_live(backend=WorkerBackend(client, token, cam), sink=view, reader_factory=lambda url, w, h, fps: FakeReader(feed, fps), pipeline_factory=pipeline_factory,
                              settings=LiveSettings(config_refresh=5, health_interval=5, flush_interval=1, retry_delay=0.01), stop=stop, clock=clock, cpu=cpu)

    results = await asyncio.gather(*[one(c) for c in cams])
    assert all(r["exit"] == "stopped" for r in results)
    for c in cams:
        row = await su.fetchrow("SELECT sum(people_in)::int AS i, sum(people_out)::int AS o FROM app.footfall_counts WHERE camera_id=$1", uuid.UUID(c))
        assert (row["i"], row["o"]) == (1, 1), c                                                       # every camera counted its own two crossings, and nobody else's
    assert threading.get_ident() not in threads and peak[0] <= 2                                       # analysed off the event loop, never more than the limit at once
    assert sink.pending == 0 and not sink.fatal_cameras


# ---------------------------------------------------------------------------------- which cameras a worker runs
async def test_each_camera_belongs_to_exactly_one_worker_and_only_runnable_cameras_are_listed(client, make, su):
    tech = await make.staff("technician")
    t, u, foot, anpr = await setup_cameras(client, make, footfall=3, anpr=3)
    ids = foot + anpr
    for cam in ids[:2] + ids[3:5]:                                                                     # these four get an address: runnable
        await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://v:p@10.20.0.14/x", "reason": "Site VPN is up"}, headers=tech["headers"])
    runnable = set(ids[:2] + ids[3:5])
    # the other two have no address (and their recorder is not a registered GB28181 one), so they are not listed
    s = make.service()
    async def listed(shard, of):
        r = await client.get("/internal/v1/workers/cameras", params={"shard": shard, "of": of}, headers=s)
        assert r.status_code == 200, r.text
        return set(r.json()["cameras"])
    whole = await listed(0, 1)
    assert runnable <= whole and not ({ids[2], ids[5]} & whole)
    parts = [await listed(i, 3) for i in range(3)]
    assert set().union(*parts) == whole and sum(len(p) for p in parts) == len(whole)                   # every camera on exactly one worker
    assert await listed(1, 3) == parts[1]                                                              # and the split is stable
    # a gb28181 recorder needs no address
    await su.execute("UPDATE app.recorders SET protocol='gb28181' WHERE tenant_id=$1", t)
    assert {ids[2], ids[5]} <= await listed(0, 1)
    # off, nothing to analyse, suspended
    await su.execute("UPDATE app.cameras SET enabled=false WHERE id=$1", uuid.UUID(ids[0]))
    await su.execute("UPDATE app.cameras SET enabled=false, use_footfall=false, use_anpr=false WHERE id=$1", uuid.UUID(ids[1]))
    now_listed = await listed(0, 1)
    assert not ({ids[0], ids[1]} & now_listed) and {ids[2], ids[3]} <= now_listed
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert not (set(ids) & await listed(0, 1))
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_the_assignment_endpoint_checks_its_input_and_its_caller(client, make):
    s = make.service()
    assert (await client.get("/internal/v1/workers/cameras", params={"shard": 3, "of": 3}, headers=s)).status_code == 422
    assert (await client.get("/internal/v1/workers/cameras", params={"shard": -1, "of": 3}, headers=s)).status_code == 422
    assert (await client.get("/internal/v1/workers/cameras", params={"of": 0}, headers=s)).status_code == 422
    assert (await client.get("/internal/v1/workers/cameras")).status_code == 401
    u = await make.user(await make.tenant())
    assert (await client.get("/internal/v1/workers/cameras", headers=u["headers"])).status_code == 401      # a customer is not a worker


async def test_the_worker_asks_the_backend_through_its_client(client, make):
    fb = FleetBackend(client, make.service()["Authorization"].split()[1])
    assert isinstance(await fb.cameras(0, 1), list)
    with pytest.raises(BackendUnavailable):
        await FleetBackend(client, "x" * 40).cameras(0, 1)                                              # a wrong token is "unavailable", not a crash
    with pytest.raises(BackendUnavailable):
        await FleetBackend(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))), base_url="http://x"), "t" * 40).cameras(0, 1)


async def test_plates_queued_while_a_batch_is_in_flight_are_not_lost_even_when_the_buffer_overflows():
    srv = Server()
    sink = BackendSink(httpx.AsyncClient(transport=httpx.MockTransport(srv.handler), base_url="http://b"), "t" * 40, max_buffer=5, clock=lambda: 1e9)
    for i in range(5):
        sink.add_plate("c", {"seen_at": iso(T), "track_id": f"p{i}", "plate_raw": "X"})
    srv.during = lambda: [sink.add_plate("c", {"seen_at": iso(T), "track_id": f"p{i}", "plate_raw": "X"}) for i in range(5, 8)]      # overflow drops p0 to p2 mid-flight
    await sink.flush(force=True)
    assert Counter(e["track_id"] for e in srv.plates) == Counter({"p0": 1, "p1": 1, "p2": 1, "p3": 1, "p4": 1, "p5": 1, "p6": 1, "p7": 1}) and sink.pending == 0   # every plate reached the backend exactly once
