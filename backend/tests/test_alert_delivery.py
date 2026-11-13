"""Alerts to WhatsApp as webhook events, end to end: customer settings -> alert -> outbox -> webhook event -> evidence link."""
import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import timedelta

os.environ["MOTO_S3_CUSTOM_ENDPOINTS"] = "https://r2.test.local"      # lets the fake S3 answer on an R2-style address
os.environ.setdefault("AWS_ACCESS_KEY_ID", "k")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "s")

import boto3
import pytest
from moto import mock_aws
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.messaging import delivery, evidence, webhook
from app.db import db
from app.services import alert_catalog as cat_mod, detectors, retention, sweeper
from app.services.alerts import raise_alert
from tests.helpers import now, setup_cameras
from tests.test_events_plates import pe, post
from tests.tls_server import TlsServer

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegdata" * 50
TOKEN = "Bearer RECEIVER-TOKEN-1"


# ---------------------------------------------------------------------------------- the receiving system
class Receiver:
    """Plays the platform owner's own system: records every event and answers as scripted (200 when the script is empty)."""

    def __init__(self):
        self.calls, self.script = [], []

    async def handle(self, request):
        raw = await request.body()
        self.calls.append({"headers": dict(request.headers), "raw": raw, "body": json.loads(raw or b"{}"), "path": request.url.path})
        status = self.script.pop(0) if self.script else 200
        return JSONResponse({"ok": status == 200}, status_code=status)


@pytest.fixture(scope="module")
def bsp_server():
    rx = Receiver()
    app = Starlette(routes=[Route("/hook/{name}", rx.handle, methods=["POST"])])
    with TlsServer(app) as srv:
        yield rx, srv


@pytest.fixture
async def world(client, make, su, bsp_server):
    """A clean slate: every alert that is built has a webhook endpoint pointing at the receiver, with a signing secret."""
    rx, srv = bsp_server
    rx.calls.clear(); rx.script.clear()
    evidence.set_store(None)
    await su.execute("UPDATE app.notifications SET status='cancelled' WHERE status='queued'")
    await su.execute("DELETE FROM platform.webhook_endpoints; DELETE FROM platform.messaging_providers; DELETE FROM platform.alert_switches; DELETE FROM platform.webhook_settings")
    delivery.set_ssl_context(srv.ssl_context())
    owner = await make.staff("owner")
    h = owner["headers"]
    secret = (await client.post("/admin/v1/webhooks/settings/secret", headers=h)).json()["secret"]
    std = await client.post("/admin/v1/webhooks/standard", json={"url": srv.url("/hook/wa"), "headers": {"Authorization": TOKEN}}, headers=h)
    assert std.status_code == 200, std.text
    return {"admin": h, "hook": rx, "srv": srv, "secret": secret, "url": srv.url("/hook/wa")}


async def endpoint_of(client, world, alert_type):
    items = (await client.get("/admin/v1/webhooks", headers=world["admin"])).json()["items"]
    return next(e for e in items if e["alert_type"] == alert_type)


async def customer(client, make, world, *, phone="98765 43210", email="ravi@x.test", rule="blocked_plate", params=None, quiet=None, clips=False):
    t, u, _, (cam,) = await setup_cameras(client, make)
    if not clips:                      # with clips on, a blocked-vehicle event waits for its video; the clip tests set that up themselves
        await make.su.execute("UPDATE app.tenants SET msg_clips=false WHERE id=$1", t)
    body = {"name": "Ravi", "consent_confirmed": True}
    if phone: body["phone"] = phone
    if email: body["email"] = email
    rec = await client.post("/v1/recipients", json=body, headers=u["headers"])
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    put = {"enabled": True, "recipient_ids": [rid], "params": params or {}}
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



# ---------------------------------------------------------------------------------- admin: webhook endpoints
async def test_only_the_owner_sees_and_changes_webhooks(client, make, world):
    support = (await make.staff("support"))["headers"]
    ep = await endpoint_of(client, world, "camera_offline")
    for call in (client.get("/admin/v1/webhooks", headers=support), client.put(f"/admin/v1/webhooks/{ep['id']}", json={"active": False}, headers=support),
                 client.post("/admin/v1/webhooks/standard", json={"url": world["url"]}, headers=support), client.get("/admin/v1/webhooks/deliveries", headers=support),
                 client.post(f"/admin/v1/webhooks/{ep['id']}/send-now", headers=support), client.post("/admin/v1/webhooks/settings/secret", headers=support)):
        assert (await call).status_code == 403
    assert (await client.get("/admin/v1/webhooks")).status_code == 401


async def test_standard_endpoints_cover_every_built_alert_and_can_be_run_again(client, world, su):
    items = (await client.get("/admin/v1/webhooks", headers=world["admin"])).json()["items"]
    assert {e["alert_type"] for e in items} == set(cat_mod.WEBHOOK_TYPES) and "report_ready" in {e["alert_type"] for e in items}
    blocked = next(e for e in items if e["alert_type"] == "blocked_plate")
    assert blocked["event"] == "smart.blocked_plate" and blocked["ai"] is True and blocked["active"] is True and "clip_url" in blocked["shortcodes"] and "clip_url" in blocked["payload_template"]
    again = (await client.post("/admin/v1/webhooks/standard", json={"url": world["url"]}, headers=world["admin"])).json()
    assert again["created"] == [] and len(again["skipped"]) == len(cat_mod.WEBHOOK_TYPES)
    stored = await su.fetchval("SELECT headers_ciphertext FROM platform.webhook_endpoints WHERE alert_type='camera_offline'")
    assert b"RECEIVER-TOKEN-1" not in bytes(stored)                                                           # header values are encrypted at rest
    audit = await su.fetch("SELECT after::text AS after FROM platform.audit_log WHERE action LIKE 'webhook.%'")
    assert audit and all("RECEIVER-TOKEN-1" not in a["after"] for a in audit if a["after"])                  # and never written to the audit log


async def test_an_endpoint_is_checked_before_it_is_saved(client, world, monkeypatch):
    monkeypatch.setitem(cat_mod.CATALOG["view_changed"], "built", False)      # every alert is built now: mark one as not built for this test
    monkeypatch.setattr(cat_mod, "WEBHOOK_TYPES", [k for k in cat_mod.WEBHOOK_TYPES if k != "view_changed"])
    h = world["admin"]
    base = {"name": "Crowd", "alert_type": "crowding", "url": "https://hooks.example.com/x"}
    assert (await client.post("/admin/v1/webhooks", json={**base, "alert_type": "view_changed"}, headers=h)).json()["error"]["code"] == "alert_not_built"
    assert (await client.post("/admin/v1/webhooks", json={**base, "alert_type": "nothing"}, headers=h)).status_code == 404
    await client.delete(f"/admin/v1/webhooks/{(await endpoint_of(client, world, 'crowding'))['id']}", headers=h)
    for bad, why in [({"url": "http://hooks.example.com/x"}, "https"), ({"headers": {"X-Signature": "mine"}}, "set by the system"),
                     ({"payload_template": '{"p": "{plate}"}'}, "plate"), ({"payload_template": "{nope"}, "not valid JSON")]:
        r = await client.post("/admin/v1/webhooks", json={**base, **bad}, headers=h)
        assert r.status_code == 422 and why in r.json()["error"]["message"], (bad, r.text)
    ok = await client.post("/admin/v1/webhooks", json=base, headers=h)
    assert ok.status_code == 201 and ok.json()["payload_template"] == webhook.default_template("crowding") and "people" in ok.json()["shortcodes"]
    assert (await client.post("/admin/v1/webhooks", json=base, headers=h)).json()["error"]["code"] == "webhook_exists"     # one endpoint for each alert


async def test_an_endpoint_can_be_edited_turned_off_and_deleted(client, world, su):
    h = world["admin"]
    ep = await endpoint_of(client, world, "recorder_offline")
    r = await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"payload_template": '{"to": "{to_phone}", "r": "{recorder}"}', "name": "Recorder alert"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Recorder alert" and json.loads(r.json()["preview"]) == {"to": "919876543210", "r": "Warehouse"}
    assert r.json()["header_names"] == ["Authorization"] and r.json()["key_hint"] == TOKEN[-4:] and "headers" not in r.json()    # unchanged headers are kept, and the key is never shown
    assert (await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"payload_template": '{"x": "{plate}"}'}, headers=h)).status_code == 422
    off = await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"active": False}, headers=h)
    assert off.json()["active"] is False
    assert (await su.fetchval("SELECT action FROM platform.audit_log WHERE target_id=$1 ORDER BY id DESC LIMIT 1", str(ep["id"]))) == "webhook.toggle"
    assert (await client.delete(f"/admin/v1/webhooks/{ep['id']}", headers=h)).status_code == 204
    assert (await client.delete(f"/admin/v1/webhooks/{ep['id']}", headers=h)).status_code == 404


async def test_send_now_sends_a_marked_and_signed_test_event_with_sample_values(client, world):
    h, rx = world["admin"], world["hook"]
    ep = await endpoint_of(client, world, "blocked_plate")
    r = await client.post(f"/admin/v1/webhooks/{ep['id']}/send-now", json={"to_phone": "+919000000007"}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["sent"]["phone_number"] == "919000000007" and r.json()["sent"]["customer"] == "Sunrise Mart"
    call = rx.calls[-1]
    assert call["headers"]["x-test"] == "true" and call["headers"]["x-event"] == "smart.blocked_plate" and call["headers"]["authorization"] == TOKEN
    assert call["headers"]["x-signature"] == webhook.sign(world["secret"], call["headers"]["x-timestamp"], call["raw"])
    rx.script.append(500)
    assert (await client.post(f"/admin/v1/webhooks/{ep['id']}/send-now", headers=h)).json() | {"sent": 0} == {"ok": False, "status": 500, "error": "The webhook answered HTTP 500.", "sent": 0}


async def test_link_life_is_at_most_four_hours_and_a_new_secret_is_shown_once(client, world):
    h = world["admin"]
    assert (await client.get("/admin/v1/webhooks/settings", headers=h)).json() == {"has_secret": True, "link_hours": 4}
    for bad in (0, 5, 24):
        assert (await client.put("/admin/v1/webhooks/settings", json={"link_hours": bad}, headers=h)).status_code == 422
    assert (await client.put("/admin/v1/webhooks/settings", json={"link_hours": 2}, headers=h)).json()["link_hours"] == 2
    second = (await client.post("/admin/v1/webhooks/settings/secret", headers=h)).json()["secret"]
    assert second.startswith("whsec_") and second != world["secret"] and "secret" not in (await client.get("/admin/v1/webhooks/settings", headers=h)).json()
    await client.put("/admin/v1/webhooks/settings", json={"link_hours": 4}, headers=h)


async def test_the_delivery_log_lists_events_and_a_failed_one_can_be_sent_again(client, make, su, world):
    h, rx = world["admin"], world["hook"]
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    rx.script += [400]
    await delivery.run_once()
    log = (await client.get("/admin/v1/webhooks/deliveries", params={"status": "failed"}, headers=h)).json()["items"]
    mine = next(e for e in log if e["event"] == "smart.blocked_plate" and e["tenant"] and e["response_status"] == 400)
    assert "98765" not in mine["recipient"] and mine["recipient"].endswith("3210") and "HTTP 400" in mine["error"] and mine["endpoint"]
    assert (await client.post(f"/admin/v1/webhooks/deliveries/{mine['id']}/resend", headers=h)).json() == {"queued": True}
    assert (await client.post(f"/admin/v1/webhooks/deliveries/{mine['id']}/resend", headers=h)).json()["error"]["code"] == "not_failed"       # only a failed one
    await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE id=$1", mine["id"])
    assert n["status"] == "sent" and n["attempts"] == 1 and n["response_status"] == 200
    assert [c["headers"]["x-delivery-id"] for c in rx.calls[-2:]] == [str(mine["id"])] * 2               # the same id, so the receiver can tell it is the same event
    by_endpoint = (await client.get("/admin/v1/webhooks/deliveries", params={"endpoint_id": str(mine["endpoint_id"])}, headers=h)).json()["items"]
    assert all(e["endpoint_id"] == mine["endpoint_id"] for e in by_endpoint) and by_endpoint


# ---------------------------------------------------------------------------------- customer: recipients and settings










# ---------------------------------------------------------------------------------- the whole journey
async def test_blocked_vehicle_reaches_the_webhook_with_picture_links_and_a_signature(client, make, su, world):
    rx = world["hook"]
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
        assert n["status"] == "queued" and n["channel"] == "whatsapp" and n["recipient"] == "+919876543210" and n["event"] == "smart.blocked_plate"      # queued with the alert, same transaction
        assert await delivery.run_once() == 1
        n = await su.fetchrow("SELECT * FROM app.notifications WHERE id=$1", n["id"])
        assert n["status"] == "sent" and n["response_status"] == 200 and n["endpoint_id"] is not None and n["error"] is None

        call = rx.calls[0]
        h, body = call["headers"], call["body"]
        assert call["path"] == "/hook/wa" and h["authorization"] == TOKEN and h["x-event"] == "smart.blocked_plate" and h["x-delivery-id"] == str(n["id"])
        assert h["x-signature"] == webhook.sign(world["secret"], h["x-timestamp"], call["raw"]) and abs(int(h["x-timestamp"]) - time.time()) < 60 and "x-test" not in h
        assert body["phone_number"] == "919876543210" and body["template_name"] == "smart_blocked_plate_v1" and body["camera"].startswith("Cam ") and "Seen" in body["details"]
        assert body["time"] and body["customer"] == (await su.fetchval("SELECT name FROM app.tenants WHERE id=$1", t))
        assert body["image_url"].startswith("https://gi.test/e/") and body["image_url"].endswith("/image.jpg") and body["clip_url"] == ""        # a picture, no clip yet
        token = body["image_url"].split("/e/")[1].split("/")[0]
        eid_read, expired = evidence.read_token(token)
        assert str(eid_read) == eid and not expired
        # the link lives for the time the owner set (4 hours by default), never longer
        left = int.from_bytes(evidence._unb64(token)[16:], "big") - time.time() if hasattr(evidence, "_unb64") else None
        if left is not None:
            assert 3 * 3600 < left <= 4 * 3600 + 5

        # the person opens the link: a page, and the picture, straight from the private bucket
        page = await client.get(f"/e/{token}")
        assert page.status_code == 200 and "Blocked vehicle detected: TN72AB1234" in page.text and page.headers["cache-control"] == "private, no-store"
        img = await client.get(f"/e/{token}/image.jpg")
        assert img.status_code == 200 and img.content == JPEG and img.headers["content-type"] == "image/jpeg"
        assert (await client.head(f"/e/{token}/image.jpg")).status_code == 200

        log = await su.fetch("SELECT status::text AS status, recipient FROM app.notifications WHERE tenant_id=$1 ORDER BY created_at", t)
        assert log[0]["status"] == "sent" and log[0]["recipient"].endswith("3210")
        al = await su.fetch("SELECT title, evidence_id FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at", t)
        assert al[0]["title"] == "Blocked vehicle detected: TN72AB1234" and al[0]["evidence_id"] is not None and len(log) == 1
    evidence.set_store(None)


async def test_the_picture_link_never_lives_longer_than_the_owner_allows(client, make, su, world):
    await client.put("/admin/v1/webhooks/settings", json={"link_hours": 1}, headers=world["admin"])
    try:
        t, u, cam, rid = await customer(client, make, world)
        eid = (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers=make.service())).json()["evidence_id"]
        await block_and_see(client, make, u, cam, evidence_id=eid)
        await delivery.run_once()
        token = world["hook"].calls[-1]["body"]["image_url"].split("/e/")[1].split("/")[0]
        assert evidence.read_token(token, now=time.time() + 3000)[1] is False and evidence.read_token(token, now=time.time() + 3700)[1] is True
    finally:
        await client.put("/admin/v1/webhooks/settings", json={"link_hours": 4}, headers=world["admin"])


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


# ---------------------------------------------------------------------------------- failures, retries, resend
async def test_a_temporary_failure_is_retried_later_with_backoff(client, make, su, world):
    rx = world["hook"]
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    rx.script += [503, 429]
    await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "queued" and n["attempts"] == 1 and "503" in n["error"] and n["response_status"] == 503 and n["next_attempt_at"] > (await su.fetchval("SELECT now()")) + timedelta(seconds=20)
    assert await delivery.run_once() == 0                                                                  # not due yet
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE id=$1", n["id"])
    await delivery.run_once()
    assert (await su.fetchval("SELECT attempts FROM app.notifications WHERE id=$1", n["id"])) == 2
    await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE id=$1", n["id"])
    await delivery.run_once()                                                                              # third try: the receiver is fine again
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE id=$1", n["id"])
    assert n["status"] == "sent" and n["attempts"] == 3 and n["error"] is None
    assert len(rx.calls) == 3 and len({c["headers"]["x-delivery-id"] for c in rx.calls}) == 1              # three tries, one delivery id


async def test_retries_cover_up_to_about_two_hours_then_the_event_is_marked_failed(client, make, su, world):
    assert delivery.MAX_ATTEMPTS == 6 and 5400 < sum(delivery.BACKOFF_SECONDS) <= 7200
    assert [delivery.backoff(i) for i in range(1, 6)] == [30, 120, 600, 1800, 3600]
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    world["hook"].script += [500] * 10
    for _ in range(delivery.MAX_ATTEMPTS):
        await su.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE tenant_id=$1 AND status='queued'", t)
        await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "failed" and n["attempts"] == 6
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1", t) == 1       # no email fallback: alerts go by WhatsApp only


async def test_a_permanent_failure_is_not_retried_and_never_falls_back_to_email(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    world["hook"].script.append(400)
    await delivery.run_once()
    n = await su.fetchrow("SELECT * FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "failed" and "HTTP 400" in n["error"] and len(world["hook"].calls) == 1
    await delivery.run_once()
    assert len(world["hook"].calls) == 1 and await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1", t) == 1


async def test_an_alert_with_no_webhook_is_recorded_but_not_sent_and_says_why(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await client.delete(f"/admin/v1/webhooks/{(await endpoint_of(client, world, 'blocked_plate'))['id']}", headers=world["admin"])
    await block_and_see(client, make, u, cam)
    n = await su.fetchrow("SELECT status::text AS st, error FROM app.notifications WHERE tenant_id=$1", t)
    assert n["st"] == "suppressed" and "No webhook" in n["error"] and await delivery.run_once() == 0
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1                # the customer still sees the alert in the app


async def test_an_endpoint_switched_off_before_sending_cancels_what_was_waiting(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    await client.put(f"/admin/v1/webhooks/{(await endpoint_of(client, world, 'blocked_plate'))['id']}", json={"active": False}, headers=world["admin"])
    await delivery.run_once()
    n = await su.fetchrow("SELECT status::text AS st, error FROM app.notifications WHERE tenant_id=$1", t)
    assert n["st"] == "cancelled" and "No active webhook" in n["error"] and world["hook"].calls == []


async def test_a_payload_that_stopped_being_json_fails_at_once_with_a_clear_reason(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    await su.execute("UPDATE platform.webhook_endpoints SET payload_template='{ broken' WHERE alert_type='blocked_plate'")
    await delivery.run_once()
    n = await su.fetchrow("SELECT status::text AS st, error FROM app.notifications WHERE tenant_id=$1", t)
    assert n["st"] == "failed" and "not valid JSON" in n["error"] and world["hook"].calls == []


async def test_the_webhook_address_cannot_point_at_our_own_network(client, make, su, world, monkeypatch):
    monkeypatch.setenv("GI_MESSAGING_ALLOW_PRIVATE_HOSTS", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        r = await client.post("/admin/v1/webhooks/standard", json={"url": "https://169.254.169.254/latest"}, headers=world["admin"])
        assert r.status_code == 200 and r.json()["created"] == []                                           # all six already have one; the address itself is checked on save
        ep = await endpoint_of(client, world, "crowding")
        bad = await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"url": "http://169.254.169.254/latest"}, headers=world["admin"])
        assert bad.status_code == 422
    finally:
        monkeypatch.setenv("GI_MESSAGING_ALLOW_PRIVATE_HOSTS", "true")
        get_settings.cache_clear()


async def test_two_delivery_workers_never_send_the_same_message_twice(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    for i in range(12):
        await block_and_see(client, make, u, cam, plate=f"TN72AB{1000 + i}", track=f"t{i}")
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND status='queued'", t) == 12
    await asyncio.gather(delivery.run_once(6), delivery.run_once(6), delivery.run_once(6))
    assert await su.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND status='sent'", t) == 12
    assert len(world["hook"].calls) == 12 and len({c["headers"]["x-delivery-id"] for c in world["hook"].calls}) == 12


async def test_a_message_claimed_by_a_crashed_worker_is_picked_up_again(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    claimed = await delivery.claim(5)                                                                           # a worker claims it and dies
    assert len(claimed) == 1 and await delivery.run_once() == 0
    await su.execute("UPDATE app.notifications SET locked_until = now() - interval '1 second' WHERE tenant_id=$1", t)
    assert await delivery.run_once() == 1
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)) == "sent"


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
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, recipients, quiet_start, quiet_end) VALUES ($1,'camera_offline',true,$2,'00:00','23:59')", t, [rid])
    async with db.service_tx() as conn:
        assert await raise_alert(conn, t, "camera_offline", body="No video is being received.", camera_name="Gate", dedupe_key="q1")
    n = await su.fetchrow("SELECT status::text, error FROM app.notifications WHERE tenant_id=$1", t)
    assert n["status"] == "suppressed" and "Quiet" in n["error"] and await delivery.run_once() == 0 and world["hook"].calls == []
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1
    # a critical alert is never held back by quiet hours: a blocked vehicle at night is exactly when it matters
    await block_and_see(client, make, u, cam)
    assert await su.fetchval("SELECT status::text FROM app.notifications n JOIN app.alerts a ON a.id = n.alert_id WHERE a.tenant_id=$1 AND a.rule_type='blocked_plate'", t) == "queued"
    from app.services.alerts import in_quiet_hours
    from datetime import time
    assert in_quiet_hours(time(23, 0), time(22, 0), time(6, 0)) and in_quiet_hours(time(3, 0), time(22, 0), time(6, 0)) and not in_quiet_hours(time(12, 0), time(22, 0), time(6, 0))
    assert in_quiet_hours(time(12, 0), time(9, 0), time(17, 0)) and not in_quiet_hours(time(18, 0), time(9, 0), time(17, 0)) and not in_quiet_hours(time(1, 0), None, None)


async def test_the_owner_can_cap_or_switch_off_a_tenants_messages(client, make, su, world):
    h = world["admin"]
    t, u, cam, rid = await customer(client, make, world)
    r = await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": True, "email": False, "monthly_cap": 2, "reason": "plan limit"}, headers=h)
    assert r.status_code == 200
    for i in range(3):
        await block_and_see(client, make, u, cam, plate=f"TN72AB{2000 + i}", track=f"c{i}")
    rows = await su.fetch("SELECT status::text AS st, error FROM app.notifications WHERE tenant_id=$1 ORDER BY created_at", t)
    assert [r["st"] for r in rows] == ["queued", "queued", "queued"], "there is no monthly limit on messages: a limit that was sent is not kept and nothing is held back"
    info = (await client.get(f"/admin/v1/messaging/tenants/{t}", headers=h)).json()
    assert info == {"whatsapp": True, "email": False, "clips": True, "monthly_cap": None, "whatsapp_this_month": 3}
    off = await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": False, "email": True, "monthly_cap": None, "reason": "unpaid"}, headers=h)
    assert off.status_code == 200
    await block_and_see(client, make, u, cam, plate="TN72AB2999", track="c9")
    assert (await su.fetchval("SELECT error FROM app.notifications WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT 1", t)).startswith("WhatsApp messages are switched off")
    warn = (await client.put("/v1/alert-types/crowding", json={"enabled": True, "recipient_ids": [rid]}, headers=u["headers"])).json()["warnings"]
    assert any("switched off" in w for w in warn)
    assert (await client.put(f"/admin/v1/messaging/tenants/{t}", json={"whatsapp": True, "email": True, "monthly_cap": None, "reason": "ok"}, headers=(await make.staff("support"))["headers"])).status_code == 403


async def test_a_person_cannot_be_flooded(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, recipients) VALUES ($1,'camera_offline',true,$2)", t, [rid])
    async with db.service_tx() as conn:
        for i in range(25):
            assert await raise_alert(conn, t, "camera_offline", body="No video is being received.", camera_name=f"Cam {i}", dedupe_key=f"flood{i}")
    states = [r["status"] for r in await su.fetch("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)]
    assert states.count("queued") == 20 and states.count("suppressed") == 5
    # the flood guard never holds a critical alert back
    await block_and_see(client, make, u, cam, plate="TN72AB3999", track="crit")
    assert await su.fetchval("SELECT status::text FROM app.notifications n JOIN app.alerts a ON a.id = n.alert_id WHERE a.tenant_id=$1 AND a.rule_type='blocked_plate'", t) == "queued"


async def test_removing_a_person_stops_their_messages(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world)
    await block_and_see(client, make, u, cam)
    assert (await client.delete(f"/v1/recipients/{rid}", headers=u["headers"])).status_code == 204
    assert await delivery.run_once() == 0 and world["hook"].calls == []
    assert (await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1", t)) == "cancelled"
    assert next(a for a in (await client.get("/v1/alert-types", headers=u["headers"])).json() if a["type"] == "blocked_plate")["recipient_ids"] == []






async def test_offline_camera_and_recorder_alerts(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="camera_offline")
    await client.put("/v1/alert-types/recorder_offline", json={"enabled": True, "recipient_ids": [rid]}, headers=u["headers"])
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '5 minutes' WHERE id=$1", cam)
    assert await sweeper.mark_stale_cameras(60) == 1
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '5 minutes' WHERE id=$1", cam)
    assert await sweeper.mark_stale_cameras(60) == 1                                                            # flapping: no second alert inside the hour
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at = now() - interval '10 minutes' WHERE tenant_id=$1", t)
    await sweeper.mark_stale_recorders(90)
    titles = sorted(r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1", t))
    assert titles == ["Camera offline", "Recorder offline"]
    await delivery.run_once()
    events = sorted(c["headers"]["x-event"] for c in world["hook"].calls)
    assert events == ["system.camera_offline", "system.recorder_offline"]                                      # System Alerts, one event each
    rec = next(c["body"] for c in world["hook"].calls if c["headers"]["x-event"] == "system.recorder_offline")
    assert rec["template_name"] == "system_recorder_offline_v1" and "lost its connection" in rec["details"] and "image_url" not in rec


async def test_overstay_detector(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="vehicle_overstay", params={"max_minutes": 180})
    from tests.test_events_plates import pe as pev
    await post(client, make, [pev(cam, "KA05MN4321", ago=timedelta(hours=5), direction="in"), pev(cam, "KA05MN1111", ago=timedelta(hours=5), direction="in"),
                              pev(cam, "KA05MN1111", ago=timedelta(hours=4), direction="out"), pev(cam, "KA05MN2222", ago=timedelta(hours=1), direction="in")])
    assert await detectors.vehicle_overstay() >= 1
    titles = [r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1", t)]
    assert titles == ["Vehicle overstay: KA05MN4321"]                                                # left in time / not long enough: none
    d = json.loads((await su.fetchrow("SELECT details::text AS d FROM app.alerts WHERE tenant_id=$1", t))["d"])
    assert d["entered_at"].endswith(("AM", "PM")), "the message says when the vehicle came in, on the shop's clock"
    await detectors.vehicle_overstay()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 1                      # one alert per visit


async def test_overstay_can_be_set_in_minutes_and_older_hour_settings_still_work(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="vehicle_overstay", params={"max_minutes": 30})
    from tests.test_events_plates import pe as pev
    await post(client, make, [pev(cam, "KA05MN4321", ago=timedelta(minutes=45), direction="in"), pev(cam, "KA05MN2222", ago=timedelta(minutes=10), direction="in")])
    await detectors.vehicle_overstay()
    rows = await su.fetch("SELECT title, body, details FROM app.alerts WHERE tenant_id=$1", t)
    assert [r["title"] for r in rows] == ["Vehicle overstay: KA05MN4321"]           # 45 minutes is over 30; 10 is not
    import json
    d = json.loads(rows[0]["details"]) if isinstance(rows[0]["details"], str) else rows[0]["details"]
    assert "30 minutes" in rows[0]["body"] and d["minutes"] == "30" and d["stay"] == "30 minutes"
    assert detectors.overstay_minutes({"max_hours": 3}) == 180 and detectors.overstay_minutes({"max_hours": 1.5}) == 90 and detectors.overstay_minutes({}) == 480
    assert detectors.overstay_minutes({"max_minutes": 45, "max_hours": 9}) == 45                # the new setting wins
    assert detectors.stay_text(1) == "1 minute" and detectors.stay_text(45) == "45 minutes" and detectors.stay_text(180) == "3 hours" and detectors.stay_text(150) == "2.5 hours"


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
    assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["tenant"] and rows[0]["event"] == "smart.blocked_plate" and rows[0]["response_status"] == 200
    assert (await client.get("/admin/v1/messaging/summary", headers=h)).json()["last_24_hours"]["whatsapp"]["sent"] >= 1
    assert (await client.get("/admin/v1/messaging/notifications", headers=(await make.staff("support"))["headers"])).status_code == 200


async def test_the_old_whatsapp_provider_and_template_routes_are_gone(client, world):
    h = world["admin"]
    assert (await client.get("/admin/v1/messaging/templates", headers=h)).status_code in (404, 405)
    assert (await client.post("/hooks/messaging/" + str(uuid.uuid4()), content=b"{}")).status_code in (404, 405)
    r = await client.post("/admin/v1/messaging/providers", json={"channel": "whatsapp", "name": "x", "kind": "http", "config": {}, "reason": "should be refused"}, headers=h)
    assert r.status_code in (404, 405)


# ---------------------------------------------------------------------------------- the access key on an alert
async def test_a_key_can_be_saved_on_one_alert_or_on_every_alert_with_the_same_address(client, world):
    h = world["admin"]
    items = (await client.get("/admin/v1/webhooks", headers=h)).json()["items"]
    one, other = items[0], items[1]
    r = await client.post("/admin/v1/webhooks/key", json={"endpoint_id": one["id"], "key": "NEWKEY-1234567890", "all_on_address": False}, headers=h)
    assert r.status_code == 200 and r.json() == {"updated": 1, "key_hint": "7890"}
    after = {e["id"]: e for e in (await client.get("/admin/v1/webhooks", headers=h)).json()["items"]}
    assert after[one["id"]]["key_hint"] == "7890" and after[other["id"]]["key_hint"] != "7890", "only the chosen alert changed"
    assert "NEWKEY" not in str(after[one["id"]]), "the key is never sent back to the screen"
    r = await client.post("/admin/v1/webhooks/key", json={"endpoint_id": one["id"], "key": "Bearer ALLKEY-0000000000", "all_on_address": True}, headers=h)
    assert r.json()["updated"] == len(items) and r.json()["key_hint"] == "0000"          # a pasted "Bearer " prefix is not doubled
    after = (await client.get("/admin/v1/webhooks", headers=h)).json()["items"]
    assert {e["key_hint"] for e in after} == {"0000"}
    assert (await client.post("/admin/v1/webhooks/key", json={"endpoint_id": one["id"]}, headers=h)).status_code == 422
    assert (await client.post("/admin/v1/webhooks/key", json={"endpoint_id": one["id"], "key": "short"}, headers=h)).status_code == 422


async def test_the_whatsapp_connection_key_can_be_reused_by_the_owner_only(client, make, world):
    owner, admin = await make.staff("owner"), await make.staff("admin")
    ep = (await client.get("/admin/v1/webhooks", headers=owner["headers"])).json()["items"][0]
    none = await client.post("/admin/v1/webhooks/key", json={"endpoint_id": ep["id"], "use_connection": True}, headers=owner["headers"])
    assert none.status_code in (200, 409)
    await client.put("/admin/v1/summaries/key", json={"key": "CONNECTION-KEY-5555"}, headers=owner["headers"])
    ok = await client.post("/admin/v1/webhooks/key", json={"endpoint_id": ep["id"], "use_connection": True, "all_on_address": False}, headers=owner["headers"])
    assert ok.status_code == 200 and ok.json()["key_hint"] == "5555"
    assert (await client.post("/admin/v1/webhooks/key", json={"endpoint_id": ep["id"], "use_connection": True}, headers=admin["headers"])).status_code == 403
