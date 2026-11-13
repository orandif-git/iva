"""Smooth live video is a switch for each customer, off for everyone, and the server caps how many smooth views run at once."""
from app.config import get_settings
from app.services import go2rtc
from tests.test_customer_live import open_view, shop

OFFER = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
ANSWER = "v=0\r\no=- 2 2 IN IP4 127.0.0.1\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
SDP = {"Content-Type": "application/sdp"}


async def start(client, u, cam, mode="live"):
    r = await client.post(f"/v1/cameras/{cam}/live", json={"mode": mode}, headers=u["headers"])
    assert r.status_code == 201, r.text
    return r.json()


async def test_it_is_off_for_everyone_until_staff_switch_it_on_for_a_customer(client, make, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://127.0.0.1:1984")
    t, u, cams = await shop(client, make)
    assert await su.fetchval("SELECT smooth_video FROM app.tenants WHERE id=$1", t) is False, "off by default"
    assert (await start(client, u, cams[0]))["smooth"] is False, "go2rtc is there but this customer does not have it, so the app is told not to try"
    owner = await make.staff("owner")
    r = await client.put(f"/admin/v1/tenants/{t}/smooth-video", json={"enabled": True, "reason": "customer asked for it"}, headers=owner["headers"])
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["available"] is True
    await client.delete(f"/v1/live/{(await su.fetchval('SELECT id FROM app.live_sessions WHERE tenant_id=$1 ORDER BY started_at DESC LIMIT 1', t))}", headers=u["headers"])
    on = await start(client, u, cams[0])
    assert on["smooth"] is True and on["ice"], "now the app is told to try, and which servers to use"
    await client.delete(f"/v1/live/{on['id']}", headers=u["headers"])
    assert (await start(client, u, cams[1], "ai"))["smooth"] is False, "AI view is drawn picture by picture"
    audit = await su.fetchrow("SELECT reason, after FROM platform.audit_log WHERE action='tenant.smooth_video' ORDER BY id DESC LIMIT 1")
    assert audit["reason"] == "customer asked for it"


async def test_a_customer_without_it_cannot_ask_for_smooth_video_even_if_the_app_tries(client, make, monkeypatch):
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://127.0.0.1:1984")
    t, u, cams = await shop(client, make)
    s = await open_view(client, u, cams[0])
    r = await client.post(f"/v1/live/{s['id']}/whep", content=OFFER.encode(), headers={**u["headers"], **SDP})
    assert r.status_code == 403 and r.json()["error"]["code"] == "smooth_video_off"


async def test_only_the_owner_can_change_it_and_support_can_see_it(client, make):
    t, u, cams = await shop(client, make)
    support = await make.staff("support")
    assert (await client.get(f"/admin/v1/tenants/{t}/smooth-video", headers=support["headers"])).status_code == 200
    assert (await client.put(f"/admin/v1/tenants/{t}/smooth-video", json={"enabled": True, "reason": "no right"}, headers=support["headers"])).status_code == 403
    owner = await make.staff("owner")
    assert (await client.put(f"/admin/v1/tenants/{t}/smooth-video", json={"enabled": True}, headers=owner["headers"])).status_code == 422, "a reason is required"
    info = (await client.get(f"/admin/v1/tenants/{t}/smooth-video", headers=owner["headers"])).json()
    assert info["enabled"] is False and "viewers_now" in info and info["max"] == get_settings().smooth_video_max


async def test_the_server_cap_gives_the_next_viewer_the_picture_view_and_frees_a_place_when_a_view_ends(client, make, su, monkeypatch):
    async def fake(camera_id, row, decrypt, offer):
        return ANSWER

    monkeypatch.setattr(go2rtc, "whep", fake)
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://127.0.0.1:1984")
    monkeypatch.setattr(get_settings(), "smooth_video_max", 1)
    await su.execute("UPDATE app.live_sessions SET ended_at=now() WHERE ended_at IS NULL")
    t, u, cams = await shop(client, make)
    await su.execute("UPDATE app.tenants SET smooth_video=true WHERE id=$1", t)
    first = await start(client, u, cams[0])
    assert first["smooth"] is True
    ok = await client.post(f"/v1/live/{first['id']}/whep", content=OFFER.encode(), headers={**u["headers"], **SDP})
    assert ok.status_code == 200 and await su.fetchval("SELECT smooth FROM app.live_sessions WHERE id=$1", first["id"]) is True
    # a second customer (also switched on) finds the server full
    t2, u2, cams2 = await shop(client, make)
    await su.execute("UPDATE app.tenants SET smooth_video=true WHERE id=$1", t2)
    second = await start(client, u2, cams2[0])
    assert second["smooth"] is False, "the app is told not to try, so it does not wait"
    busy = await client.post(f"/v1/live/{second['id']}/whep", content=OFFER.encode(), headers={**u2["headers"], **SDP})
    assert busy.status_code == 503 and busy.json()["error"]["code"] == "smooth_video_busy"
    # the same view asking again is not counted against itself
    again = await client.post(f"/v1/live/{first['id']}/whep", content=OFFER.encode(), headers={**u["headers"], **SDP})
    assert again.status_code == 200
    await client.delete(f"/v1/live/{first['id']}", headers=u["headers"])
    third = await start(client, u2, cams2[1])
    assert third["smooth"] is True, "the place was freed when the first view ended"
