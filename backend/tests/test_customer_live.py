"""Live view and AI view for customers: the clocks, the limits, who may watch, and what is recorded."""
import asyncio

import pytest

from app.services import snapshot
from tests.helpers import setup_cameras

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 50


async def shop(client, make, cams=3):
    t, u, foot, anpr = await setup_cameras(client, make, footfall=cams, anpr=0)
    return t, u, foot


async def open_view(client, u, cam, mode="live"):
    r = await client.post(f"/v1/cameras/{cam}/live", json={"mode": mode}, headers=u["headers"])
    assert r.status_code == 201, r.text
    return r.json()


async def test_live_view_lasts_two_minutes_and_ai_view_thirty_seconds(client, make, su):
    t, u, cams = await shop(client, make)
    live = await open_view(client, u, cams[0], "live")
    assert 115 <= live["seconds_left"] <= 120 and live["cap_seconds"] == 600 and live["period_seconds"] == 120 and live["can_extend"]
    ai = await open_view(client, u, cams[1], "ai")
    assert 25 <= ai["seconds_left"] <= 30 and ai["cap_seconds"] == 120 and ai["period_seconds"] == 30
    assert await su.fetchval("SELECT ai_view_until > now() FROM app.cameras WHERE id=$1", cams[1])        # the worker is asked to draw the boxes


async def test_a_view_closes_by_itself_when_its_time_is_up(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    await su.execute("UPDATE app.live_sessions SET expires_at = now() - interval '1 second' WHERE id=$1", s["id"])
    got = (await client.get(f"/v1/live/{s['id']}", headers=u["headers"])).json()
    assert got["state"] == "ended" and got["seconds_left"] == 0 and got["ended_why"] == "timed_out"
    assert (await client.get(f"/v1/live/{s['id']}/frame", headers=u["headers"])).status_code == 410
    assert (await client.post(f"/v1/live/{s['id']}/extend", headers=u["headers"])).status_code == 410


async def test_keep_watching_adds_the_same_time_until_the_limit(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0], "ai")                       # 30 s each, up to 120 s
    for _ in range(3):
        r = await client.post(f"/v1/live/{s['id']}/extend", headers=u["headers"])
        assert r.status_code == 200, r.text
    assert 115 <= r.json()["seconds_left"] <= 120 and r.json()["can_extend"] is False and r.json()["extensions"] == 3
    r = await client.post(f"/v1/live/{s['id']}/extend", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "time_limit_reached"


async def test_live_view_can_be_extended_up_to_ten_minutes(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0], "live")                     # 120 s each, up to 600 s
    for _ in range(4):
        assert (await client.post(f"/v1/live/{s['id']}/extend", headers=u["headers"])).status_code == 200
    assert (await client.post(f"/v1/live/{s['id']}/extend", headers=u["headers"])).status_code == 409


async def test_stopping_ends_the_view_at_once(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    r = await client.delete(f"/v1/live/{s['id']}", headers=u["headers"])
    assert r.status_code == 200 and r.json()["state"] == "ended" and r.json()["ended_why"] == "stopped"
    assert (await client.get(f"/v1/live/{s['id']}/frame", headers=u["headers"])).status_code == 410


async def test_a_person_watches_one_camera_at_a_time(client, make, su):
    t, u, cams = await shop(client, make)
    first = await open_view(client, u, cams[0])
    second = await open_view(client, u, cams[1])
    assert (await client.get(f"/v1/live/{first['id']}", headers=u["headers"])).json()["ended_why"] == "replaced"
    assert second["state"] == "open"


async def test_a_customer_can_have_only_two_cameras_open_at_once(client, make, su):
    t, u, cams = await shop(client, make)
    u2, u3 = await make.user(t), await make.user(t)
    await open_view(client, u, cams[0])
    await open_view(client, u2, cams[1])
    r = await client.post(f"/v1/cameras/{cams[2]}/live", json={"mode": "live"}, headers=u3["headers"])
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_open"


async def test_only_owners_and_managers_can_watch(client, make, su):
    t, u, cams = await shop(client, make)
    staff = await make.user(t, role="regular")
    assert (await client.post(f"/v1/cameras/{cams[0]}/live", json={"mode": "live"}, headers=staff["headers"])).status_code == 403
    s = await open_view(client, u, cams[0])
    assert (await client.get(f"/v1/live/{s['id']}", headers=staff["headers"])).status_code == 403


async def test_only_the_person_who_opened_a_view_can_drive_it_and_other_customers_cannot_see_it(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    mate = await make.user(t)
    for call in (client.post(f"/v1/live/{s['id']}/extend", headers=mate["headers"]), client.delete(f"/v1/live/{s['id']}", headers=mate["headers"])):
        assert (await call).status_code == 403
    t2, u2, cams2 = await shop(client, make)
    assert (await client.get(f"/v1/live/{s['id']}", headers=u2["headers"])).status_code == 404
    assert (await client.post(f"/v1/cameras/{cams[0]}/live", json={"mode": "live"}, headers=u2["headers"])).status_code == 404


async def test_a_paused_camera_or_an_offline_recorder_cannot_be_watched(client, make, su):
    t, u, cams = await shop(client, make)
    await su.execute("UPDATE app.cameras SET enabled=false, use_footfall=false WHERE id=$1", cams[0])
    r = await client.post(f"/v1/cameras/{cams[0]}/live", json={"mode": "live"}, headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_paused"
    await su.execute("UPDATE app.recorders SET status='offline' WHERE tenant_id=$1", t)
    r = await client.post(f"/v1/cameras/{cams[1]}/live", json={"mode": "live"}, headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_offline"


async def test_live_pictures_come_from_the_camera_and_ai_pictures_from_the_worker(client, make, su, monkeypatch):
    t, u, cams = await shop(client, make)
    calls = []

    async def fake_get(camera_id, row, decrypt, now=None, max_age=None):
        calls.append(max_age)
        return JPEG
    monkeypatch.setattr(snapshot, "get", fake_get)
    live = await open_view(client, u, cams[0], "live")
    r = await client.get(f"/v1/live/{live['id']}/frame", headers=u["headers"])
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg" and calls and calls[0] < 5
    ai = await open_view(client, u, cams[1], "ai")
    assert (await client.get(f"/v1/live/{ai['id']}/frame", headers=u["headers"])).status_code == 404            # the worker has not sent one yet
    await su.execute("INSERT INTO app.ai_view_frames (camera_id, tenant_id, jpeg) VALUES ($1,$2,$3)", cams[1], t, JPEG)
    r = await client.get(f"/v1/live/{ai['id']}/frame", headers=u["headers"])
    assert r.status_code == 200 and r.content == JPEG


async def test_every_view_is_in_the_history_and_the_audit_log(client, make, su):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0], "ai")
    await client.delete(f"/v1/live/{s['id']}", headers=u["headers"])
    hist = (await client.get("/v1/live-history", headers=u["headers"])).json()
    assert len(hist) == 1 and hist[0]["mode"] == "ai" and hist[0]["ended_why"] == "stopped" and hist[0]["user_name"] and hist[0]["camera_name"]
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE tenant_id=$1 AND action='camera.live_view'", t) == 1
    other = await make.user(t, role="regular")
    assert (await client.get("/v1/live-history", headers=other["headers"])).status_code == 403


# ------------------------------------------------------------------------------------------ moving pictures
from app.services import live_stream  # noqa: E402


def test_the_splitter_cuts_complete_pictures_and_keeps_the_rest():
    a, b = b"\xff\xd8AAA\xff\xd9", b"\xff\xd8BBB\xff\xd9"
    buf = bytearray(b"junk" + a + b + b"\xff\xd8CC")
    assert live_stream.pop_jpegs(buf) == [a, b] and bytes(buf) == b"\xff\xd8CC"
    buf += b"C\xff\xd9"
    assert live_stream.pop_jpegs(buf) == [b"\xff\xd8CCC\xff\xd9"] and not buf


async def test_real_ffmpeg_gives_a_stream_of_pictures():
    """Not a stand-in: FFmpeg makes a test video and the same reader that serves a camera turns it into JPEGs."""
    n = 0
    async for jpeg in live_stream.ffmpeg_frames("testsrc=size=320x180:rate=12", "lavfi"):
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9") and len(jpeg) > 500
        n += 1
        if n == 3:
            break
    assert n == 3


async def test_a_live_view_streams_pictures_until_it_ends(client, make, su, monkeypatch):
    t, u, cams = await shop(client, make)

    async def fake_source(row, decrypt):
        return "test://camera", None

    async def fake_frames(url, fmt):
        assert url == "test://camera"
        i = 0
        while True:                                  # a camera never stops by itself
            yield b"\xff\xd8" + bytes([i % 200 + 1]) * 40 + b"\xff\xd9"
            i += 1
            await asyncio.sleep(0.2)
    monkeypatch.setattr(snapshot, "source_for", fake_source)
    monkeypatch.setattr(live_stream, "ffmpeg_frames", fake_frames)
    s = await open_view(client, u, cams[0], "live")
    # The test client hands over a response only when it is complete, so the stream runs as a task and the view is closed from the other side.
    task = asyncio.create_task(client.get(f"/v1/live/{s['id']}/stream", headers=u["headers"]))
    await asyncio.sleep(2.2)
    assert not task.done(), "a live view keeps going while it is open"
    await client.delete(f"/v1/live/{s['id']}", headers=u["headers"])
    r = await asyncio.wait_for(task, 15)                    # ...and ends by itself once the view is closed
    assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
    got = live_stream.pop_jpegs(bytearray(r.content))
    assert len(got) >= 5 and all(g.startswith(b"\xff\xd8") for g in got)
    assert b"Content-Type: image/jpeg" in r.content
    assert live_stream.open_count() == 0, "its place is given back"


async def test_ai_view_has_no_stream_and_others_cannot_watch_yours(client, make, su, monkeypatch):
    t, u, cams = await shop(client, make)
    ai = await open_view(client, u, cams[1], "ai")
    r = await client.get(f"/v1/live/{ai['id']}/stream", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "use_frames"
    live = await open_view(client, u, cams[0], "live")
    other = await make.user(t, role="admin")
    assert (await client.get(f"/v1/live/{live['id']}/stream", headers=other["headers"])).status_code == 403
    assert live_stream.open_count() == 0


async def test_a_busy_server_says_so_and_a_failed_camera_gives_the_place_back(client, make, su, monkeypatch):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0], "live")

    async def broken(row, decrypt):
        from app.errors import ApiError
        raise ApiError(409, "camera_offline", "The recorder is not sending video for this camera right now. Check that it is online.")
    monkeypatch.setattr(snapshot, "source_for", broken)
    r = await client.get(f"/v1/live/{s['id']}/stream", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_offline" and live_stream.open_count() == 0
    held = [live_stream.slot() for _ in range(8)]
    r = await client.get(f"/v1/live/{s['id']}/stream", headers=u["headers"])
    assert r.status_code == 429 and r.json()["error"]["code"] == "video_busy"
    for h in held:
        h.release()
    assert live_stream.open_count() == 0
