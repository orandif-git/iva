"""Live view in the Control Center: moving pictures of one camera for Owner and Admin, a time limit, a limit on how many run at once, and a record of every start."""
import asyncio

from app.config import get_settings
from app.services import live_stream, snapshot
from tests.helpers import setup_cameras


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


async def a_camera(client, make):
    t, u, cams, _ = await setup_cameras(client, make, footfall=1, anpr=0)
    return t, cams[0]


def fake_camera(monkeypatch):
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


async def test_only_owner_and_admin_can_watch_live(client, make, monkeypatch):
    t, cam = await a_camera(client, make)
    fake_camera(monkeypatch)
    monkeypatch.setattr(get_settings(), "live_stream_seconds", 1)
    for role, code in (("onboarding", 403), ("support", 403)):
        s = await make.staff(role)
        assert (await client.get(f"/admin/v1/tenants/{t}/shop/cameras/{cam}/live", headers=s["headers"])).status_code == code, role
    assert (await client.get(f"/admin/v1/tenants/{t}/shop/cameras/{cam}/live")).status_code == 401


async def test_a_live_view_streams_pictures_and_ends_by_itself_when_the_time_is_up(client, make, su, monkeypatch):
    t, cam = await a_camera(client, make)
    fake_camera(monkeypatch)
    monkeypatch.setattr(get_settings(), "live_stream_seconds", 1)
    for role in ("owner", "admin"):
        s = await make.staff(role)
        r = await asyncio.wait_for(client.get(f"/admin/v1/tenants/{t}/shop/cameras/{cam}/live", headers=s["headers"]), 15)
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream", role
        got = live_stream.pop_jpegs(bytearray(r.content))
        assert len(got) >= 3 and all(g.startswith(b"\xff\xd8") for g in got) and b"Content-Type: image/jpeg" in r.content
        assert live_stream.open_count() == 0, "its place is given back"
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='camera.live_view' AND tenant_id=$1", t) == 2, "every start is written to the activity log"


async def test_when_the_server_is_full_the_answer_is_try_again_in_a_moment(client, make, monkeypatch):
    t, cam = await a_camera(client, make)
    fake_camera(monkeypatch)
    owner = await make.staff("owner")
    monkeypatch.setattr(get_settings(), "live_stream_max", 0)
    r = await client.get(f"/admin/v1/tenants/{t}/shop/cameras/{cam}/live", headers=owner["headers"])
    assert r.status_code == 429 and r.json()["error"]["code"] == "video_busy" and live_stream.open_count() == 0


async def test_a_camera_without_a_video_source_is_an_ordinary_error_and_gives_its_place_back(client, make, monkeypatch):
    t, cam = await a_camera(client, make)
    owner = await make.staff("owner")

    async def none(row, decrypt):
        from app.errors import ApiError
        raise ApiError(409, "no_video_source", "This camera has no video source yet.")
    monkeypatch.setattr(snapshot, "source_for", none)
    r = await client.get(f"/admin/v1/tenants/{t}/shop/cameras/{cam}/live", headers=owner["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_video_source" and live_stream.open_count() == 0
