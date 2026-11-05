"""Alerts to WhatsApp and email, end to end: customer settings -> alert -> outbox -> provider -> delivery report -> evidence link."""
import asyncio
import os
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

os.environ["MOTO_S3_CUSTOM_ENDPOINTS"] = "https://r2.test.local"      # lets the fake S3 answer on an R2-style address
os.environ.setdefault("AWS_ACCESS_KEY_ID", "k")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "s")

import boto3
import pytest
from aiosmtpd.controller import Controller
from moto import mock_aws
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.messaging import delivery, evidence
from app.services import detectors, retention, sweeper
from tests.helpers import now, setup_cameras
from tests.test_events_plates import pe, post
from tests.tls_server import TlsServer

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegdata" * 50
HOOK = "hook-secret-xyz"


# ---------------------------------------------------------------------------------- the mock provider
class Bsp:
    def __init__(self):
        self.calls, self.script = [], []

    def next_reply(self):
        return self.script.pop(0) if self.script else (200, {"messages": [{"id": f"wamid.{uuid.uuid4().hex[:8]}"}]})

    async def handle(self, request):
        self.calls.append({"headers": dict(request.headers), "body": json.loads(await request.body() or b"{}")})
        status, data = self.next_reply()
        return JSONResponse(data, status_code=status)


@pytest.fixture(scope="module")
def bsp_server():
    bsp = Bsp()
    app = Starlette(routes=[Route("/v1/{to}/messages", bsp.handle, methods=["POST"])])
    with TlsServer(app) as srv:
        yield bsp, srv


class Smtp:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture(scope="module")
def smtp_server():
    import socket
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    h = Smtp()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    yield h, port
    c.stop()


@pytest.fixture
async def world(client, make, su, bsp_server, smtp_server):
    """A clean slate with a working WhatsApp provider (approved templates) and an email provider, both active."""
    bsp, srv = bsp_server
    smtp, smtp_port = smtp_server
    bsp.calls.clear(); bsp.script.clear(); smtp.messages.clear()
    evidence.set_store(None)
    await su.execute("UPDATE app.notifications SET status='cancelled' WHERE status='queued'")
    await su.execute("DELETE FROM platform.messaging_providers")
    await su.execute("DELETE FROM platform.message_templates")
    delivery.set_ssl_context(srv.ssl_context())
    owner = await make.staff("owner")
    h = owner["headers"]
    recipe = {"method": "POST", "url": f"https://localhost:{srv.port}/v1/{{{{recipient.phone_digits}}}}/messages", "headers": {"Authorization": "Bearer {{secret.api_key}}"},
              "body": {"to": "{{recipient.phone_digits}}", "ref": "{{message.id}}", "template": {"name": "{{template.name}}", "language": {"code": "{{template.language}}"},
                       "components": [{"$if": "media.is_image", "then": {"type": "header", "parameters": [{"type": "image", "image": {"link": "{{media.url}}"}}]}},
                                      {"$if": "media.is_video", "then": {"type": "header", "parameters": [{"type": "video", "video": {"link": "{{media.url}}"}}]}},
                                      {"type": "body", "parameters": [{"$each": "vars", "do": {"type": "text", "text": "{{item}}"}}]},
                                      {"$if": "link.token", "then": {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "{{link.token}}"}]}}]}},
              "success": {"status": [200]}, "message_id_path": "messages.0.id", "error_path": "error.message", "cost_paise": 85,
              "webhook": {"auth": {"type": "hmac_sha256", "header": "X-Sig", "secret": "hook", "prefix": "sha256="}, "events_path": "events", "id_path": "id",
                          "status_path": "state", "error_path": "why", "status_map": {"DELIVERED": "delivered", "READ": "read", "FAILED": "failed", "SENT": "sent"}}}
    r = await su.fetchval("SELECT 1")
    wa = await _provider(su, h, "whatsapp", "BSP", "http", recipe, {"api_key": "SECRET-API-KEY-1", "hook": HOOK}, client)
    em = await _provider(su, h, "email", "Mail", "smtp", {"host": "127.0.0.1", "port": smtp_port, "security": "none", "from_email": "alerts@gi.test", "from_name": "Gate Insight"},
                         {}, client)
    inst = await client.post("/admin/v1/messaging/templates/install-defaults", headers=h)
    assert inst.status_code == 200, inst.text
    await su.execute("UPDATE platform.message_templates SET status='approved' WHERE key IN ('alert_image','alert_link','alert_text')")
    return {"admin": h, "wa": wa, "em": em, "bsp": bsp, "smtp": smtp, "srv": srv}


async def _provider(su, h, channel, name, kind, config, secrets, client):
    r = await client.post("/admin/v1/messaging/providers", json={"channel": channel, "name": name, "kind": kind, "config": config, "secrets": secrets, "reason": "test setup"}, headers=h)
    assert r.status_code == 201, r.text
    a = await client.post(f"/admin/v1/messaging/providers/{r.json()['id']}/activate", params={"reason": "test setup"}, headers=h)
    assert a.status_code == 200, a.text
    return r.json()


async def customer(client, make, world, *, channels=("whatsapp",), phone="98765 43210", email="ravi@x.test", rule="blocked_plate", params=None, quiet=None):
    t, u, _, (cam,) = await setup_cameras(client, make)
    body = {"name": "Ravi", "consent_confirmed": True}
    if phone: body["phone"] = phone
    if email: body["email"] = email
    rec = await client.post("/v1/recipients", json=body, headers=u["headers"])
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    put = {"enabled": True, "channels": list(channels), "recipient_ids": [rid], "params": params or {}}
    if quiet: put.update(quiet)
    r = await client.put(f"/v1/alert-types/{rule}", json=put, headers=u["headers"])
    assert r.status_code == 200, r.text
    return t, u, cam, rid


async def block_and_see(client, make, u, cam, plate="TN72AB1234", evidence_id=None, track=None):
    r = await client.post("/v1/vehicle-lists", json={"plate_text": plate, "list_type": "blocked"}, headers=u["headers"])
    assert r.status_code in (201, 409)
    e = pe(cam, plate, ago=timedelta(minutes=1), track=track)
    if evidence_id: e["evidence_id"] = str(evidence_id)
    await post(client, make, [e])


async def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(HOOK.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------------- admin: providers and templates
async def test_provider_secrets_are_never_shown_and_only_one_is_active_per_channel(client, world, su):
    h = world["admin"]
    listed = (await client.get("/admin/v1/messaging/providers", headers=h)).json()
    wa = next(p for p in listed if p["channel"] == "whatsapp")
    assert wa["secret_names"] == ["api_key", "hook"] and "SECRET-API-KEY-1" not in json.dumps(listed)
    stored = await su.fetchval("SELECT secrets_ciphertext FROM platform.messaging_providers WHERE id=$1", wa["id"])
    assert b"SECRET-API-KEY-1" not in bytes(stored)                                   # encrypted at rest
    second = await client.post("/admin/v1/messaging/providers", json={"channel": "whatsapp", "name": "Other", "kind": "http", "secrets": {},
                               "config": {"url": "https://other.example.com/send"}, "reason": "second"}, headers=h)
    await client.post(f"/admin/v1/messaging/providers/{second.json()['id']}/activate", params={"reason": "switch over"}, headers=h)
    now_active = [p["name"] for p in (await client.get("/admin/v1/messaging/providers", headers=h)).json() if p["channel"] == "whatsapp" and p["active"]]
    assert now_active == ["Other"]
    audit = await su.fetch("SELECT action, after::text AS after FROM platform.audit_log WHERE action LIKE 'messaging.provider.%' ORDER BY id DESC LIMIT 10")
    assert audit and all("SECRET-API-KEY-1" not in a["after"] for a in audit if a["after"])


async def test_only_the_owner_manages_providers_and_templates(client, make, world):
    for role in ("support", "technician", "finance", "analyst"):
        s = await make.staff(role)
        r = await client.post("/admin/v1/messaging/providers", json={"channel": "email", "name": "x", "kind": "smtp", "config": {}, "reason": "no way"}, headers=s["headers"])
        assert r.status_code == 403, role
        assert (await client.post("/admin/v1/messaging/templates/install-defaults", headers=s["headers"])).status_code == 403
    assert (await client.get("/admin/v1/messaging/providers", headers=(await make.staff("support"))["headers"])).status_code == 200
    assert (await client.get("/admin/v1/messaging/providers", headers=(await make.staff("analyst"))["headers"])).status_code == 403


@pytest.mark.parametrize("config,why", [
    ({"url": "http://api.example.com/x"}, "https"),
    ({"url": "https://api.example.com/x", "headers": {"Host": "evil"}}, "not allowed"),
    ({"url": "https://api.example.com/x", "body": {"a": "{{secret.unknown}}"}}, "unknown variable"),
    ({"url": "https://api.example.com/x", "body": {"a": "{{recipient.phone"}}, "not closed"),
    ({"url": "https://user:pw@api.example.com/x"}, "credentials"),
])
async def test_a_bad_recipe_is_refused_with_a_reason(client, world, config, why):
    r = await client.post("/admin/v1/messaging/providers", json={"channel": "whatsapp", "name": "Bad", "kind": "http", "config": config, "secrets": {"api_key": "k"}, "reason": "trying it"}, headers=world["admin"])
    assert r.status_code == 422 and why in r.json()["error"]["message"]


async def test_preview_shows_the_request_without_sending_and_hides_secrets(client, world):
    h, wa = world["admin"], world["wa"]
    tpl = next(t for t in (await client.get("/admin/v1/messaging/templates", headers=h)).json() if t["key"] == "alert_image")
    r = await client.post(f"/admin/v1/messaging/providers/{wa['id']}/test", json={"to": "+919876543210", "template_key": "alert_image", "media_url": "https://gi.test/e/x/image.jpg"}, headers=h)
    p = r.json()["preview"]
    assert p["headers"]["Authorization"] == "Bearer ••••" and "SECRET-API-KEY-1" not in r.text
    assert p["body"]["to"] == "919876543210" and p["body"]["template"]["name"] == "gi_alert_image"
    assert [c["type"] for c in p["body"]["template"]["components"]] == ["header", "body", "button"]
    assert world["bsp"].calls == []                                                   # nothing was sent


async def test_a_test_send_really_calls_the_provider(client, world):
    r = await client.post(f"/admin/v1/messaging/providers/{world['wa']['id']}/test", json={"to": "+919876543210", "template_key": "alert_text", "send": True}, headers=world["admin"])
    assert r.json()["ok"] is True and r.json()["message_id"].startswith("wamid.")
    assert len(world["bsp"].calls) == 1 and world["bsp"].calls[0]["headers"]["authorization"] == "Bearer SECRET-API-KEY-1"


async def test_template_rules_and_submission_sheet(client, world):
    h = world["admin"]
    base = {"key": "t1", "provider_template": "t_one", "body_text": "Hello {{1}} and {{2}} today", "variables": ["event", "location"]}
    assert (await client.post("/admin/v1/messaging/templates", json=base, headers=h)).status_code == 201
    for body, code in [({**base, "key": "t2", "body_text": "{{1}} starts here and {{2}} ok", "variables": ["event", "location"]}, "variable_at_edge"),
                       ({**base, "key": "t3", "body_text": "Hi {{2}} then {{1}} ok"}, "variables_mismatch"),
                       ({**base, "key": "t4", "body_text": "Big sale {{1}} and {{2}} now", "variables": ["event", "location"]}, "promotional_wording"),
                       ({**base, "key": "t5", "variables": ["event", "bogus"]}, "unknown_variable"),
                       ({**base, "key": "t6", "body_text": "Hi {{1}} and {{2}} ok {{3}}"}, "variables_mismatch")]:
        r = await client.post("/admin/v1/messaging/templates", json=body, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (code, r.text)
    img = next(t for t in (await client.get("/admin/v1/messaging/templates", headers=h)).json() if t["key"] == "alert_image")
    sheet = (await client.get(f"/admin/v1/messaging/templates/{img['id']}/submission", headers=h)).json()
    assert sheet["category"] == "UTILITY" and sheet["header"] == "IMAGE" and len(sheet["samples"]) == 4 and sheet["button"]["type"] == "URL"


# ---------------------------------------------------------------------------------- customer: recipients and settings
async def test_a_phone_number_needs_the_customers_consent_confirmation(client, make, world, su):
    t, u, _, _ = await setup_cameras(client, make)
    h = u["headers"]
    r = await client.post("/v1/recipients", json={"name": "Guard", "phone": "9876543210"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    ok = await client.post("/v1/recipients", json={"name": "Guard", "phone": "098765 43210", "consent_confirmed": True}, headers=h)
    assert ok.status_code == 201 and ok.json()["phone"] == "+919876543210" and ok.json()["phone_masked"].endswith("3210")
    row = await su.fetchrow("SELECT consent_confirmed_by, consent_confirmed_at FROM app.recipients WHERE id=$1", ok.json()["id"])
    assert row["consent_confirmed_by"] == u["id"] and row["consent_confirmed_at"] is not None          # who confirmed, and when
    assert (await client.post("/v1/recipients", json={"name": "Dup", "phone": "+91 98765 43210", "consent_confirmed": True}, headers=h)).status_code == 409
    assert (await client.post("/v1/recipients", json={"name": "Mail only", "email": "a@b.co"}, headers=h)).status_code == 201     # email needs no confirmation
    for bad in ("12345", "abc", "+0123456789"):
        assert (await client.post("/v1/recipients", json={"name": "X", "phone": bad, "consent_confirmed": True}, headers=h)).status_code == 422
    assert (await client.post("/v1/recipients", json={"name": "Nobody"}, headers=h)).status_code == 422


async def test_changing_a_number_asks_for_consent_again(client, make, world):
    t, u, _, rid = await customer(client, make, world)
    h = u["headers"]
    r = await client.patch(f"/v1/recipients/{rid}", json={"phone": "9123456789"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    assert (await client.patch(f"/v1/recipients/{rid}", json={"phone": "9123456789", "consent_confirmed": True}, headers=h)).json()["phone"] == "+919123456789"
    assert (await client.patch(f"/v1/recipients/{rid}", json={"name": "Ravi K"}, headers=h)).status_code == 200      # a name change does not


async def test_the_alert_catalog_and_customer_choices(client, make, world):
    t, u, cam, rid = await customer(client, make, world, rule="camera_offline")
    h = u["headers"]
    cat = {c["type"]: c for c in (await client.get("/v1/alert-types", headers=h)).json()}
    assert set(cat) == {"blocked_plate", "vehicle_overstay", "crowding", "camera_offline", "recorder_offline", "low_confidence_plate"}
    assert cat["blocked_plate"]["enabled"] is True and cat["crowding"]["enabled"] is False and cat["camera_offline"]["recipient_ids"] == [rid]
    r = await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 80}, "channels": ["whatsapp", "email"], "recipient_ids": [rid],
                                                         "quiet_start": "22:00", "quiet_end": "06:00"}, headers=h)
    assert r.status_code == 200 and r.json()["params"] == {"people": 80} and r.json()["quiet_start"] == "22:00"
    off = await client.put("/v1/alert-types/blocked_plate", json={"enabled": False}, headers=h)
    assert off.json()["enabled"] is False and not any(c["type"] == "blocked_plate" and c["enabled"] for c in (await client.get("/v1/alert-types", headers=h)).json())
    for body in ({"enabled": True, "params": {"people": 0}}, {"enabled": True, "params": {"people": "many"}}, {"enabled": True, "params": {"nope": 1}},
                 {"enabled": True, "quiet_start": "22:00"}, {"enabled": True, "channels": ["sms"]}, {"enabled": True, "recipient_ids": [str(uuid.uuid4())]}):
        assert (await client.put("/v1/alert-types/crowding", json=body, headers=h)).status_code == 422, body
    assert (await client.put("/v1/alert-types/unknown", json={"enabled": True}, headers=h)).status_code == 404
    reg = await make.user(t, "regular")
    assert (await client.put("/v1/alert-types/crowding", json={"enabled": True}, headers=reg["headers"])).status_code == 403          # only an administrator
    assert (await client.get("/v1/alert-types", headers=reg["headers"])).status_code == 200


async def test_tenants_cannot_see_or_use_each_others_people_and_alerts(client, make, world):
    t1, u1, cam1, r1 = await customer(client, make, world)
    t2, u2, cam2, r2 = await customer(client, make, world, phone="9000000001")
    await block_and_see(client, make, u1, cam1)
    assert [r["id"] for r in (await client.get("/v1/recipients", headers=u2["headers"])).json()] == [r2]
    assert (await client.get("/v1/alerts", headers=u2["headers"])).json() == [] and (await client.get("/v1/notifications", headers=u2["headers"])).json() == []
    stolen = await client.put("/v1/alert-types/crowding", json={"enabled": True, "recipient_ids": [r1]}, headers=u2["headers"])
    assert stolen.status_code == 422
    assert (await client.delete(f"/v1/recipients/{r1}", headers=u2["headers"])).status_code == 404
    assert (await client.patch(f"/v1/recipients/{r1}", json={"name": "Hacked"}, headers=u2["headers"])).status_code == 404


async def test_a_view_only_staff_session_cannot_change_alert_settings(client, make, world):
    t, u, cam, rid = await customer(client, make, world)
    imp = await make.staff("support")
    r = await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer asked us to check alert settings", "minutes": 15}, headers=imp["headers"])
    assert r.status_code == 201, r.text
    tok = {"Authorization": "Bearer " + r.json()["token"]}
    assert (await client.get("/v1/alert-types", headers=tok)).status_code == 200
    assert (await client.put("/v1/alert-types/crowding", json={"enabled": True}, headers=tok)).status_code == 403
    assert (await client.post("/v1/recipients", json={"name": "X", "email": "x@y.co"}, headers=tok)).status_code == 403
    assert (await client.post(f"/v1/recipients/{rid}/test", params={"channel": "whatsapp"}, headers=tok)).status_code == 403


# ---------------------------------------------------------------------------------- the whole journey
async def test_blocked_vehicle_reaches_whatsapp_with_picture_and_link_then_delivery_reports_update_it(client, make, su, world):
    bsp = world["bsp"]
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1"); s3.create_bucket(Bucket="gi-evidence")
        evidence.set_store(evidence.R2Store("acct", "k", "s", "gi-evidence", "https://r2.test.local"))
        t, u, cam, rid = await customer(client, make, world)
        up = await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers={**make.service(), "Content-Type": "image/jpeg"})
        assert up.status_code == 201, up.text
        eid = up.json()["evidence_id"]
        assert s3.get_object(Bucket="gi-evidence", Key=await su.fetchval("SELECT storage_key FROM app.evidence WHERE id=$1", uuid.UUID(eid)))["Body"].read() == JPEG   # in the bucket
        await block_and_see(client, make, u, cam, evidence_id=eid)

        n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
        assert n["status"] == "queued" and n["channel"] == "whatsapp" and n["recipient"] == "+919876543210"           # queued with the alert, same transaction
        assert await delivery.run_once() == 1
        n = await su.fetchrow("SELECT * FROM app.notifications WHERE id=$1", n["id"])
        assert n["status"] == "sent" and n["template"] == "alert_image" and n["provider_message_id"].startswith("wamid.") and n["cost_paise"] == 85

        call = bsp.calls[0]
        body = call["body"]
        assert call["headers"]["authorization"] == "Bearer SECRET-API-KEY-1" and body["to"] == "919876543210" and body["ref"] == str(n["id"])
        comps = {c["type"]: c for c in body["template"]["components"]}
        link = comps["header"]["parameters"][0]["image"]["link"]
        assert link.startswith("https://gi.test/e/") and link.endswith("/image.jpg")
        texts = [p["text"] for p in comps["body"]["parameters"]]
        assert texts[0] == "Blocked vehicle TN72AB1234" and texts[1].startswith("Cam ") and "entering" in texts[3] or "Seen" in texts[3]
        token = comps["button"]["parameters"][0]["text"]
        assert link == f"https://gi.test/e/{token}/image.jpg"

        # the person opens the link: a page, and the picture, straight from the private bucket
        page = await client.get(f"/e/{token}")
        assert page.status_code == 200 and "Blocked vehicle TN72AB1234" in page.text and page.headers["cache-control"] == "private, no-store"
        img = await client.get(f"/e/{token}/image.jpg")
        assert img.status_code == 200 and img.content == JPEG and img.headers["content-type"] == "image/jpeg"
        assert (await client.head(f"/e/{token}/image.jpg")).status_code == 200

        # delivery reports arrive from the provider and never move a message backwards
        async def hook(events, good=True):
            raw = json.dumps({"events": events}).encode()
            return await client.post(f"/hooks/messaging/{world['wa']['id']}", content=raw, headers={"X-Sig": await sign(raw) if good else "sha256=bad"})
        mid = n["provider_message_id"]
        assert (await hook([{"id": mid, "state": "DELIVERED"}], good=False)).status_code == 401
        assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE id=$1", n["id"])) == "sent"
        assert (await hook([{"id": mid, "state": "DELIVERED"}])).json()["applied"] == 1
        assert (await hook([{"id": mid, "state": "SENT"}])).json()["applied"] == 0                  # late, out of order: ignored
        assert (await hook([{"id": mid, "state": "READ"}])).json()["applied"] == 1
        assert (await hook([{"id": mid, "state": "FAILED", "why": "x"}])).json()["applied"] == 0     # cannot fail after being read
        assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE id=$1", n["id"])) == "read"
        log = (await client.get("/v1/notifications", headers=u["headers"])).json()
        assert log[0]["status"] == "read" and log[0]["recipient"].endswith("3210") and "98765" not in log[0]["recipient"]
        al = (await client.get("/v1/alerts", headers=u["headers"])).json()
        assert al[0]["title"] == "Blocked vehicle TN72AB1234" and al[0]["has_evidence"] and al[0]["messages_sent"] == 1
    evidence.set_store(None)


async def test_evidence_links_expire_and_cannot_be_forged(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    up = await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers=make.service())
    eid = uuid.UUID(up.json()["evidence_id"])
    good = evidence.make_token(eid, 3600)
    assert (await client.get(f"/e/{good}/image.jpg")).status_code == 200
    assert (await client.get(f"/e/{evidence.make_token(eid, -10)}")).status_code == 410                   # expired
    assert (await client.get(f"/e/{evidence.make_token(eid, -10)}/image.jpg")).status_code == 410
    flipped = good[:-2] + ("AA" if good[-2:] != "AA" else "BB")
    assert (await client.get(f"/e/{flipped}")).status_code == 404                                         # tampered signature
    other = evidence.make_token(uuid.uuid4(), 3600)
    assert (await client.get(f"/e/{other}")).status_code == 410                                           # signed, but no such picture
    assert (await client.get("/e/not-a-token")).status_code == 404
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await client.get(f"/e/{good}/image.jpg")).status_code == 410                                  # a suspended account's pictures are not served
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_evidence_upload_checks(client, make, world):
    t, u, cam, rid = await customer(client, make, world)
    s = make.service()
    assert (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=b"GIF89a....", headers=s)).status_code == 415
    assert (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=b"\xff\xd8\xff" + b"x" * 3_000_000, headers=s)).status_code == 413
    assert (await client.post("/internal/v1/evidence", params={"camera_id": str(uuid.uuid4())}, content=JPEG, headers=s)).status_code == 404
    assert (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers=u["headers"])).status_code == 401     # a customer token is not a worker
    assert (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG)).status_code == 401


async def test_evidence_from_another_tenant_cannot_be_attached(client, make, su, world):
    t1, u1, cam1, _ = await customer(client, make, world)
    t2, u2, cam2, _ = await customer(client, make, world, phone="9000000002")
    eid = (await client.post("/internal/v1/evidence", params={"camera_id": cam1}, content=JPEG, headers=make.service())).json()["evidence_id"]
    await block_and_see(client, make, u2, cam2, evidence_id=eid)
    assert await su.fetchval("SELECT evidence_id FROM app.alerts WHERE tenant_id=$1", t2) is None


# ---------------------------------------------------------------------------------- failures, retries, fallbacks
async def test_a_temporary_failure_is_retried_later_with_backoff(client, make, su, world):
    bsp = world["bsp"]
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    bsp.script += [(503, {"error": {"message": "busy"}}), (429, {})]
    await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "queued" and n["attempts"] == 1 and "busy" in n["error"] and n["next_attempt_at"] > (await su.fetchval("SELECT now()")) + timedelta(seconds=20)
    assert await delivery.run_once() == 0                                                                  # not due yet
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE id=$1", n["id"])
    await delivery.run_once()
    assert (await su.fetchval("SELECT attempts FROM app.notifications WHERE id=$1", n["id"])) == 2
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE id=$1", n["id"])
    await delivery.run_once()                                                                              # third try: the provider is fine again
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE id=$1", n["id"])
    assert n["status"] == "sent" and n["attempts"] == 3 and n["error"] is None
    assert len(bsp.calls) == 3


async def test_retries_stop_after_five_attempts(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, email=None)
    await block_and_see(client, make, u, cam)
    world["bsp"].script += [(500, {})] * 10
    for _ in range(delivery.MAX_ATTEMPTS):
        await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE tenant_id=$1 AND status='queued'", t)
        await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "failed" and n["attempts"] == 5
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1", t) == 1       # nobody to fall back to


async def test_a_permanent_whatsapp_failure_falls_back_to_email(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, channels=("whatsapp",))
    await block_and_see(client, make, u, cam)
    world["bsp"].script.append((400, {"error": {"message": "number is not on WhatsApp"}}))
    await delivery.run_once()
    rows = {r["channel"]: r for r in await su.fetch("SELECT * FROM app.notifications WHERE tenant_id=$1", t)}
    assert rows["whatsapp"]["status"] == "failed" and "not on WhatsApp" in rows["whatsapp"]["error"] and rows["email"]["fallback_of"] == rows["whatsapp"]["id"]
    assert world["bsp"].calls.__len__() == 1                                                              # a permanent error is not retried
    await delivery.run_once()
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE id=$1", rows["email"]["id"])) == "sent"
    m = world["smtp"].messages[-1]
    assert m.rcpt_tos == ["ravi@x.test"] and b"Blocked vehicle TN72AB1234" in m.content and b"Subject: [Gate Insight] Blocked vehicle" in m.content


async def test_whatsapp_and_email_both_requested_send_both(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, channels=("whatsapp", "email"))
    await block_and_see(client, make, u, cam)
    await delivery.run_once()
    assert {r["channel"]: r["status"] for r in await su.fetch("SELECT channel::text, status::text FROM app.notifications WHERE tenant_id=$1", t)} == {"whatsapp": "sent", "email": "sent"}
    assert len(world["bsp"].calls) == 1 and len(world["smtp"].messages) == 1


async def test_without_an_active_provider_or_approved_template_messages_wait_instead_of_vanishing(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await su.execute("UPDATE platform.message_templates SET status='submitted'")
    await block_and_see(client, make, u, cam)
    await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "queued" and "approved" in n["error"] and world["bsp"].calls == []
    await su.execute("UPDATE platform.message_templates SET status='approved' WHERE key='alert_text'; UPDATE app.notifications SET next_attempt_at=now() WHERE id='%s'" % n["id"])
    await su.execute("UPDATE platform.messaging_providers SET active=false WHERE channel='whatsapp'")
    await delivery.run_once()
    assert "provider" in (await su.fetchval("SELECT error FROM app.notifications WHERE id=$1", n["id"]))
    await su.execute("UPDATE platform.messaging_providers SET active=true WHERE channel='whatsapp'; UPDATE app.notifications SET next_attempt_at=now() WHERE id='%s'" % n["id"])
    await delivery.run_once()
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE id=$1", n["id"])) == "sent"      # fixed by the administrator: it goes out


async def test_the_best_approved_template_is_used(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await su.execute("UPDATE platform.message_templates SET status='submitted' WHERE key='alert_image'")        # picture template not approved yet
    eid = (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers=make.service())).json()["evidence_id"]
    await block_and_see(client, make, u, cam, evidence_id=eid)
    await delivery.run_once()
    comps = [c["type"] for c in world["bsp"].calls[0]["body"]["template"]["components"]]
    assert comps == ["body", "button"]                                                                            # link template: no picture header
    assert world["bsp"].calls[0]["body"]["template"]["name"] == "gi_alert_link"


async def test_two_delivery_workers_never_send_the_same_message_twice(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    for i in range(12):
        await block_and_see(client, make, u, cam, plate=f"TN72AB{1000 + i}", track=f"t{i}")
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND status='queued'", t) == 12
    await asyncio.gather(delivery.run_once(6), delivery.run_once(6), delivery.run_once(6))
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND status='sent'", t) == 12
    assert len(world["bsp"].calls) == 12 and len({c["body"]["ref"] for c in world["bsp"].calls}) == 12


async def test_a_message_claimed_by_a_crashed_worker_is_picked_up_again(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    claimed = await delivery.claim(5)                                                                           # a worker claims it and dies
    assert len(claimed) == 1 and await delivery.run_once() == 0
    await su.execute("UPDATE app.notifications SET locked_until = now() - interval '1 second' WHERE tenant_id=$1", t)
    assert await delivery.run_once() == 1
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)) == "sent"


# ---------------------------------------------------------------------------------- limits and rules
async def test_a_disabled_alert_type_raises_nothing(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await client.put("/v1/alert-types/blocked_plate", json={"enabled": False}, headers=u["headers"])
    await block_and_see(client, make, u, cam)
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0


async def test_an_alert_with_no_recipients_still_shows_in_the_app(client, make, su, world):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await block_and_see(client, make, u, cam)
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1 and await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1", t) == 0


async def test_the_same_alert_inside_the_cool_down_is_raised_once(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam, track="a")
    await block_and_see(client, make, u, cam, track="b")
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1 and await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1", t) == 1


async def test_quiet_hours_record_the_alert_but_send_nothing(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, quiet={"quiet_start": "00:00", "quiet_end": "23:59"})
    await block_and_see(client, make, u, cam)
    n = await su.fetchrow("SELECT status::text, error FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "suppressed" and "Quiet" in n["error"] and await delivery.run_once() == 0 and world["bsp"].calls == []
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1
    from app.services.alerts import in_quiet_hours
    from datetime import time
    assert in_quiet_hours(time(23, 0), time(22, 0), time(6, 0)) and in_quiet_hours(time(3, 0), time(22, 0), time(6, 0)) and not in_quiet_hours(time(12, 0), time(22, 0), time(6, 0))
    assert in_quiet_hours(time(12, 0), time(9, 0), time(17, 0)) and not in_quiet_hours(time(18, 0), time(9, 0), time(17, 0)) and not in_quiet_hours(time(1, 0), None, None)


async def test_the_owner_can_cap_or_switch_off_a_tenants_messages(client, make, su, world):
    h = world["admin"]
    t, u, cam, rid = await customer(client, make, world, channels=("whatsapp", "email"))
    r = await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": True, "email": False, "monthly_cap": 2, "reason": "plan limit"}, headers=h)
    assert r.status_code == 200
    for i in range(3):
        await block_and_see(client, make, u, cam, plate=f"TN72AB{2000 + i}", track=f"c{i}")
    rows = await su.fetch("SELECT channel::text AS ch, status::text AS st, error FROM app.notifications WHERE tenant_id=$1 ORDER BY created_at", t)
    wa = [r for r in rows if r["ch"] == "whatsapp"]
    assert [r["st"] for r in wa] == ["queued", "queued", "suppressed"] and "limit" in wa[2]["error"]
    assert all(r["st"] == "suppressed" and "switched off" in r["error"] for r in rows if r["ch"] == "email")
    info = (await client.get(f"/admin/v1/messaging/tenants/{t}", headers=h)).json()
    assert info == {"whatsapp": True, "email": False, "clips": True, "monthly_cap": 2, "whatsapp_this_month": 2}
    warn = (await client.put("/v1/alert-types/crowding", json={"enabled": True, "channels": ["email"], "recipient_ids": [rid]}, headers=u["headers"])).json()["warnings"]
    assert any("switched off" in w for w in warn)
    assert (await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": True, "email": True, "monthly_cap": None, "reason": "ok"}, headers=(await make.staff("support"))["headers"])).status_code == 403


async def test_a_person_cannot_be_flooded(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    for i in range(25):
        await block_and_see(client, make, u, cam, plate=f"TN72AB{3000 + i}", track=f"f{i}")
    states = [r["status"] for r in await su.fetch("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)]
    assert states.count("queued") == 20 and states.count("suppressed") == 5


async def test_test_message_to_a_person_is_limited(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    h = u["headers"]
    for _ in range(3):
        assert (await client.post(f"/v1/recipients/{rid}/test", params={"channel": "whatsapp"}, headers=h)).status_code == 200
    assert (await client.post(f"/v1/recipients/{rid}/test", params={"channel": "whatsapp"}, headers=h)).status_code == 429
    await delivery.run_once()
    assert len(world["bsp"].calls) == 3 and world["bsp"].calls[0]["body"]["template"]["name"] == "gi_alert_text"
    other = await make.user(t, "regular")
    assert (await client.post(f"/v1/recipients/{rid}/test", params={"channel": "email"}, headers=other["headers"])).status_code == 403


async def test_removing_a_person_stops_their_messages(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    assert (await client.delete(f"/v1/recipients/{rid}", headers=u["headers"])).status_code == 204
    assert await delivery.run_once() == 0 and world["bsp"].calls == []
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)) == "cancelled"
    assert (await client.get("/v1/alert-types", headers=u["headers"])).json()[0]["recipient_ids"] == []


async def test_acknowledging_an_alert(client, make, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    a = (await client.get("/v1/alerts", params={"unacknowledged": True}, headers=u["headers"])).json()[0]
    assert (await client.post(f"/v1/alerts/{a['id']}/acknowledge", headers=u["headers"])).status_code == 200
    assert (await client.post(f"/v1/alerts/{a['id']}/acknowledge", headers=u["headers"])).status_code == 409
    assert (await client.get("/v1/alerts", params={"unacknowledged": True}, headers=u["headers"])).json() == []
    assert (await client.post(f"/v1/alerts/{uuid.uuid4()}/acknowledge", headers=u["headers"])).status_code == 404


# ---------------------------------------------------------------------------------- the other detectors
async def test_offline_camera_and_recorder_alerts(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="camera_offline")
    await client.put("/v1/alert-types/recorder_offline", json={"enabled": True, "channels": ["whatsapp"], "recipient_ids": [rid]}, headers=u["headers"])
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '5 minutes' WHERE id=$1", cam)
    assert await sweeper.mark_stale_cameras(60) == 1
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '5 minutes' WHERE id=$1", cam)
    assert await sweeper.mark_stale_cameras(60) == 1                                                            # flapping: no second alert inside the hour
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at = now() - interval '10 minutes' WHERE tenant_id=$1", t)
    await sweeper.mark_stale_recorders(90)
    titles = sorted(r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1", t))
    assert titles == ["Camera offline", "Recorder offline"]
    await delivery.run_once()
    texts = [c["body"]["template"]["components"][0]["parameters"][0]["text"] for c in world["bsp"].calls]
    assert sorted(texts) == ["Camera offline", "Recorder offline"] and all(c["body"]["template"]["name"] == "gi_alert_text" for c in world["bsp"].calls)


async def test_overstay_detector(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="vehicle_overstay", params={"max_hours": 3})
    from tests.test_events_plates import pe as pev
    await post(client, make, [pev(cam, "KA05MN4321", ago=timedelta(hours=5), direction="in"), pev(cam, "KA05MN1111", ago=timedelta(hours=5), direction="in"),
                              pev(cam, "KA05MN1111", ago=timedelta(hours=4), direction="out"), pev(cam, "KA05MN2222", ago=timedelta(hours=1), direction="in")])
    assert await detectors.vehicle_overstay() >= 1
    titles = [r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1", t)]
    assert titles == ["Vehicle KA05MN4321 has stayed too long"]                                                # left in time / not long enough: none
    await detectors.vehicle_overstay()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1                      # one alert per visit


async def test_crowding_detector(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="crowding", params={"people": 10})
    foot = (await client.get("/v1/cameras", headers=u["headers"])).json()
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,date_trunc('minute', now()),14,3)", t, cam)
    await detectors.crowding(); await detectors.crowding()
    rows = await su.fetch("SELECT body FROM app.alerts WHERE tenant_id=$1", t)
    assert len(rows) == 1 and "About 11 people" in rows[0]["body"]


async def test_low_confidence_plate_alert_is_optional(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="low_confidence_plate")
    await post(client, make, [pe(cam, "MH12AB1234", conf=40, track="lc1"), pe(cam, "MH12AB9999", conf=35, track="lc2")])
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='low_confidence_plate'", t) == 1


async def test_expired_pictures_are_deleted_from_storage(client, make, su, world):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1"); s3.create_bucket(Bucket="bucket-two")
        evidence.set_store(evidence.R2Store("a", "k", "s", "bucket-two", "https://r2.test.local"))
        t, u, cam, rid = await customer(client, make, world)
        eid = (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers=make.service())).json()["evidence_id"]
        key = await su.fetchval("SELECT storage_key FROM app.evidence WHERE id=$1", uuid.UUID(eid))
        assert s3.list_objects_v2(Bucket="bucket-two")["KeyCount"] >= 1
        await retention.purge_pictures(__import__("time").monotonic() + 30)
        assert s3.get_object(Bucket="bucket-two", Key=key)                                                              # not expired: kept
        await su.execute("UPDATE app.evidence SET expires_at = now() - interval '1 day' WHERE id=$1", uuid.UUID(eid))
        assert await retention.purge_pictures(__import__("time").monotonic() + 30) >= 1
        with pytest.raises(s3.exceptions.NoSuchKey):
            s3.get_object(Bucket="bucket-two", Key=key)
        assert await su.fetchval("SELECT count(*) FROM app.evidence WHERE id=$1", uuid.UUID(eid)) == 0
    evidence.set_store(None)


async def test_the_admin_delivery_log_and_summary(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    await delivery.run_once()
    h = world["admin"]
    rows = (await client.get("/admin/v1/messaging/notifications", params={"tenant_id": str(t)}, headers=h)).json()
    assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["tenant"]
    assert (await client.get("/admin/v1/messaging/summary", headers=h)).json()["last_24_hours"]["whatsapp"]["sent"] >= 1
    assert (await client.get("/admin/v1/messaging/notifications", headers=(await make.staff("analyst"))["headers"])).status_code == 200


async def test_webhook_rejects_unknown_providers_and_missing_signatures(client, world):
    raw = b'{"events": []}'
    assert (await client.post(f"/hooks/messaging/{uuid.uuid4()}", content=raw, headers={"X-Sig": await sign(raw)})).status_code == 401
    assert (await client.post(f"/hooks/messaging/{world['wa']['id']}", content=raw)).status_code == 401
    assert (await client.post(f"/hooks/messaging/{world['em']['id']}", content=raw, headers={"X-Sig": await sign(raw)})).status_code == 401   # an email provider has no webhook
    assert (await client.post(f"/hooks/messaging/{world['wa']['id']}", content=b"not json", headers={"X-Sig": await sign(b"not json")})).status_code == 400
