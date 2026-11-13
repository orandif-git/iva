"""The System Alerts that use data we already hold: connection keeps dropping, back to normal, plan limit, message allowance, payment due, new sign-in, daily summary."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import db
from app.messaging import delivery
from app.services import alert_catalog as cat, alerts as alerts_svc, detectors, sweeper
from tests.helpers import setup_cameras
from tests.test_alert_delivery import bsp_server, customer, world  # noqa: F401  (fixtures)


CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


async def titles(su, t, rule=None):
    rows = await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1 AND ($2::text IS NULL OR rule_type=$2) ORDER BY created_at", t, rule)
    return [r["title"] for r in rows]


def test_every_system_alert_that_uses_existing_data_is_built_and_the_summaries_are_on_by_default():
    system = {k for k in cat.BUILT if cat.CATALOG[k]["family"] == "system"}
    assert system == {"recorder_offline", "camera_offline", "connection_unstable", "back_to_normal", "counting_stopped", "daily_summary", "weekly_summary", "clock_wrong", "storage_full", "disk_fault", "recording_stopped", "view_changed", "picture_poor", "recorder_restarted", "hardware_fault", "disk_wear", "retention_low", "service_ended"}
    assert len(cat.BUILT) == 27                                                                       # the three area alerts are Smart Alerts, in test_area_alerts.py
    assert alerts_svc.OPT_IN == {"retention_low"} and not alerts_svc.OPT_IN & alerts_svc.DEFAULT_ENABLED and system - alerts_svc.OPT_IN <= alerts_svc.DEFAULT_ENABLED
    assert all(v["built"] for v in cat.CATALOG.values()), "every alert in the catalogue is built: the review is complete"


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
    assert back and back["title"].startswith("Service restored: ") and "working again" in back["body"]
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
    assert await titles(su, t, "back_to_normal") == ["Service restored: Cam 1"]
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
    assert await titles(su, t, "connection_unstable") == ["Unstable connection"]               # five drops reaches the usual limit of five
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
async def fake_messages(su, t, n):
    for i in range(n):
        await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status) VALUES ($1,'whatsapp','+919876543210','sent')", t)


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
    assert await titles(su, t, "daily_summary") == ["Daily system summary"] and await titles(su, other, "daily_summary") == []
    text = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t)
    assert "cameras working" in text and "recorders online" in text and "alerts in the last 24 hours" in text
    await detectors.daily_summary()
    assert len(await titles(su, t, "daily_summary")) == 1


# ------------------------------------------------------------------------------------------ the new alerts reach a webhook with their own shortcodes


async def test_the_occupancy_and_after_hours_pictures_get_boxes_and_stay_plain_when_the_detector_is_missing():
    import cv2
    import numpy as np
    from app.services import annotate
    img = np.zeros((360, 640, 3), np.uint8)
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    out = annotate.draw(jpeg, [(100, 80, 200, 300)], ((0.1, 0.5), (0.9, 0.5), (0, 1)), "2 in view")
    pic = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert pic[80:300, 100].max() > 100 and pic[180, 320].max() > 100, "a person's box and the counting line are drawn"
    annotate._detector = None
    annotate._load = lambda: (_ for _ in ()).throw(RuntimeError("no model"))
    assert await annotate.people_picture(jpeg) == jpeg, "no detector: the plain picture goes out and the alert is not held back"
