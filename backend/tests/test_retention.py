"""Data retention: each tenant's own periods, batching, the time budget, whole-month partition dropping, and what is never deleted."""
import time
import uuid
from datetime import date, timedelta

import pytest

from app.config import get_settings
from app.messaging import evidence
from app.services import retention
from tests.helpers import setup_cameras

DAY = "interval '1 day'"


def soon():
    return time.monotonic() + 60


async def two_tenants(client, make, su, days_a=7, days_b=90):
    ta, ua, _, (ca,) = await setup_cameras(client, make)
    tb, ub, _, (cb,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.tenants SET retention_events_days=$2 WHERE id=$1", ta, days_a)
    await su.execute("UPDATE app.tenants SET retention_events_days=$2 WHERE id=$1", tb, days_b)
    return (ta, ua, ca), (tb, ub, cb)


async def count(su, table, tenant, extra=""):
    return await su.fetchval(f"SELECT count(*) FROM app.{table} WHERE tenant_id=$1 {extra}", tenant)


async def test_footfall_is_kept_for_each_tenants_own_period(client, make, su):
    (ta, _, ca), (tb, _, cb) = await two_tenants(client, make, su)
    for t, c in ((ta, ca), (tb, cb)):
        for days in (1, 6, 8, 30, 100):
            await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in) VALUES ($1,$2,date_trunc('minute', now()) - make_interval(days => $3), 5)", t, c, days)
    removed = await retention.purge_footfall(soon())
    assert removed >= 4                                                                         # tenant A: 8, 30, 100 days gone; tenant B: only the 100-day one
    ages = lambda t: su.fetch("SELECT round(extract(epoch FROM now() - bucket_start)/86400)::int AS d FROM app.footfall_counts WHERE tenant_id=$1 ORDER BY 1", t)
    assert [r["d"] for r in await ages(ta)] == [1, 6]
    assert [r["d"] for r in await ages(tb)] == [1, 6, 8, 30]


async def test_plate_reads_and_alerts_with_their_message_log_follow_the_same_rule(client, make, su):
    (ta, ua, ca), (tb, ub, cb) = await two_tenants(client, make, su)
    for t, c in ((ta, ca), (tb, cb)):
        for days in (2, 20):
            await su.execute("INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, review_status) VALUES ($1,$2, now() - make_interval(days => $3), 'GJ14AW1013','auto')", t, c, days)
            a = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, created_at) VALUES ($1,'camera_offline','warning','x', now() - make_interval(days => $2)) RETURNING id", t, days)
            r = await su.fetchval("INSERT INTO app.recipients (tenant_id, name, email) VALUES ($1,'R',$2) RETURNING id", t, f"{uuid.uuid4().hex[:6]}@x.test")
            await su.execute("INSERT INTO app.notifications (tenant_id, alert_id, recipient_id, channel, recipient, status) VALUES ($1,$2,$3,'email','a@b.co','sent')", t, a, r)
    assert await retention.purge_plate_reads(soon()) >= 1 and await retention.purge_alerts(soon()) >= 1
    assert (await count(su, "plate_events", ta), await count(su, "alerts", ta), await count(su, "notifications", ta)) == (1, 1, 1)        # the 20-day-old ones are gone, the message log with them
    assert (await count(su, "plate_events", tb), await count(su, "alerts", tb), await count(su, "notifications", tb)) == (2, 2, 2)        # a longer period keeps everything
    assert await count(su, "recipients", ta) == 2                                                                                           # people are never deleted


async def test_loose_old_messages_are_removed(client, make, su):
    (ta, _, _), _ = await two_tenants(client, make, su)
    r = await su.fetchval("INSERT INTO app.recipients (tenant_id, name, email) VALUES ($1,'R','z@x.test') RETURNING id", ta)
    await su.execute("INSERT INTO app.notifications (tenant_id, recipient_id, channel, recipient, status, created_at) VALUES ($1,$2,'email','z@x.test','cancelled', now() - interval '40 days')", ta, r)
    await su.execute("INSERT INTO app.notifications (tenant_id, recipient_id, channel, recipient, status) VALUES ($1,$2,'email','z@x.test','sent')", ta, r)
    assert await retention.purge_loose_notifications(soon()) >= 1
    assert await count(su, "notifications", ta, "AND alert_id IS NULL") == 1


async def test_big_clean_ups_run_in_small_batches_and_finish(client, make, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "retention_batch", 3)
    (ta, _, ca), _ = await two_tenants(client, make, su)
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in) SELECT $1,$2, date_trunc('minute', now()) - interval '40 days' - make_interval(mins => g), 1 FROM generate_series(1,25) g", ta, ca)
    assert await retention.purge_footfall(soon()) == 25                                        # 9 batches of up to 3
    assert await count(su, "footfall_counts", ta) == 0


async def test_a_run_out_of_time_stops_cleanly_and_carries_on_next_time(client, make, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "retention_batch", 4)
    (ta, _, ca), _ = await two_tenants(client, make, su)
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in) SELECT $1,$2, date_trunc('minute', now()) - interval '40 days' - make_interval(mins => g), 1 FROM generate_series(1,10) g", ta, ca)
    assert await retention.purge_footfall(time.monotonic() - 1) == 0                           # no time at all: nothing happens, nothing breaks
    assert await count(su, "footfall_counts", ta) == 10
    assert await retention.purge_footfall(soon()) == 10                                        # the next run finishes the job


async def test_pictures_and_clips_expire_by_date_and_the_files_go_too(client, make, su):
    (ta, _, ca), _ = await two_tenants(client, make, su)
    old, fresh = uuid.uuid4(), uuid.uuid4()
    for eid, ex in ((old, "now() - interval '1 hour'"), (fresh, "now() + interval '1 day'")):
        key = evidence.storage_key(ta, eid, "image")
        await evidence.store().put(key, b"\xff\xd8\xffdata", "image/jpeg")
        await su.execute(f"INSERT INTO app.evidence (id, tenant_id, camera_id, kind, storage_key, content_type, bytes, expires_at) VALUES ($1,$2,$3,'image',$4,'image/jpeg',7,{ex})", eid, ta, ca, key)
    assert await retention.purge_pictures(soon()) >= 1
    assert await evidence.store().get(evidence.storage_key(ta, old, "image")) is None and await evidence.store().get(evidence.storage_key(ta, fresh, "image"))
    assert await su.fetchval("SELECT count(*) FROM app.evidence WHERE id=$1", old) == 0 and await su.fetchval("SELECT count(*) FROM app.evidence WHERE id=$1", fresh) == 1


async def test_a_file_that_cannot_be_deleted_keeps_its_row_for_next_time(client, make, su, monkeypatch):
    (ta, _, ca), _ = await two_tenants(client, make, su)
    eid = uuid.uuid4()
    await su.execute("INSERT INTO app.evidence (id, tenant_id, camera_id, kind, storage_key, content_type, bytes, expires_at) VALUES ($1,$2,$3,'image','k/stuck.jpg','image/jpeg',1, now() - interval '1 hour')", eid, ta, ca)
    real = evidence.store()

    async def broken(key):
        raise RuntimeError("storage down")
    monkeypatch.setattr(real, "delete", broken)
    assert await retention.purge_pictures(soon()) == 0
    assert await su.fetchval("SELECT count(*) FROM app.evidence WHERE id=$1", eid) == 1        # not forgotten: the file may still exist
    monkeypatch.undo()
    assert await retention.purge_pictures(soon()) >= 1 and await su.fetchval("SELECT count(*) FROM app.evidence WHERE id=$1", eid) == 0


async def test_new_pictures_expire_after_the_tenants_own_picture_period(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.tenants SET retention_snapshots_days=3 WHERE id=$1", t)
    up = await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=b"\xff\xd8\xff" + b"x" * 50, headers=make.service())
    assert up.status_code == 201
    days = await su.fetchval("SELECT round(extract(epoch FROM expires_at - now())/86400)::int FROM app.evidence WHERE id=$1", uuid.UUID(up.json()["evidence_id"]))
    assert days == 3


async def test_whole_old_months_of_plate_reads_are_dropped_but_not_recent_or_current_ones(client, make, su):
    (ta, _, ca), _ = await two_tenants(client, make, su)
    old_day, mid_day = date.today() - timedelta(days=3000), date.today() - timedelta(days=400)
    for d in (old_day, mid_day):
        await su.execute("SELECT app.ensure_plate_partition($1::date)", d)
        await su.execute("INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, review_status) VALUES ($1,$2,$3::date + time '10:00','OLD1','auto')", ta, ca, d)
    await su.execute("INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, review_status) VALUES ($1,$2,now(),'NEW1','auto')", ta, ca)
    names = await retention.drop_old_partitions()
    assert f"plate_events_{old_day:%Y_%m}" in names                                           # older than any tenant could keep it
    assert f"plate_events_{mid_day:%Y_%m}" not in names                                       # a tenant may keep 730 days, so 400 days stays
    assert f"plate_events_{date.today():%Y_%m}" not in names and not any("default" in n for n in names)
    assert await su.fetchval("SELECT count(*) FROM app.plate_events WHERE plate_text='NEW1' AND tenant_id=$1", ta) == 1
    assert await su.fetchval("SELECT count(*) FROM app.plate_events WHERE plate_text='OLD1' AND tenant_id=$1", ta) == 1     # only the 400-day-old one remains


async def test_the_partition_function_is_not_open_to_customers(client, make, su):
    from app.db import db
    async with db.tenant_tx(await make.tenant()) as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.fetchval("SELECT app.drop_old_plate_partitions(now()::date)")


async def test_a_run_is_recorded_and_the_audit_log_is_never_cleaned(client, make, su):
    (ta, _, ca), _ = await two_tenants(client, make, su)
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in) VALUES ($1,$2, now() - interval '50 days', 1)", ta, ca)
    await su.execute("INSERT INTO platform.audit_log (actor_type, actor_name, action, ts) VALUES ('staff','x','old.action', now() - interval '3 years')")
    out = await retention.run_all()
    assert out["footfall"] >= 1 and out["ran_out_of_time"] is False and isinstance(out["partitions_dropped"], list)
    run = await su.fetchrow("SELECT job, ok, details FROM platform.job_runs WHERE job='retention' ORDER BY id DESC LIMIT 1")
    assert run["ok"] is True and __import__("json").loads(run["details"])["footfall"] == out["footfall"]
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='old.action'") == 1

