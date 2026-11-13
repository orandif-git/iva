"""The System health page: who may see it, what it says when things are fine and when they are not, and the saved samples."""
from datetime import datetime, timedelta, timezone

from app.services import system_health as sh
from tests.helpers import setup_cameras


async def test_only_the_owner_and_admin_may_open_it(client, make):
    for role, code in (("owner", 200), ("admin", 200), ("onboarding", 403), ("support", 403)):
        staff = await make.staff(role)
        assert (await client.get("/admin/v1/system/health", headers=staff["headers"])).status_code == code, role
    assert (await client.get("/admin/v1/system/health")).status_code == 401


async def test_a_worker_that_checked_in_and_cameras_that_stream_are_healthy(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", footfall=1)
    await su.execute("INSERT INTO platform.worker_seen (shard, of) VALUES (0,1) ON CONFLICT (shard, of) DO UPDATE SET seen_at=now()")
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now(), analysis_fps=4 WHERE id=$1", cam)
    staff = await make.staff("owner")
    r = (await client.get("/admin/v1/system/health", headers=staff["headers"])).json()
    assert r["cameras"]["streaming"] >= 1 and r["cameras"]["expected"] >= r["cameras"]["streaming"]
    assert cam not in [x.get("id") for x in r["cameras"]["silent"]]
    svc = {s["name"]: s for s in r["services"]}
    assert svc["Analysis worker"]["level"] == "ok" and svc["Database"]["level"] == "ok"
    assert {"disk_pct", "mem_pct", "load_pct", "cpus"} <= set(r["server"])
    assert r["storage"]["database_mb"] > 0 and r["storage"]["biggest"]
    assert len(r["messages"]["per_hour"]) == 24


async def test_a_silent_worker_and_a_silent_camera_are_named(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", footfall=1)
    await su.execute("DELETE FROM platform.worker_seen")
    await su.execute("INSERT INTO platform.worker_seen (shard, of, seen_at) VALUES (0,1, now() - interval '20 minutes')")
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now() - interval '30 minutes' WHERE id=$1", cam)
    staff = await make.staff("owner")
    r = (await client.get("/admin/v1/system/health", headers=staff["headers"])).json()
    assert {s["name"]: s["level"] for s in r["services"]}["Analysis worker"] == "crit"
    name = await su.fetchval("SELECT name FROM app.cameras WHERE id=$1", cam)
    assert name in [x["camera"] for x in r["cameras"]["silent"]]
    assert r["overall"] == "crit"
    assert any("Analysis worker" in x["text"] for x in r["todo"]) and any("sent nothing for 10 minutes" in x["text"] for x in r["todo"])


async def test_messages_that_wait_too_long_are_flagged(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", footfall=1)
    staff = await make.staff("owner")
    await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status, created_at, next_attempt_at) VALUES ($1,'whatsapp','+919800000001','queued', now() - interval '40 minutes', now() - interval '39 minutes')", t)
    m = (await client.get("/admin/v1/system/health", headers=staff["headers"])).json()["messages"]
    assert m["waiting"] == 1 and m["oldest_waiting_minutes"] >= 39 and m["level"] == "crit"


def test_limits_and_levels():
    assert sh.level(50, "disk") == "ok" and sh.level(80, "disk") == "warn" and sh.level(95, "disk") == "crit"
    assert sh.worst("ok", "warn") == "warn" and sh.worst("warn", "crit", "ok") == "crit" and sh.worst() == "ok"


async def test_days_left_follows_the_pace_of_the_last_two_days(client, make, su):
    await su.execute("DELETE FROM platform.health_samples")
    now = datetime.now(timezone.utc)
    for i in range(48):                                        # one sample an hour, 1 per cent every 4 hours
        await su.execute("INSERT INTO platform.health_samples (ts, disk_pct) VALUES ($1,$2)", now - timedelta(hours=48 - i), 40 + i * 0.25)
    from app.db import db
    async with db.admin_tx() as conn:
        days = await sh.disk_days_left(conn, 52)
    assert 7 < days < 9, days                                  # 48 per cent left at 6 per cent a day
    await su.execute("DELETE FROM platform.health_samples")
    async with db.admin_tx() as conn:
        assert await sh.disk_days_left(conn, 52) is None       # no history, no promise


async def test_a_sample_is_saved_and_old_ones_are_removed(su):
    await su.execute("DELETE FROM platform.health_samples")
    await su.execute("INSERT INTO platform.health_samples (ts, disk_pct) VALUES (now() - interval '10 days', 40)")
    await sh.take_sample()
    rows = await su.fetch("SELECT disk_pct, ts FROM platform.health_samples")
    assert len(rows) == 1 and rows[0]["disk_pct"] > 0
