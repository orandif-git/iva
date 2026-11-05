"""Making reports: the job that builds queued reports, and the scheduler that queues them.

A report is a row (`queued`), picked up by this job (`FOR UPDATE SKIP LOCKED`, so several backend processes can share the work), built from the
tenant's own data, stored as an Excel file next to the evidence pictures, and marked `ready`. A scheduled report is then emailed with the file
attached. A report that fails is marked `failed` with a reason and is never retried by itself; the customer can ask for it again."""
import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..db import db
from ..messaging import evidence
from . import report_builders as rb

log = logging.getLogger("gateinsight.reports")

TYPES = {"gate_register": ("Gate register", 31), "footfall_summary": ("Footfall summary", 366), "vehicle_audit": ("Vehicle audit", 92)}   # label, longest period (days)
LOCK_SECONDS = 600


def local_period(tz_name: str, d_from: date, d_to: date) -> tuple[datetime, datetime]:
    """Whole local days, both ends included: [midnight of d_from, midnight after d_to)."""
    tz = ZoneInfo(tz_name)
    return (datetime.combine(d_from, time.min, tz).astimezone(timezone.utc), datetime.combine(d_to + timedelta(days=1), time.min, tz).astimezone(timezone.utc))


def filename(report_type: str, start: datetime, end: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    a, b = start.astimezone(tz).date(), (end - timedelta(seconds=1)).astimezone(tz).date()
    return f"{TYPES[report_type][0].lower().replace(' ', '_')}_{a:%d-%b-%Y}" + (f"_to_{b:%d-%b-%Y}" if b != a else "") + ".xlsx"


# ----------------------------------------------------------------------------- building
async def _build(conn, r) -> tuple[bytes, int, bool]:
    t = await conn.fetchrow("SELECT timezone FROM app.tenants WHERE id=$1", r["tenant_id"])
    tz, tid, a, b = t["timezone"], r["tenant_id"], r["period_start"], r["period_end"]
    if r["report_type"] == "gate_register":
        rows = await conn.fetch(
            """SELECT p.seen_at, p.plate_text, p.plate_raw, p.vehicle_type, c.name AS gate, p.direction::text AS direction, p.confidence, p.review_status::text AS review_status,
                      p.dwell_seconds, (SELECT v.list_type::text FROM app.vehicle_lists v WHERE v.tenant_id=p.tenant_id AND v.plate_text=p.plate_text ORDER BY v.list_type = 'blocked' DESC LIMIT 1) AS list_type
               FROM app.plate_events p LEFT JOIN app.cameras c ON c.id = p.camera_id
               WHERE p.tenant_id=$1 AND p.seen_at >= $2 AND p.seen_at < $3 ORDER BY p.seen_at LIMIT $4""", tid, a, b, rb.MAX_ROWS + 1)
        cut = len(rows) > rb.MAX_ROWS
        rows = rows[:rb.MAX_ROWS]
        return await asyncio.to_thread(rb.gate_register, rows, tz, cut), len(rows), cut
    if r["report_type"] == "footfall_summary":
        rows = await conn.fetch(
            """SELECT (f.bucket_start AT TIME ZONE $4)::date AS day, extract(hour FROM f.bucket_start AT TIME ZONE $4)::int AS hour, coalesce(c.name, 'Removed camera') AS camera,
                      sum(f.people_in)::int AS people_in, sum(f.people_out)::int AS people_out
               FROM app.footfall_counts f LEFT JOIN app.cameras c ON c.id = f.camera_id
               WHERE f.tenant_id=$1 AND f.bucket_start >= $2 AND f.bucket_start < $3 GROUP BY 1,2,3""", tid, a, b, tz)
        days = max(1, round((b - a).total_seconds() / 86400))
        return await asyncio.to_thread(rb.footfall_summary, rows, tz, days), len(rows), False
    vehicles = await conn.fetch(
        """SELECT p.plate_text, min(p.seen_at) AS first_seen, max(p.seen_at) AS last_seen, count(*) FILTER (WHERE p.direction='in') AS entries, count(*) FILTER (WHERE p.direction='out') AS exits,
                  coalesce(sum(p.dwell_seconds),0) AS dwell_total, count(p.dwell_seconds) AS dwell_n,
                  (SELECT v.list_type::text FROM app.vehicle_lists v WHERE v.tenant_id=p.tenant_id AND v.plate_text=p.plate_text ORDER BY v.list_type = 'blocked' DESC LIMIT 1) AS list_type
           FROM app.plate_events p WHERE p.tenant_id=$1 AND p.seen_at >= $2 AND p.seen_at < $3 AND p.plate_text IS NOT NULL AND p.review_status <> 'needs_review'
           GROUP BY p.plate_text, p.tenant_id ORDER BY max(p.seen_at) DESC LIMIT $4""", tid, a, b, rb.MAX_ROWS)
    inside = await conn.fetch(
        """SELECT i.plate_text, i.seen_at, c.name AS gate, i.vehicle_type, (SELECT v.list_type::text FROM app.vehicle_lists v WHERE v.tenant_id=i.tenant_id AND v.plate_text=i.plate_text ORDER BY v.list_type = 'blocked' DESC LIMIT 1) AS list_type
           FROM app.plate_events i LEFT JOIN app.cameras c ON c.id = i.camera_id
           WHERE i.tenant_id=$1 AND i.seen_at >= $2 AND i.seen_at < $3 AND i.direction='in' AND i.plate_text IS NOT NULL AND i.review_status <> 'needs_review'
             AND NOT EXISTS (SELECT 1 FROM app.plate_events o WHERE o.entry_event_id = i.id) ORDER BY i.seen_at LIMIT $4""", tid, a, b, rb.MAX_ROWS)
    return await asyncio.to_thread(rb.vehicle_audit, vehicles, inside, tz, datetime.now(timezone.utc)), len(vehicles), False


async def build_one() -> bool:
    """Takes one queued report, builds it. Returns False when there was nothing to do."""
    async with db.service_tx() as conn:
        r = await conn.fetchrow(
            """UPDATE app.reports SET status='working', locked_until = now() + make_interval(secs => $1)
               WHERE id = (SELECT id FROM app.reports WHERE (status='queued' OR (status='working' AND locked_until < now())) ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
               RETURNING *""", float(LOCK_SECONDS))
    if not r:
        return False
    try:
        async with db.service_tx() as conn:
            data, rows, cut = await _build(conn, r)
            tz = await conn.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", r["tenant_id"])
        key = f"t/{r['tenant_id']}/reports/{r['id']}.xlsx"
        await evidence.store().put(key, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        async with db.service_tx() as conn:
            await conn.execute("UPDATE app.reports SET status='ready', file_key=$2, bytes=$3, row_count=$4, truncated=$5, generated_at=now(), locked_until=NULL, error=NULL WHERE id=$1",
                               r["id"], key, len(data), rows, cut)
            if r["schedule_id"]:
                await _queue_emails(conn, r["id"], r["schedule_id"], r["tenant_id"])
    except Exception:
        log.exception("report %s failed", r["id"])
        async with db.service_tx() as conn:
            await conn.execute("UPDATE app.reports SET status='failed', error='The report could not be built. Please try again.', locked_until=NULL WHERE id=$1", r["id"])
    return True


async def _queue_emails(conn, report_id, schedule_id, tenant_id) -> int:
    t = await conn.fetchrow("SELECT msg_email FROM app.tenants WHERE id=$1", tenant_id)
    people = await conn.fetch("SELECT r.id, r.email FROM app.recipients r JOIN app.report_schedules s ON r.id = ANY(s.recipients) WHERE s.id=$1 AND r.active AND r.email IS NOT NULL", schedule_id)
    n = 0
    for p in people:
        got = await conn.fetchval(
            """INSERT INTO app.notifications (tenant_id, report_id, recipient_id, channel, recipient, status, error)
               VALUES ($1,$2,$3,'email',$4,$5::app.delivery_status,$6) ON CONFLICT DO NOTHING RETURNING id""",
            tenant_id, report_id, p["id"], p["email"], "queued" if t["msg_email"] else "suppressed", None if t["msg_email"] else "Email messages are switched off for this account.")
        n += bool(got)
    return n


# ----------------------------------------------------------------------------- schedules
def latest_period(sch, now_utc: datetime, tz_name: str) -> tuple[datetime, datetime] | None:
    """The newest period this schedule should already have produced, as (start, end) in UTC, or None when its time has not come yet today."""
    tz = ZoneInfo(tz_name)
    now = now_utc.astimezone(tz)
    at = sch["send_at"]
    midnight = lambda d: datetime.combine(d, time.min, tz)
    if sch["frequency"] == "daily":
        day = now.date() if now.time() >= at else now.date() - timedelta(days=1)         # the report is sent on `day` at send_at and covers the day before
        end = midnight(day)
        return (end - timedelta(days=1)).astimezone(timezone.utc), end.astimezone(timezone.utc)
    if sch["frequency"] == "weekly":
        back = (now.weekday() - sch["weekday"]) % 7
        day = now.date() - timedelta(days=back)
        if back == 0 and now.time() < at:
            day -= timedelta(days=7)
        end = midnight(day)
        return (end - timedelta(days=7)).astimezone(timezone.utc), end.astimezone(timezone.utc)
    first = now.date().replace(day=1)
    due = first.replace(day=sch["day_of_month"])
    if now.date() < due or (now.date() == due and now.time() < at):
        first = (first - timedelta(days=1)).replace(day=1)
    end = midnight(first)
    start = midnight((first - timedelta(days=1)).replace(day=1))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def queue_due(now_utc: datetime | None = None) -> int:
    """Queues every scheduled report whose time has come. Each period is queued once, even after downtime (only the newest missed one)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    queued = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """SELECT s.*, t.timezone, t.retention_events_days, t.status::text AS tenant_status FROM app.report_schedules s JOIN app.tenants t ON t.id = s.tenant_id
               WHERE s.enabled AND t.status::text NOT IN ('suspended','cancelled') FOR UPDATE OF s SKIP LOCKED""")
        for s in rows:
            per = latest_period(s, now_utc, s["timezone"])
            if per is None or (s["last_period_end"] is not None and s["last_period_end"] >= per[1]):
                continue
            await conn.execute(
                """INSERT INTO app.reports (tenant_id, schedule_id, report_type, period_start, period_end, expires_at)
                   VALUES ($1,$2,$3,$4,$5, now() + make_interval(days => $6))""", s["tenant_id"], s["id"], s["report_type"], per[0], per[1], s["retention_events_days"])
            await conn.execute("UPDATE app.report_schedules SET last_period_end=$2 WHERE id=$1", s["id"], per[1])
            queued += 1
    return queued


async def purge_expired() -> int:
    n = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch("SELECT id, file_key FROM app.reports WHERE expires_at < now() LIMIT 200")
        gone = []
        for r in rows:
            try:
                if r["file_key"]:
                    await evidence.store().delete(r["file_key"])
                gone.append(r["id"])
            except Exception:
                log.exception("could not delete report file %s", r["id"])
        if gone:
            await conn.execute("DELETE FROM app.reports WHERE id = ANY($1::uuid[])", gone)
            n = len(gone)
    return n


async def run_forever() -> None:
    last_sched = 0.0
    while True:
        try:
            loop_t = asyncio.get_running_loop().time()
            if loop_t - last_sched >= 60:
                last_sched = loop_t
                await queue_due()
            if not await build_one():
                await asyncio.sleep(3)
        except Exception:
            log.exception("report loop failed")
            await asyncio.sleep(10)
