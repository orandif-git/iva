"""The Health page of a customer in the Control Center: recorders, cameras, the last week's problems and the messages of the last day."""
import json
from datetime import datetime, timedelta, timezone

from tests.helpers import setup_cameras


async def shop(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, footfall=1, anpr=0)
    staff = await make.staff("support")
    return t, cam, rec, staff


def url(t):
    return f"/admin/v1/tenants/{t}/shop/health"


async def test_every_console_user_can_read_it_and_a_stranger_cannot(client, make, su):
    t, cam, rec, staff = await shop(client, make, su)
    for role in ("owner", "admin", "onboarding", "support"):
        s = await make.staff(role)
        assert (await client.get(url(t), headers=s["headers"])).status_code == 200, role
    assert (await client.get(url(t))).status_code == 401


async def test_a_working_camera_and_an_online_recorder_are_healthy(client, make, su):
    t, cam, rec, staff = await shop(client, make, su)
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at=now() WHERE tenant_id=$1", t)
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now(), last_frame_at=now() WHERE id=$1", cam)
    h = (await client.get(url(t), headers=staff["headers"])).json()
    assert h["overall"] == "ok" and h["issues"] == [] and h["counts"]["cameras_working"] == 1 and h["counts"]["recorders_online"] == 1
    assert len(h["days"]) == 7 and h["cameras"][0]["working"] is True and h["recorders"][0]["level"] == "ok"


async def test_an_offline_recorder_a_silent_camera_a_failed_disk_and_a_wrong_clock_are_named(client, make, su):
    t, cam, rec, staff = await shop(client, make, su)
    health = {"disks": [{"num": 0, "total_mb": 1000, "free_mb": 100, "status": 2, "state": 2}], "clock_out_minutes": 9, "oldest_recording": "2026-10-12T16:25:43", "days_held": 33}
    await su.execute("UPDATE app.recorders SET status='offline', health=$2 WHERE tenant_id=$1", t, json.dumps(health))
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now() - interval '20 minutes' WHERE id=$1", cam)
    h = (await client.get(url(t), headers=staff["headers"])).json()
    text = " | ".join(i["text"] for i in h["issues"])
    assert h["overall"] == "crit" and "is offline" in text and "has failed" in text and "9 minutes out" in text
    r = h["recorders"][0]
    assert r["oldest_recording"] == "2026-10-12T16:25:43" and r["days_held"] == 33 and r["disks"]["state"] == "fault" and r["clock_out_minutes"] == 9
    assert h["cameras"][0]["working"] is False and h["cameras"][0]["recorder_offline"] is True


async def test_a_picture_problem_and_stuck_messages_are_reported_and_test_alerts_are_not_counted(client, make, su):
    t, cam, rec, staff = await shop(client, make, su)
    await su.execute("UPDATE app.recorders SET status='online' WHERE tenant_id=$1", t)
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now() WHERE id=$1", cam)
    await su.execute("INSERT INTO app.camera_picture_state (camera_id, tenant_id, bad_view_kind, bad_view_since) VALUES ($1,$2,'covered', now())", cam, t)
    await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status, created_at) VALUES ($1,'whatsapp','+919876543210','queued', now() - interval '30 minutes')", t)
    for details in ({}, {"test": True}):
        await su.execute("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, details) VALUES ($1,'recorder_offline','warning','x','y',$2)", t, json.dumps(details))
    h = (await client.get(url(t), headers=staff["headers"])).json()
    text = " | ".join(i["text"] for i in h["issues"])
    assert "is covered" in text and "waiting for more than 10 minutes" in text and h["overall"] == "warn"
    assert sum(d["problems"] for d in h["days"]) == 1, "a test alert is not a problem"


async def test_the_recorders_window_has_the_disks_hardware_channels_and_the_last_days_connection_history(client, make, su):
    t, cam, rec, staff = await shop(client, make, su)
    health = {"disks": [{"num": 0, "total_mb": 4_000_000, "free_mb": 2_000_000, "status": 1, "state": 1, "life_percent": 12.0, "model": "WD Purple", "serial": "WCC7K3"}], "uptime_seconds": 3600,
              "hardware": {"temperature_c": 41.5, "fan_ok": True, "poe_fault": False}, "clock": {"local": "2026-11-14T11:04:00"}, "recording_now": {"1": True}, "channel_online": {"1": True},
              "oldest_recording": "2026-10-12T16:25:43", "days_held": 33}
    await su.execute("UPDATE app.recorders SET status='online', firmware='V4.001', last_ip='157.51.122.232', health=$2 WHERE tenant_id=$1", t, json.dumps(health))
    rid = await su.fetchval("SELECT id FROM app.recorders WHERE tenant_id=$1", t)
    await su.execute("INSERT INTO app.recorder_channels (recorder_id, tenant_id, channel_no, name) VALUES ($1,$2,1,'Gate') ON CONFLICT (recorder_id, channel_no) DO UPDATE SET name='Gate'", rid, t)
    now = datetime.now(timezone.utc)
    await su.execute("DELETE FROM app.recorder_link_events WHERE recorder_id=$1", rid)           # the helper's own first connection
    for status, ago in (("offline", 180), ("online", 177), ("offline", 40)):
        await su.execute("INSERT INTO app.recorder_link_events (tenant_id, recorder_id, status, at) VALUES ($1,$2,$3,$4)", t, rid, status, now - timedelta(minutes=ago))
    d = (await client.get(url(t), headers=staff["headers"])).json()["recorders"][0]["detail"]
    assert d["firmware"] == "V4.001" and d["address"] == "157.51.122.232" and d["uptime_seconds"] == 3600 and d["temperature_c"] == 41.5 and d["fan_ok"] is True
    assert d["disks"][0]["model"] == "WD Purple" and d["disks"][0]["state"] == "wearing" and d["disks"][0]["total_mb"] == 4_000_000
    assert d["channels"][0]["online"] is True and d["channels"][0]["recording"] is True and d["channels"][0]["name"] == "Gate"
    lines = [h["text"] for h in d["history"]]
    assert lines == ["Went offline. Not back yet.", "Went offline, back online after 3 minutes."], lines
    empty = (await client.get(url(await make.tenant()), headers=staff["headers"])).json()
    assert empty["recorders"] == []
