"""Smooth live video: the SDP exchange with go2rtc, what the player is told when it is not available, and the camera source line."""
import uuid

import httpx
import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services import go2rtc, snapshot
from tests.test_customer_live import open_view, shop

OFFER = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
ANSWER = "v=0\r\no=- 2 2 IN IP4 127.0.0.1\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"


def test_the_name_of_a_stream_belongs_to_one_camera():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert go2rtc.stream_name(a) == go2rtc.stream_name(str(a)) == f"iv_{a.hex}"
    assert go2rtc.stream_name(a) != go2rtc.stream_name(b), "two cameras never share a stream"


def test_the_source_line_turns_the_camera_into_h264_webrtc_can_carry():
    line = go2rtc.source_command("http://relay.test/play/abc?token=one", "mpeg")
    assert line.startswith("exec:") and "libx264" in line and "-profile:v baseline" in line and "-tune zerolatency" in line
    assert "'-f' 'mpeg'" in line or " -f mpeg " in line, "the relay's MPEG-PS is demuxed"
    assert "mpegts" in line and "pipe:1" in line
    assert "relay.test/play/abc" in line
    rtsp = go2rtc.source_command("rtsp://camera.local/live", None)
    assert "-rtsp_transport tcp" in rtsp and "mpeg" not in rtsp.split("-i")[1].split("-an")[0], "an RTSP camera is read over TCP"


class _FakeGo2rtc:
    def __init__(self):
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def put(self, path, params):
        self.calls.append(("PUT", path, params))
        return httpx.Response(200)

    async def post(self, path, params, content, headers):
        self.calls.append(("POST", path, params, content, headers))
        return httpx.Response(200, text=ANSWER)


async def test_an_offer_registers_the_camera_and_returns_the_answer(monkeypatch):
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://go2rtc.test")
    fake = _FakeGo2rtc()

    async def client():
        return fake

    async def source(row, decrypt):
        return "http://relay.test/play/abc?token=one", "mpeg"

    monkeypatch.setattr(go2rtc, "_client", client)
    monkeypatch.setattr(snapshot, "source_for", source)
    cam = uuid.uuid4()
    answer = await go2rtc.whep(cam, {"source_ciphertext": None}, lambda: "unused", OFFER)
    assert answer == ANSWER
    (put, post) = fake.calls
    assert put[0] == "PUT" and put[1] == "/api/streams" and put[2]["name"] == go2rtc.stream_name(cam)
    assert "mpegts" in put[2]["src"], "the camera is registered with the FFmpeg source that produces WebRTC video"
    assert post[0] == "POST" and post[1] == "/api/webrtc" and post[2] == {"src": go2rtc.stream_name(cam)}
    assert post[3] == OFFER.encode() and post[4]["Content-Type"] == "application/sdp"


async def test_an_unreachable_go2rtc_is_a_plain_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://go2rtc.test")

    async def client():
        raise AssertionError("not reached")

    async def source(row, decrypt):
        return "rtsp://camera.local/live", None

    monkeypatch.setattr(snapshot, "source_for", source)

    class Dead(_FakeGo2rtc):
        async def put(self, path, params):
            raise httpx.ConnectError("down")

    async def dead_client():
        return Dead()

    monkeypatch.setattr(go2rtc, "_client", dead_client)
    with pytest.raises(ApiError) as e:
        await go2rtc.whep(uuid.uuid4(), {"source_ciphertext": None}, lambda: "x", OFFER)
    assert e.value.status == 503 and e.value.code == "smooth_video_unavailable"


# ------------------------------------------------------------------------------------------ through the customer's API
async def test_the_player_gets_an_answer_from_the_smooth_video_route(client, make, monkeypatch):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    seen = {}

    async def fake(camera_id, row, decrypt, offer):
        seen.update(camera=str(camera_id), offer=offer)
        return ANSWER

    monkeypatch.setattr(go2rtc, "whep", fake)
    r = await client.post(f"/v1/live/{s['id']}/whep", content=OFFER.encode(),
                          headers={**u["headers"], "Content-Type": "application/sdp"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/sdp") and r.text == ANSWER
    assert seen == {"camera": cams[0], "offer": OFFER}


async def test_without_a_smooth_video_service_the_app_is_told_to_keep_the_pictures(client, make, monkeypatch):
    monkeypatch.setattr(get_settings(), "go2rtc_url", "")
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    r = await client.post(f"/v1/live/{s['id']}/whep", content=OFFER.encode(),
                          headers={**u["headers"], "Content-Type": "application/sdp"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "smooth_video_unavailable"


async def test_an_empty_offer_is_refused_before_anything_is_started(client, make, monkeypatch):
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    r = await client.post(f"/v1/live/{s['id']}/whep", content=b"", headers={**u["headers"], "Content-Type": "application/sdp"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_offer"


async def test_ai_view_and_someone_elses_view_have_no_smooth_video(client, make, monkeypatch):
    t, u, cams = await shop(client, make)
    ai = await open_view(client, u, cams[0], "ai")
    r = await client.post(f"/v1/live/{ai['id']}/whep", content=OFFER.encode(), headers={**u["headers"], "Content-Type": "application/sdp"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "use_frames"
    live = await open_view(client, u, cams[1], "live")
    other = await make.user(t, role="admin")
    r = await client.post(f"/v1/live/{live['id']}/whep", content=OFFER.encode(), headers={**other["headers"], "Content-Type": "application/sdp"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_your_view"
