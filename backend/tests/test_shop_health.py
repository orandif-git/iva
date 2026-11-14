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
