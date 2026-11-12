"""Recorder health, round two: disk wear, days of video kept, restarts, hardware, the recorder's own alarms, why a camera is offline, and the daily summary."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services import detectors, sweeper
from tests.helpers import setup_cameras
from tests.test_recorder_health import disk, recorder, report, titles


async def on(client, u, kind, **params):
    r = await client.put(f"/v1/alert-types/{kind}", json={"enabled": True, "params": params}, headers=u["headers"])
    assert r.status_code == 200, r.text


async def test_a_disk_that_is_wearing_out_is_reported_while_it_still_works(client, make, su):
    t, dev = await recorder(client, make, su)
    worn = {"num": 1, "total_mb": 4_000_000, "free_mb": 3_000_000, "status": 1, "state": 1, "life_percent": 12.0}
    assert await report(client, make, dev, {"disks": [{**disk(0), "life_percent": 80.0}]}) == [], "a healthy disk"
    assert await report(client, make, dev, {"disks": [{**disk(0), "life_percent": 80.0}, worn]}) == ["disk_wear"]
    a = await su.fetchrow("SELECT title, body, severity FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"] == "Recorder disk health low" and "Disk 2" in a["body"] and "12% of its life left" in a["body"] and a["severity"] == "warning"
    assert await report(client, make, dev, {"disks": [{**worn, "state": 2}]}) == ["disk_fault"], "a disk that has already failed is a failure, not a warning"


async def test_days_of_video_kept_are_checked_against_the_number_the_customer_chose(client, make, su):
    t, dev = await recorder(client, make, su)
    u = await make.user(t)
    assert await report(client, make, dev, {"days_held": 6}) == [], "nobody has asked: a recorder that keeps a week is not wrong"
    await on(client, u, "retention_low", days=30)
    assert await report(client, make, dev, {"days_held": 45}) == []
    assert await report(client, make, dev, {"days_held": 6}) == ["retention_low"]
    a = await su.fetchrow("SELECT title, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='retention_low'", t)
    assert a["title"] == "Recording retention below target" and "about 6 days" in a["body"] and "You wanted 30" in a["body"]


async def test_a_restart_is_noticed_from_the_uptime_going_back_and_three_in_a_day_is_a_power_problem(client, make, su):
    t, dev = await recorder(client, make, su)
    assert await report(client, make, dev, {"uptime_seconds": 500_000}) == []
    assert await report(client, make, dev, {"uptime_seconds": 500_600}) == [], "just running on"
    assert await report(client, make, dev, {"uptime_seconds": 300}) == ["recorder_restarted"]
    assert "restarted. If this happens again" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='recorder_restarted'", t)
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    assert await report(client, make, dev, {"uptime_seconds": 900}) == []
    assert await report(client, make, dev, {"uptime_seconds": 100}) == ["recorder_restarted"]
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await report(client, make, dev, {"uptime_seconds": 700})
    assert await report(client, make, dev, {"uptime_seconds": 50}) == ["recorder_restarted"]
    assert "3 times in the last day" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='recorder_restarted'", t) and "UPS" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1", t)


async def test_heat_a_stopped_fan_or_a_failed_power_port_is_a_hardware_warning(client, make, su):
    t, dev = await recorder(client, make, su)
    assert await report(client, make, dev, {"hardware": {"temperature_c": 55.0, "fan_ok": True}}) == []
    assert await report(client, make, dev, {"hardware": {"temperature_c": 78.0, "fan_ok": False, "poe_fault": None}}) == ["hardware_fault"]
    b = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='hardware_fault'", t)
    assert "78" in b and "a fan has stopped" in b


async def test_the_recorders_own_alarms_become_alerts_at_once(client, make, su):
    t, dev = await recorder(client, make, su)
    cam = await su.fetchval("SELECT id FROM app.cameras WHERE tenant_id=$1 AND channel_no=1", t)

    async def alarm(*events):
        r = await client.post(f"/internal/v1/recorders/{dev}/alarms", json={"events": list(events)}, headers=make.service())
        assert r.status_code == 200, r.text
        return r.json()["raised"]

    assert await alarm({"kind": "disk_error", "disk": 0}) == ["disk_fault"]
    assert await alarm({"kind": "storage_low", "percent": 95}) == ["storage_full"]
    assert await alarm({"kind": "video_tamper", "channel": 1}) == ["view_changed"]
    assert "Camera covered" in await su.fetchval("SELECT title FROM app.alerts WHERE tenant_id=$1 AND rule_type='view_changed'", t)
    assert await alarm({"kind": "video_loss", "channel": 1}) == ["camera_offline"]
    assert await alarm({"kind": "video_loss", "channel": 9}) == [], "a channel that is not a camera of this customer"
    assert await alarm({"kind": "reboot"}) == ["recorder_restarted"]
    assert await alarm({"kind": "disk_error", "disk": 0}) == [], "the same thing is not said twice in a day"
    assert (await client.post(f"/internal/v1/recorders/{dev}/alarms", json={"events": [{"kind": "nonsense"}]}, headers=make.service())).status_code == 422
    assert (await client.post(f"/internal/v1/recorders/{dev}/alarms", json={"events": [{"kind": "reboot"}]})).status_code in (401, 403)
    assert cam is not None


async def test_when_the_recorder_cannot_see_a_camera_the_alert_says_so(client, make, su):
    await su.execute("TRUNCATE platform.worker_seen")
    t, dev = await recorder(client, make, su)
    cam = await su.fetchval("SELECT id FROM app.cameras WHERE tenant_id=$1 AND channel_no=1", t)
    await report(client, make, dev, {"channel_online": {"1": False}})
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at = now() - interval '10 minutes' WHERE id=$1", cam)
    await client.get("/internal/v1/workers/cameras", headers=make.service())
    assert await sweeper.mark_stale_cameras(60) >= 1
    body = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='camera_offline'", t)
    assert "lost" in body and "power and its cable" in body
    await su.execute("UPDATE app.cameras SET stream_state='offline' WHERE id=$1", cam)


async def test_the_daily_summary_says_how_full_the_disks_are_and_how_many_days_are_kept(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    dev = await su.fetchval("SELECT device_id FROM app.recorders WHERE tenant_id=$1", t)
    await report(client, make, dev, {"disks": [disk(total=1000, free=0)], "days_held": 21})
    hour = datetime.now(ZoneInfo(await su.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", t))).hour
    await on(client, u, "daily_summary", hour=hour)
    await detectors.daily_summary()
    assert "Video kept: 21 days" in await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='daily_summary'", t)


async def test_a_customer_sees_what_the_recorder_last_said_about_itself(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    dev = await su.fetchval("SELECT device_id FROM app.recorders WHERE tenant_id=$1", t)
    await report(client, make, dev, {"disks": [disk(total=1000, free=250)], "days_held": 14, "uptime_seconds": 86400})
    (r,) = (await client.get("/v1/recorders", headers=u["headers"])).json()
    assert r["health"]["percent_full"] == 75 and r["health"]["days_held"] == 14 and r["health"]["disks"][0]["total_mb"] == 1000 and r["health_at"] is not None
    staff = (await make.staff("support"))["headers"]
    mine = [x for x in (await client.get("/admin/v1/recorders", headers=staff)).json() if x["device_id"] == dev]
    assert mine and mine[0]["health"]["percent_full"] == 75


async def test_a_channel_that_recorded_steadily_and_then_stops_is_noticed_in_twenty_minutes(client, make, su):
    t, dev = await recorder(client, make, su)

    async def reading(on):
        return await report(client, make, dev, {"recording_now": {"1": on, "2": True}})

    for _ in range(4):
        assert await reading(True) == []
    assert await reading(False) == [], "one reading without recording could be a gap"
    assert await reading(False) == ["recording_stopped"], "two in a row after a steady hour: it has stopped"
    a = await su.fetchrow("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='recording_stopped'", t)
    assert "1 camera" in a["body"]


async def test_a_channel_that_only_records_on_motion_is_never_judged_by_the_moment(client, make, su):
    t, dev = await recorder(client, make, su)
    for on in (True, False, False, True, False, False, False, True, False, False, False, False):
        assert await report(client, make, dev, {"recording_now": {"1": on}}) == []


async def test_a_disks_make_and_serial_number_are_kept_for_staff(client, make, su):
    t, dev = await recorder(client, make, su)
    await report(client, make, dev, {"disks": [{**disk(0), "model": "WDCWD43PURZ", "serial": "WX72D43MN17T"}]})
    (r,) = (await client.get("/v1/recorders", headers=(await make.user(t))["headers"])).json()
    assert r["health"]["disks"][0]["model"] == "WDCWD43PURZ" and r["health"]["disks"][0]["serial"] == "WX72D43MN17T"
