"""Camera health from the camera's own picture: moved, turned, covered, too dark, too blurry (stage 5 of the alert review)."""
import base64
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from app.services import alert_catalog as cat
from app.services import picture_checks as pc
from tests.helpers import setup_cameras
from worker.picture import SKETCH_H, SKETCH_W, measure


def sketch(seed, points=420):
    """A made-up outline sketch: `points` of the 2304 points are outline."""
    rng = random.Random(seed)
    bits = np.zeros(SKETCH_W * SKETCH_H, dtype=np.uint8)
    bits[rng.sample(range(SKETCH_W * SKETCH_H), points)] = 1
    return np.packbits(bits).tobytes()


def test_the_worker_measures_brightness_sharpness_and_an_outline_sketch_of_a_picture():
    rng = np.random.default_rng(3)
    scene = np.full((720, 1280, 3), 90, np.uint8)
    for _ in range(40):
        x, y = int(rng.integers(0, 1100)), int(rng.integers(0, 600))
        scene[y:y + 90, x:x + 140] = int(rng.integers(20, 235))                                # a busy picture with plenty of edges
    m = measure(scene)
    assert 30 < m["brightness"] < 200 and m["contrast"] > 20 and m["sharpness"] > 50
    assert len(base64.b64decode(m["edges"])) == pc.SKETCH_BYTES and pc.edge_points(base64.b64decode(m["edges"])) > 100
    import cv2
    soft = measure(cv2.GaussianBlur(scene, (0, 0), 12))
    assert soft["sharpness"] < m["sharpness"] * 0.2, "a blurred picture is far less sharp"
    flat = measure(np.full((720, 1280, 3), 128, np.uint8))
    assert flat["contrast"] < 1 and pc.edge_points(base64.b64decode(flat["edges"])) == 0
    assert measure(scene[:, :, 0])["brightness"] > 0, "a grey picture works too"
    same = measure(scene)
    assert pc.similarity(base64.b64decode(m["edges"]), base64.b64decode(same["edges"])) == 1.0


def test_two_sketches_of_the_same_view_match_and_a_different_view_does_not():
    a = sketch(1)
    assert pc.similarity(a, a) == 1.0 and pc.similarity(sketch(2), a) < 0.2 and pc.similarity(bytes(288), bytes(288)) == 1.0


async def camera(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    return t, foot, {"id": foot, "tenant_id": t, "name": await su.fetchval("SELECT name FROM app.cameras WHERE id=$1", foot)}


async def feed(conn_ctx, cam, start, minutes, **kw):
    out = []
    for i in range(0, minutes + 1, 5):
        out += await pc.apply(conn_ctx, cam, {"at": start + timedelta(minutes=i), "brightness": kw.get("brightness", 120.0), "contrast": kw.get("contrast", 40.0),
                                              "sharpness": kw.get("sharpness", 300.0), "sketch": kw.get("sketch", sketch(1))})
    return out


async def run(client, make, su, steps):
    """steps: list of (minutes, kwargs) fed one after another. Returns the alert types raised, in order."""
    from app.db import db
    await su.execute("DELETE FROM app.alert_rules WHERE rule_type IN ('view_changed','picture_poor')")
    t, foot, cam = await camera(client, make, su)
    start, got = datetime.now(timezone.utc) - timedelta(days=1), []
    async with db.service_tx() as conn:
        clock = start
        for minutes, kw in steps:
            got += await feed(conn, cam, clock, minutes, **kw)
            clock += timedelta(minutes=minutes + 5)
    return t, foot, got


async def test_a_camera_that_is_moved_is_reported_once_after_half_an_hour_and_the_new_view_becomes_usual(client, make, su):
    t, foot, got = await run(client, make, su, [(60, {}), (20, {"sketch": sketch(9)})])
    assert got == [], "twenty minutes of a different view: it could be a person in front of the lens"
    t, foot, got = await run(client, make, su, [(60, {}), (40, {"sketch": sketch(9)}), (40, {"sketch": sketch(9)})])
    assert got == ["view_changed"], "reported once, then the new view is the usual one"
    a = await su.fetchrow("SELECT title, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='view_changed'", t)
    assert a["title"].startswith("Camera position changed: ") and "no longer looks at what it used to" in a["body"]


async def test_a_covered_camera_is_reported_after_a_quarter_of_an_hour(client, make, su):
    t, foot, got = await run(client, make, su, [(60, {}), (10, {"contrast": 2.0, "sketch": bytes(288)})])
    assert got == [], "ten minutes: a hand passing in front"
    t, foot, got = await run(client, make, su, [(60, {}), (30, {"contrast": 2.0, "sketch": bytes(288)})])
    assert got == ["view_changed"]
    assert (await su.fetchrow("SELECT title FROM app.alerts WHERE tenant_id=$1 AND rule_type='view_changed'", t))["title"].startswith("Camera covered: ")


async def test_a_change_of_light_is_not_a_change_of_view(client, make, su):
    t, foot, got = await run(client, make, su, [(60, {}), (120, {"brightness": 25.0, "sketch": sketch(7)}), (60, {"brightness": 130.0})])
    assert got == [], "the night picture is compared with the night view, not the day one, so switching to infrared raises nothing"


async def test_a_picture_that_stays_much_blurrier_than_usual_is_reported_after_two_hours(client, make, su):
    t, foot, got = await run(client, make, su, [(300, {}), (90, {"sharpness": 40.0})])
    assert got == [], "an hour and a half of blur: a passing cloud or steam"
    t, foot, got = await run(client, make, su, [(300, {}), (150, {"sharpness": 40.0})])
    assert got == ["picture_poor"]
    a = await su.fetchrow("SELECT title, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='picture_poor'", t)
    assert a["title"].startswith("Poor image quality: ") and "much less sharp than it usually is" in a["body"]
    t, foot, got = await run(client, make, su, [(60, {"sharpness": 40.0}), (200, {"sharpness": 40.0})])
    assert got == [], "a camera with no history has no usual sharpness to fall short of"


async def test_a_dark_picture_is_only_a_problem_while_the_place_is_open(client, make, su):
    await su.execute("DELETE FROM app.alert_rules WHERE rule_type IN ('view_changed','picture_poor')")
    t, foot, cam = await camera(client, make, su)
    from app.db import db
    tz = await su.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", t)
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    async with db.service_tx() as conn:
        got = await feed(conn, cam, start, 120, brightness=5.0, sketch=sketch(4))
        assert got == [], "no opening hours set: night and a fault cannot be told apart"
    await su.execute("DELETE FROM app.camera_picture_state WHERE camera_id=$1", foot)
    local = datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    await su.execute("UPDATE app.tenants SET open_time=$2, close_time=$3, closed_days='{}' WHERE id=$1", t, (local - timedelta(hours=12)).time(), (local + timedelta(hours=11)).time())
    async with db.service_tx() as conn:
        got = await feed(conn, cam, start, 120, brightness=5.0, sketch=sketch(4))
    assert got == ["picture_poor"]
    assert "almost black while you are open" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='picture_poor'", t)
    await su.execute("UPDATE app.tenants SET open_time=NULL, close_time=NULL WHERE id=$1", t)


async def test_the_worker_reports_reach_the_server_and_bad_ones_are_refused(client, make, su):
    t, foot, cam = await camera(client, make, su)
    good = {"camera_id": str(foot), "at": datetime.now(timezone.utc).isoformat(), "brightness": 100, "contrast": 30, "sharpness": 200, "edges": base64.b64encode(sketch(1)).decode()}
    r = await client.post("/internal/v1/events/pictures", json={"events": [good, {**good, "edges": "AAAA"}, {**good, "camera_id": "00000000-0000-0000-0000-000000000000"}]}, headers=make.service())
    assert r.status_code == 200 and r.json()["accepted"] == 1
    assert sorted(x["reason"] for x in r.json()["rejected"]) == ["bad_picture", "unknown_camera"]
    assert (await su.fetchval("SELECT last_brightness FROM app.camera_picture_state WHERE camera_id=$1", foot)) == 100
    assert (await client.post("/internal/v1/events/pictures", json={"events": [{**good, "brightness": 999}]}, headers=make.service())).status_code == 422
    assert (await client.post("/internal/v1/events/pictures", json={"events": [good]})).status_code in (401, 403)


def test_both_picture_alerts_are_built_and_listed_with_what_they_need():
    assert cat.CATALOG["view_changed"]["built"] and cat.CATALOG["picture_poor"]["built"] and "what" in cat.shortcodes_for("view_changed")


async def test_a_worker_measures_the_picture_every_five_minutes_and_the_server_keeps_it(client, make, su):
    from worker.pipeline import CameraConfig, CameraPipeline
    from worker.sink import BackendSink, MemorySink
    t, foot, cam = await camera(client, make, su)
    scene = np.random.default_rng(5).integers(0, 255, (360, 640, 3), dtype=np.uint8)

    class NoDetections:
        last_ms = 0.0
        def detect(self, frame):
            return []

    mem = MemorySink()
    quiet = CameraPipeline(CameraConfig(str(foot), "Door"), NoDetections(), None, mem)
    start = datetime.now(timezone.utc) - timedelta(minutes=20)
    for i in range(0, 20 * 60, 5):                                       # twenty minutes of frames, one every five seconds
        quiet.process(scene, start + timedelta(seconds=i))
    assert len(mem.pictures) == 4 and {"at", "brightness", "contrast", "sharpness", "edges"} <= set(mem.pictures[0]), "one measurement every five minutes"
    sink = BackendSink(client, make.service()["Authorization"].split()[1])
    live = CameraPipeline(CameraConfig(str(foot), "Door"), NoDetections(), None, sink)
    live.process(scene, datetime.now(timezone.utc) - timedelta(seconds=30))
    live.process(scene, datetime.now(timezone.utc) - timedelta(seconds=20))
    assert len(sink.pictures) == 1, "only the newest measurement waits to be sent"
    assert await sink.close() == 0 and not sink.rejected
    assert await su.fetchval("SELECT last_sharpness FROM app.camera_picture_state WHERE camera_id=$1", foot) > 0
