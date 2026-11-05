import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.helpers import iso, now, setup_cameras
from tests.video_gen import ColorDetector, ScriptedReader, make_video, walk
from worker.pipeline import CameraConfig, CameraPipeline
from worker.sink import BackendSink, MemorySink
from worker.source import VideoFileSource

LINE = ((0.1, 0.5), (0.9, 0.5), (0, 1))                     # horizontal line at mid-height; moving down is "in"


def people_scene(t):
    a, b, c = walk(200, 60, 300, 1, 5), walk(320, 300, 60, 2, 6), walk(440, 60, 300, 11, 15)
    return [a(t), b(t), c(t)], []                             # A goes in, B goes out, C goes in (crossing at ~13 s)


def run(video, pipe, start=None, fps=5):
    for frame, ts in VideoFileSource(video, fps, start).frames():
        pipe.process(frame, ts)
    pipe.finish()


def footfall_pipe(cam, sink, detector=None):
    return CameraPipeline(CameraConfig(cam, "Entrance", use_footfall=True, line=LINE), detector or ColorDetector(), None, sink)


def test_people_are_counted_in_and_out_from_a_video_file(tmp_path):
    video = make_video(tmp_path / "people.mp4", 16, people_scene)
    sink = MemorySink()
    pipe = footfall_pipe("cam", sink)
    run(video, pipe)
    assert pipe.totals == {"in": 2, "out": 1}
    assert sum(v[0] for v in sink.footfall.values()) == 2 and sum(v[1] for v in sink.footfall.values()) == 1


def test_counts_land_in_the_minute_they_happened(tmp_path):
    video = make_video(tmp_path / "people.mp4", 16, people_scene)
    sink = MemorySink()
    start = datetime(2026, 11, 4, 10, 0, 50, tzinfo=timezone.utc)           # first minute ends at 10 s into the clip
    run(video, footfall_pipe("cam", sink), start)
    by_minute = {m.strftime("%H:%M"): tuple(v) for (_, m), v in sink.footfall.items()}
    assert by_minute == {"10:00": (1, 1), "10:01": (1, 0)}                  # A in and B out before 10:01, C in after


def test_nothing_is_counted_without_a_line_or_when_footfall_is_off(tmp_path):
    video = make_video(tmp_path / "p.mp4", 16, people_scene)
    det = ColorDetector()
    sink = MemorySink()
    run(video, CameraPipeline(CameraConfig("cam", use_footfall=True, line=None), det, None, sink))
    assert sink.footfall == {} and det.calls == 0                           # no line: it does not even run the detector
    run(video, CameraPipeline(CameraConfig("cam", use_footfall=False), det, None, sink))
    assert det.calls == 0


async def test_footfall_reaches_the_backend_and_a_rerun_changes_nothing(client, make, su, tmp_path):
    t, u, (cam,), _ = await setup_cameras(client, make)
    video = make_video(tmp_path / "people.mp4", 16, people_scene)
    start = (now() - timedelta(minutes=30)).replace(second=0, microsecond=0)
    totals = []
    for _ in range(2):                                                      # the second run is a retry of the same footage
        sink = BackendSink(client, make.service()["Authorization"].split()[1])
        run(video, footfall_pipe(cam, sink), start)
        assert await sink.close() == 0 and not sink.rejected
        row = await su.fetchrow("SELECT sum(people_in)::int AS i, sum(people_out)::int AS o FROM app.footfall_counts WHERE camera_id=$1", uuid.UUID(cam))
        totals.append((row["i"], row["o"]))
    assert totals == [(2, 1), (2, 1)]
    s = (await client.get("/v1/footfall/summary", params={"from": iso(start - timedelta(hours=1)), "to": iso(now())}, headers=u["headers"])).json()
    assert (s["people_in"], s["people_out"], s["net"]) == (2, 1, 1)


# ------------------------------------------------------------------------ plates
ZONE = CameraConfig("cam", "Gate", use_anpr=True, zone=[(0, 0.2), (1, 0.2), (1, 1), (0, 1)], zone_direction=(0, 1))


def vehicle_scene(plate, down=True, t0=0.5, t1=4.5):
    path = walk(320, 100 if down else 270, 270 if down else 100, t0, t1)
    return lambda t: ([], [(path(t)[0], path(t)[1], plate)])


def gate_pipe(cam, sink, reads, snap=None, zone=ZONE):
    cfg = CameraConfig(cam, "Gate", use_anpr=True, zone=zone.zone, zone_direction=zone.zone_direction)
    return CameraPipeline(cfg, ColorDetector(), ScriptedReader(reads), sink, snapshot_dir=snap, run_id="r" + uuid.uuid4().hex[:5])


def test_noisy_reads_of_one_vehicle_become_one_voted_event(tmp_path):
    video = make_video(tmp_path / "gate.mp4", 6, vehicle_scene("GJ14AW1013"))
    sink = MemorySink()
    pipe = gate_pipe("cam", sink, ["GJ14AW1013", "1J14AW1013", "GJ14AW1O13", "GJ14AW1013", "GJ14AWI013", "GJ14AW1013"], snap=tmp_path / "snaps")
    run(video, pipe)
    assert len(sink.plates) == 1
    e = sink.plates[0]
    assert e["plate_raw"] == "GJ14AW1013" and e["direction"] == "in" and e["vehicle_type"] == "car" and e["track_id"].startswith("r") and "-" in e["track_id"]
    assert 50 <= e["confidence"] <= 95
    assert (tmp_path / "snaps" / e["snapshot_key"]).is_file() and e["snapshot_key"].startswith("cam/")
    assert pipe.stats["plate_reads"] <= 6                                    # bounded work per vehicle


def test_a_vehicle_driving_the_other_way_is_marked_out(tmp_path):
    sink = MemorySink()
    run(make_video(tmp_path / "g.mp4", 6, vehicle_scene("GJ14AW1013", down=False)), gate_pipe("cam", sink, ["GJ14AW1013"]))
    assert sink.plates[0]["direction"] == "out"


def test_a_parked_vehicle_has_no_direction_and_one_with_no_readable_plate_has_no_event(tmp_path):
    sink = MemorySink()
    parked = lambda t: ([], [(320, 200, "GJ14AW1013")])
    run(make_video(tmp_path / "park.mp4", 4, parked), gate_pipe("cam", sink, ["GJ14AW1013"]))
    assert len(sink.plates) == 1 and sink.plates[0]["direction"] is None
    sink2 = MemorySink()
    run(make_video(tmp_path / "blur.mp4", 6, vehicle_scene("")), gate_pipe("cam", sink2, [""]))
    assert sink2.plates == []


def test_vehicles_outside_the_plate_zone_are_ignored(tmp_path):
    low_zone = CameraConfig("cam", zone=[(0, 0.85), (1, 0.85), (1, 1), (0, 1)])
    sink = MemorySink()
    pipe = gate_pipe("cam", sink, ["GJ14AW1013"], zone=low_zone)
    run(make_video(tmp_path / "g.mp4", 6, vehicle_scene("GJ14AW1013")), pipe)
    assert sink.plates == [] and pipe.stats["plate_reads"] == 0


async def test_entry_and_exit_clips_are_paired_with_dwell_time_by_the_backend(client, make, su, tmp_path):
    t, u, _, (cam,) = await setup_cameras(client, make)
    token = make.service()["Authorization"].split()[1]
    for name, down, hours_ago in (("in", True, 3), ("out", False, 1)):
        video = make_video(tmp_path / f"{name}.mp4", 6, vehicle_scene("GJ14AW1013", down=down))
        sink = BackendSink(client, token)
        run(video, gate_pipe(cam, sink, ["GJ14AW1013", "GJ14AW1O13", "GJ14AW1013"]), now() - timedelta(hours=hours_ago))
        left = await sink.close()
        assert left == 0 and sink.stats["plates_sent"] == 1, (name, dict(sink.stats), dict(sink.rejected), left)
    items = (await client.get("/v1/plates", params={"from": iso(now() - timedelta(hours=5))}, headers=u["headers"])).json()["items"]
    by_dir = {i["direction"]: i for i in items}
    assert by_dir["in"]["plate_text"] == by_dir["out"]["plate_text"] == "GJ14AW1013"
    assert by_dir["out"]["entry_event_id"] == by_dir["in"]["id"]
    assert 7100 <= by_dir["out"]["dwell_seconds"] <= 7300                    # about two hours inside
    assert by_dir["in"]["review_status"] == "auto"


async def test_a_blocked_vehicle_seen_by_the_worker_raises_an_alert(client, make, su, tmp_path):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await client.post("/v1/vehicle-lists", json={"plate_text": "GJ14AW1013", "list_type": "blocked"}, headers=u["headers"])
    sink = BackendSink(client, make.service()["Authorization"].split()[1])
    run(make_video(tmp_path / "g.mp4", 6, vehicle_scene("GJ14AW1013")), gate_pipe(cam, sink, ["GJ14AW1013"]), now() - timedelta(minutes=10))
    await sink.close()
    rows = await su.fetch("SELECT title, severity FROM app.alerts WHERE tenant_id=$1", t)
    assert [(r["title"], r["severity"]) for r in rows] == [("Blocked vehicle GJ14AW1013", "critical")]


async def test_a_camera_that_may_not_send_plates_stops_the_sink(client, make, tmp_path):
    t, u, (foot,), _ = await setup_cameras(client, make)                      # a footfall camera: plates not enabled
    sink = BackendSink(client, make.service()["Authorization"].split()[1])
    run(make_video(tmp_path / "g.mp4", 6, vehicle_scene("GJ14AW1013")), gate_pipe(foot, sink, ["GJ14AW1013"]), now() - timedelta(minutes=10))
    await sink.flush(force=True)
    assert sink.fatal == "plates_not_enabled"


async def test_the_worker_fetches_its_settings_from_the_backend(client, make, tmp_path):
    t, u, (foot,), _ = await setup_cameras(client, make)
    line = {"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}
    await client.put(f"/v1/cameras/{foot}/zones", json={"zones": [line]}, headers=u["headers"])
    cfg = CameraConfig.from_backend((await client.get(f"/internal/v1/cameras/{foot}/config", headers=make.service())).json())
    assert cfg.use_footfall and cfg.line == ((0.1, 0.5), (0.9, 0.5), (0, 1))
    sink = BackendSink(client, make.service()["Authorization"].split()[1])
    pipe = CameraPipeline(cfg, ColorDetector(), None, sink)
    run(make_video(tmp_path / "people.mp4", 16, people_scene), pipe, now() - timedelta(minutes=20))
    assert await sink.close() == 0 and pipe.totals == {"in": 2, "out": 1}
