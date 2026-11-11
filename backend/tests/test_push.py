"""Push notifications: devices register and are forgotten, an alert is pushed to the account's devices only, and nothing breaks when push is off."""
import asyncio
import json
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings
from app.db import db
from app.services import push
from app.services.alerts import raise_alert

TOKEN_A = "a" * 40
TOKEN_B = "b" * 40


@pytest.fixture
def google(tmp_path, monkeypatch):
    """A stand-in for Google: it hands out an access token and records every message it is asked to deliver."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    f = tmp_path / "sa.json"
    f.write_text(json.dumps({"client_email": "push@proj.iam.gserviceaccount.com", "private_key": key, "token_uri": "https://oauth2.googleapis.com/token"}))
    s = get_settings()
    monkeypatch.setattr(s, "fcm_project_id", "demo-proj")
    monkeypatch.setattr(s, "fcm_service_account_file", str(f))
    monkeypatch.setattr(push, "_token", None)
    seen = {"messages": [], "gone": set()}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            assert b"assertion=" in req.content
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer ya29.test" and req.url.path == "/v1/projects/demo-proj/messages:send"
        m = json.loads(req.content)["message"]
        if m["token"] in seen["gone"]:
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
        seen["messages"].append(m)
        return httpx.Response(200, json={"name": "projects/demo-proj/messages/1"})
    monkeypatch.setattr(push, "transport", httpx.MockTransport(handler))
    return seen


async def register(client, user, token=TOKEN_A, platform="web"):
    return await client.post("/v1/devices", json={"token": token, "platform": platform}, headers=user["headers"])


async def fire(tenant_id, rule_type="camera_offline", severity=None):
    async with db.service_tx() as conn:
        a = await raise_alert(conn, tenant_id, rule_type, body="The camera stopped.", camera_name="Gate", dedupe_key=f"t:{uuid.uuid4()}", severity=severity)
    await asyncio.sleep(0.3)           # the push goes out in the background
    return a


async def test_a_device_registers_once_and_can_be_removed(client, make, su):
    t = await make.tenant()
    u = await make.user(t)
    assert (await register(client, u)).status_code == 204 and (await register(client, u)).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM app.push_devices WHERE tenant_id=$1", t) == 1
    assert (await client.get("/v1/push", headers=u["headers"])).json() == {"enabled": False, "devices": 1}
    assert (await client.post("/v1/devices", json={"token": "short", "platform": "web"}, headers=u["headers"])).status_code == 422
    assert (await client.post("/v1/devices", json={"token": TOKEN_A, "platform": "windows"}, headers=u["headers"])).status_code == 422
    assert (await client.post("/v1/devices/remove", json={"token": TOKEN_A}, headers=u["headers"])).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM app.push_devices WHERE tenant_id=$1", t) == 0


async def test_a_device_moves_to_whoever_signs_in_on_it_now(client, make, su):
    t1, t2 = await make.tenant(), await make.tenant()
    u1, u2 = await make.user(t1), await make.user(t2)
    await register(client, u1)
    await register(client, u2)                      # the same browser, now signed in to another account
    assert await su.fetchval("SELECT count(*) FROM app.push_devices WHERE tenant_id=$1", t1) == 0
    assert await su.fetchval("SELECT tenant_id FROM app.push_devices WHERE token=$1", TOKEN_A) == t2
    assert (await client.post("/v1/devices/remove", json={"token": TOKEN_A}, headers=u1["headers"])).status_code == 204       # not theirs any more: nothing happens
    assert await su.fetchval("SELECT count(*) FROM app.push_devices WHERE tenant_id=$1", t2) == 1


async def test_an_alert_is_pushed_to_that_accounts_devices_only(client, make, su, google):
    t1, t2 = await make.tenant(), await make.tenant()
    u1, u2 = await make.user(t1), await make.user(t2)
    await register(client, u1, TOKEN_A, "android")
    await register(client, u1, TOKEN_B, "web")
    await register(client, u2, "c" * 40, "ios")
    assert (await client.get("/v1/push", headers=u1["headers"])).json()["enabled"] is True
    a = await fire(t1)
    assert a
    got = {m["token"]: m for m in google["messages"]}
    assert set(got) == {TOKEN_A, TOKEN_B}, "the other account's device is not told"
    m = got[TOKEN_A]
    assert m["notification"]["title"] == "Camera offline" and m["notification"]["body"] == "The camera stopped."
    assert m["data"] == {"kind": "alert", "alert_id": str(a)} and m["android"]["priority"] == "HIGH"
    assert m["webpush"]["fcm_options"]["link"] == "https://gi.test/next/", "a tap on a browser notification opens the app"


async def test_info_alerts_and_quiet_hours_stay_silent(client, make, su, google):
    t = await make.tenant()
    u = await make.user(t)
    await register(client, u)
    await fire(t, "back_to_normal")                    # an "info" alert: WhatsApp only
    assert google["messages"] == []
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, quiet_start, quiet_end) VALUES ($1,'camera_offline',true,'00:00','23:59')", t)
    await fire(t)
    assert google["messages"] == [], "quiet hours"


async def test_a_device_that_no_longer_exists_is_forgotten(client, make, su, google):
    t = await make.tenant()
    u = await make.user(t)
    await register(client, u, TOKEN_A)
    await register(client, u, TOKEN_B)
    google["gone"].add(TOKEN_A)
    await fire(t)
    assert [m["token"] for m in google["messages"]] == [TOKEN_B]
    assert await su.fetchval("SELECT array_agg(token) FROM app.push_devices WHERE tenant_id=$1", t) == [TOKEN_B]


async def test_without_a_firebase_project_nothing_is_sent_and_nothing_breaks(client, make, su, monkeypatch):
    sent = []

    async def boom(*a, **k):
        sent.append(1)
    monkeypatch.setattr(push, "deliver", boom)
    t = await make.tenant()
    u = await make.user(t)
    await register(client, u)
    assert await fire(t)
    assert sent == [] and not push.enabled()


async def test_a_failing_google_never_holds_up_an_alert(client, make, su, google, monkeypatch):
    t = await make.tenant()
    u = await make.user(t)
    await register(client, u)
    monkeypatch.setattr(push, "transport", httpx.MockTransport(lambda r: httpx.Response(500, text="down")))
    assert await fire(t)
    assert await su.fetchval("SELECT count(*) FROM app.push_devices WHERE tenant_id=$1", t) == 1, "an outage does not forget devices"
