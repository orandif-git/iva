"""The System Alerts that use data we already hold: connection keeps dropping, back to normal, plan limit, message allowance, payment due, new sign-in, daily summary."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import db
from app.messaging import delivery
from app.services import alert_catalog as cat, alerts as alerts_svc, detectors, sweeper
from app.services.auth import device_label
from tests.helpers import setup_cameras
from tests.test_alert_delivery import bsp_server, customer, smtp_server, world  # noqa: F401  (fixtures)
from tests.test_auth import PW, person

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


async def titles(su, t, rule=None):
    rows = await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1 AND ($2::text IS NULL OR rule_type=$2) ORDER BY created_at", t, rule)
    return [r["title"] for r in rows]


def test_every_system_alert_that_uses_existing_data_is_built_and_the_daily_summary_is_opt_in():
    system = {k for k in cat.BUILT if cat.CATALOG[k]["family"] == "system"}
    assert system == {"recorder_offline", "camera_offline", "connection_unstable", "back_to_normal", "plan_limit", "message_allowance", "payment_due", "new_sign_in", "daily_summary"}
    assert len(cat.BUILT) == 13
    assert "daily_summary" not in alerts_svc.DEFAULT_ENABLED and system - {"daily_summary"} <= alerts_svc.DEFAULT_ENABLED
    assert not [k for k in cat.CATALOG if cat.CATALOG[k]["built"] and cat.CATALOG[k]["needs"].startswith(("Reading", "Picture"))]       # nothing that needs a new recorder reading is claimed


def test_a_device_is_described_by_its_browser_and_system_only():
    assert device_label(CHROME_WIN) == "Chrome on Windows" and device_label(SAFARI_IPHONE) == "Safari on iPhone"
    assert device_label("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36") == "Chrome on Android"
    assert device_label(None) == "a new device" and device_label("curl/8.0") == "a new device"


# ------------------------------------------------------------------------------------------ connection history and "back to normal"
async def recorder_of(su, t):
    return await su.fetchrow("SELECT id, device_id FROM app.recorders WHERE tenant_id=$1", t)


async def heartbeat(client, make, device_id):
    return await client.post(f"/internal/v1/recorders/{device_id}/heartbeat", headers=make.service(), json={"ip": "1.2.3.4", "channels": [{"channel_no": 1, "name": "C1"}]})


async def test_a_recorder_that_was_reported_down_is_reported_back_and_its_history_is_kept(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    rec = await recorder_of(su, t)
    await su.execute("UPDATE app.recorders SET last_heartbeat_at = now() - interval '10 minutes' WHERE id=$1", rec["id"])
    assert await sweeper.mark_stale_recorders(90) >= 1
    assert (await heartbeat(client, make, rec["device_id"])).status_code == 200
    assert await titles(su, t, "recorder_offline") == ["Recorder offline"]
    back = await su.fetchrow("SELECT title, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='back_to_normal'", t)
    assert back and back["title"].endswith("is back") and "working again" in back["body"]
    assert [r["status"] for r in await su.fetch("SELECT status FROM app.recorder_link_events WHERE recorder_id=$1 ORDER BY id", rec["id"])] == ["online", "offline", "online"]
    await heartbeat(client, make, rec["device_id"])                                       # still online: nothing new
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='back_to_normal'", t) == 1


async def test_nothing_is_said_about_being_back_when_the_customer_was_never_told_it_was_down(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    await client.put("/v1/alert-types/recorder_offline", json={"enabled": False}, headers=u["headers"])      # they do not want those
    rec = await recorder_of(su, t)
    await su.execute("UPDATE app.recorders SET last_heartbeat_at = now() - interval '10 minutes' WHERE id=$1", rec["id"])
    await sweeper.mark_stale_recorders(90)
    await heartbeat(client, make, rec["device_id"])
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0
    assert await su.fetchval("SELECT count(*) FROM app.recorder_link_events WHERE recorder_id=$1 AND status='offline'", rec["id"]) == 1       # the history is kept anyway


async def test_a_registrar_report_that_the_recorder_dropped_is_recorded(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    rec = await recorder_of(su, t)
    assert (await client.post(f"/internal/v1/recorders/{rec['device_id']}/offline", headers=make.service())).status_code == 204
    assert (await client.post(f"/internal/v1/recorders/{rec['device_id']}/offline", headers=make.service())).status_code == 204        # already offline: no second event
    assert await su.fetchval("SELECT count(*) FROM app.recorder_link_events WHERE recorder_id=$1 AND status='offline'", rec["id"]) == 1


async def test_a_camera_that_was_reported_down_is_reported_back(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make, footfall=0, anpr=1)
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '5 minutes' WHERE id=$1", cam)
    assert await sweeper.mark_stale_cameras(60) >= 1
    assert (await client.post(f"/internal/v1/cameras/{cam}/frame-status", headers=make.service(), json={"state": "streaming", "analysis_fps": 2.0})).status_code == 200
    assert await titles(su, t, "back_to_normal") == ["Cam 1 is back"]
    await client.post(f"/internal/v1/cameras/{cam}/frame-status", headers=make.service(), json={"state": "streaming", "analysis_fps": 2.0})
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='back_to_normal'", t) == 1


# ------------------------------------------------------------------------------------------ connection keeps dropping
async def drops(su, t, rec, n, minutes_ago=10):
    for _ in range(n):
        await su.execute("INSERT INTO app.recorder_link_events (tenant_id, recorder_id, status, at) VALUES ($1,$2,'offline', now() - make_interval(mins => $3))", t, rec, minutes_ago)


async def test_a_connection_that_keeps_dropping_is_reported_once_and_the_number_can_be_changed(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    t2, u2, _, _ = await setup_cameras(client, make)
    rec, rec2 = (await recorder_of(su, t))["id"], (await recorder_of(su, t2))["id"]
    await client.put("/v1/alert-types/connection_unstable", json={"enabled": True, "params": {"drops": 8}}, headers=u2["headers"])
    await drops(su, t, rec, 5); await drops(su, t2, rec2, 5)
    await detectors.connection_unstable()
    assert await titles(su, t, "connection_unstable") == ["Connection keeps dropping"]               # five drops reaches the usual limit of five
    assert await titles(su, t2, "connection_unstable") == []                                          # this customer asked for eight
    row = await su.fetchrow("SELECT body, details::text AS details FROM app.alerts WHERE tenant_id=$1 AND rule_type='connection_unstable'", t)
    assert "5 times" in row["body"] and '"drops": 5' in row["details"]
    await detectors.connection_unstable()
    assert len(await titles(su, t, "connection_unstable")) == 1                                       # not again for six hours


async def test_old_drops_do_not_count(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    rec = (await recorder_of(su, t))["id"]
    await drops(su, t, rec, 6, minutes_ago=120)
    await detectors.connection_unstable()
    assert await titles(su, t, "connection_unstable") == []


# ------------------------------------------------------------------------------------------ plan limit, message allowance, payment
async def test_the_plan_limit_alert_comes_at_ninety_percent_and_once_a_month(client, make, su):
    t = await make.tenant(limit=10)
    (await setup_cameras(client, make, footfall=5, anpr=4, tenant=t, limit=10))
    t2 = await make.tenant(limit=10)
    _, u2, _, _ = await setup_cameras(client, make, footfall=5, anpr=4, tenant=t2, limit=10)
    t3 = await make.tenant(limit=10)
    await setup_cameras(client, make, footfall=2, anpr=1, tenant=t3, limit=10)
    await client.put("/v1/alert-types/plan_limit", json={"enabled": True, "params": {"percent": 100}}, headers=u2["headers"])
    await detectors.plan_limit()
    assert await titles(su, t, "plan_limit") == ["Camera limit nearly reached"]                       # 9 of 10
    row = await su.fetchrow("SELECT body, details::text AS details FROM app.alerts WHERE tenant_id=$1 AND rule_type='plan_limit'", t)
    assert "9 of the 10 cameras" in row["body"] and '"used": 9' in row["details"] and '"limit": 10' in row["details"]
    assert await titles(su, t2, "plan_limit") == [] and await titles(su, t3, "plan_limit") == []       # the one who asked for 100%, and the one with room
    await detectors.plan_limit()
    assert len(await titles(su, t, "plan_limit")) == 1


async def fake_messages(su, t, n):
    for i in range(n):
        await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status) VALUES ($1,'whatsapp','+919876543210','sent')", t)


async def test_the_message_allowance_alert_comes_at_eighty_percent_for_a_customer_with_a_cap(client, make, su):
    t, t2, t3 = await make.tenant(), await make.tenant(), await make.tenant()
    await su.execute("UPDATE app.tenants SET msg_monthly_cap=10 WHERE id = ANY($1::uuid[])", [t, t2])
    await fake_messages(su, t, 8); await fake_messages(su, t2, 7); await fake_messages(su, t3, 50)        # t3 has no cap
    await detectors.message_allowance()
    assert await titles(su, t, "message_allowance") == ["Message allowance nearly used"]
    assert "8 of your 10" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='message_allowance'", t)
    assert await titles(su, t2, "message_allowance") == [] and await titles(su, t3, "message_allowance") == []
    await detectors.message_allowance()
    assert len(await titles(su, t, "message_allowance")) == 1


async def invoice(su, t, due_in_days, status="issued"):
    number = "INV-" + uuid.uuid4().hex[:6].upper()
    await su.execute(
        """INSERT INTO app.invoices (tenant_id, period_month, number, status, subtotal_paise, total_paise, tax_rate, issued_at, due_at)
           VALUES ($1, date_trunc('month', now() - make_interval(days => $4))::date, $2, $5, 1000, 1180, 18, now(), now() + make_interval(days => $3))""", t, number, due_in_days, abs(due_in_days) * 40, status)
    return number


async def test_a_payment_alert_comes_when_an_invoice_is_due_soon_and_again_when_it_is_overdue(client, make, su):
    soon, late, far, paid = await make.tenant(), await make.tenant(), await make.tenant(), await make.tenant()
    n_soon, n_late = await invoice(su, soon, 2), await invoice(su, late, -1)
    await invoice(su, far, 10); await invoice(su, paid, 1, status="paid")
    await detectors.payment_due()
    assert await titles(su, soon, "payment_due") == ["Payment due soon"] and await titles(su, late, "payment_due") == ["Payment overdue"]
    assert await titles(su, far, "payment_due") == [] and await titles(su, paid, "payment_due") == []
    body = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='payment_due'", soon)
    assert n_soon in body and "₹12" in body and "is due on" in body
    assert "was due on" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='payment_due'", late) and n_late
    await detectors.payment_due()
    assert len(await titles(su, soon, "payment_due")) == 1 and len(await titles(su, late, "payment_due")) == 1


# ------------------------------------------------------------------------------------------ new sign-in
async def test_a_sign_in_from_a_new_phone_or_computer_is_reported_but_the_first_one_is_not(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)

    async def sign_in(ua):
        r = await client.post("/v1/auth/login", json={"email": email, "password": PW}, headers={"User-Agent": ua})
        assert r.status_code == 200, r.text
    await sign_in(CHROME_WIN)
    assert await titles(su, t, "new_sign_in") == []                                                   # the first device is just the start
    await sign_in(CHROME_WIN)
    assert await titles(su, t, "new_sign_in") == []                                                   # the same one again
    await sign_in(SAFARI_IPHONE)
    assert await titles(su, t, "new_sign_in") == ["New sign-in on your account"]
    row = await su.fetchrow("SELECT body, details::text AS details FROM app.alerts WHERE tenant_id=$1 AND rule_type='new_sign_in'", t)
    assert "Safari on iPhone" in row["body"] and '"device": "Safari on iPhone"' in row["details"]
    await sign_in(SAFARI_IPHONE)
    assert len(await titles(su, t, "new_sign_in")) == 1
    assert await su.fetchval("SELECT count(*) FROM app.user_devices WHERE user_id=$1", uid) == 2
    assert await su.fetchval("SELECT length(fingerprint) FROM app.user_devices WHERE user_id=$1 LIMIT 1", uid) == 32              # only a short fingerprint is kept


async def test_a_wrong_password_leaves_no_trace_of_a_device(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)
    r = await client.post("/v1/auth/login", json={"email": email, "password": "not-the-password"}, headers={"User-Agent": CHROME_WIN})
    assert r.status_code == 401
    assert await su.fetchval("SELECT count(*) FROM app.user_devices WHERE user_id=$1", uid) == 0


async def test_one_person_is_not_confused_with_another_in_the_same_company(client, make, su):
    t = await make.tenant()
    (_, e1), (_, e2) = await person(su, t), await person(su, t)
    await client.post("/v1/auth/login", json={"email": e1, "password": PW}, headers={"User-Agent": CHROME_WIN})
    await client.post("/v1/auth/login", json={"email": e2, "password": PW}, headers={"User-Agent": SAFARI_IPHONE})        # a different person's first device
    assert await titles(su, t, "new_sign_in") == []


# ------------------------------------------------------------------------------------------ daily summary
async def test_the_daily_summary_comes_once_a_day_at_the_chosen_hour_and_only_when_asked_for(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    other, _, _, _ = await setup_cameras(client, make)                                                # never asked for it
    tz = ZoneInfo(await su.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", t))
    now_hour = datetime.now(tz).hour
    await client.put("/v1/alert-types/daily_summary", json={"enabled": True, "params": {"hour": (now_hour + 5) % 24}}, headers=u["headers"])
    await detectors.daily_summary()
    assert await titles(su, t, "daily_summary") == []                                                 # not the hour yet
    await client.put("/v1/alert-types/daily_summary", json={"enabled": True, "params": {"hour": now_hour}}, headers=u["headers"])
    await detectors.daily_summary()
    assert await titles(su, t, "daily_summary") == ["Daily health summary"] and await titles(su, other, "daily_summary") == []
    text = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t)
    assert "cameras working" in text and "recorders online" in text and "alerts in the last 24 hours" in text
    await detectors.daily_summary()
    assert len(await titles(su, t, "daily_summary")) == 1


# ------------------------------------------------------------------------------------------ the new alerts reach a webhook with their own shortcodes
async def test_a_payment_alert_reaches_the_webhook_with_the_invoice_and_due_date(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="payment_due")
    ep = next(e for e in (await client.get("/admin/v1/webhooks", headers=world["admin"])).json()["items"] if e["alert_type"] == "payment_due")
    assert {"invoice", "due_date"} <= set(ep["shortcodes"])
    r = await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"payload_template": '{"to": "{to_phone}", "invoice": "{invoice}", "due": "{due_date}", "text": "{details}"}'}, headers=world["admin"])
    assert r.status_code == 200, r.text
    number = await invoice(su, t, 2)
    await detectors.payment_due()
    assert await delivery.run_once() == 1
    call = world["hook"].calls[-1]
    assert call["headers"]["x-event"] == "system.payment_due" and call["body"]["invoice"] == number and call["body"]["to"] == "919876543210" and call["body"]["due"] and number in call["body"]["text"]
