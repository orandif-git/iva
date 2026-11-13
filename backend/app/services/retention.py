"""Deleting old data. Each tenant keeps its events (footfall, plate reads, alerts, message log) for `retention_events_days` and its
pictures and clips for `retention_snapshots_days`; both are set in the admin console. Cleaning up is done in small batches so it never
holds a table locked, and a run that runs out of time simply carries on at the next run.

Not touched on purpose: the platform audit log (append-only, kept for accountability) and the tenant's own settings and people."""
import logging
import time

from ..config import get_settings
from ..db import db
from ..messaging import evidence

log = logging.getLogger("gateinsight.retention")

# Every tenant keeps events at most this long (the admin console limit), so older whole months can simply be dropped.
MAX_RETENTION_DAYS = 730


async def _batched(sql: str, deadline: float, *args) -> int:
    """Runs a DELETE ... WHERE ctid IN (SELECT ... LIMIT n) again and again, each in its own short transaction."""
    total = 0
    while time.monotonic() < deadline:
        async with db.service_tx() as conn:
            n = int((await conn.execute(sql, *args, get_settings().retention_batch)).split()[-1])
        total += n
        if n < get_settings().retention_batch:
            break
    return total


async def purge_footfall(deadline: float) -> int:
    return await _batched(
        """DELETE FROM app.footfall_counts WHERE ctid IN (
             SELECT f.ctid FROM app.footfall_counts f JOIN app.tenants t ON t.id = f.tenant_id
             WHERE f.bucket_start < now() - make_interval(days => t.retention_events_days) LIMIT $1)""", deadline)


async def purge_plate_reads(deadline: float) -> int:
    return await _batched(
        """DELETE FROM app.plate_events WHERE (id, seen_at) IN (
             SELECT p.id, p.seen_at FROM app.plate_events p JOIN app.tenants t ON t.id = p.tenant_id
             WHERE p.seen_at < now() - make_interval(days => t.retention_events_days) LIMIT $1)""", deadline)


async def purge_alerts(deadline: float) -> int:
    """Alerts and, with them, the message log (which holds phone numbers and email addresses)."""
    return await _batched(
        """DELETE FROM app.alerts WHERE ctid IN (
             SELECT a.ctid FROM app.alerts a JOIN app.tenants t ON t.id = a.tenant_id
             WHERE a.created_at < now() - make_interval(days => t.retention_events_days)
               AND NOT EXISTS (SELECT 1 FROM app.notifications q WHERE q.alert_id = a.id AND q.status = 'queued') LIMIT $1)""", deadline)


async def purge_loose_notifications(deadline: float) -> int:
    """Messages with no alert (none today, but a deleted alert's cancelled stragglers must not linger)."""
    return await _batched(
        """DELETE FROM app.notifications WHERE ctid IN (
             SELECT n.ctid FROM app.notifications n JOIN app.tenants t ON t.id = n.tenant_id
             WHERE n.alert_id IS NULL AND n.created_at < now() - make_interval(days => t.retention_events_days) LIMIT $1)""", deadline)


async def purge_pictures(deadline: float) -> int:
    """Pictures and clips past their date: the file first, then the row. A file that cannot be deleted keeps its row and is tried again."""
    total = 0
    while time.monotonic() < deadline:
        async with db.service_tx() as conn:
            rows = await conn.fetch("SELECT id, storage_key FROM app.evidence WHERE expires_at < now() ORDER BY expires_at LIMIT 200")
            gone = []
            for r in rows:
                try:
                    await evidence.store().delete(r["storage_key"])
                    gone.append(r["id"])
                except Exception:
                    log.exception("could not delete stored file for evidence %s", r["id"])
            if gone:
                await conn.execute("DELETE FROM app.evidence WHERE id = ANY($1::uuid[])", gone)
        total += len(gone)
        if len(rows) < 200 or not gone:
            break
    return total


async def drop_old_partitions() -> list[str]:
    async with db.service_tx() as conn:
        return list(await conn.fetchval("SELECT app.drop_old_plate_partitions((now() - make_interval(days => $1))::date)", MAX_RETENTION_DAYS))


async def run_all() -> dict:
    """One full clean-up. Returns what was removed, and records the run so the admin console can show it."""
    s = get_settings()
    started = time.monotonic()
    deadline = started + s.retention_seconds
    out: dict = {}
    ok = True
    for name, job in (("pictures_and_clips", purge_pictures), ("footfall", purge_footfall), ("plate_reads", purge_plate_reads),
                      ("alerts_and_messages", purge_alerts), ("loose_messages", purge_loose_notifications)):
        try:
            out[name] = await job(deadline)
        except Exception:
            log.exception("retention step %s failed", name)
            out[name] = "failed"
            ok = False
    try:
        from . import reports
        out["reports"] = await reports.purge_expired()
    except Exception:
        log.exception("purging reports failed")
        out["reports"] = "failed"
        ok = False
    try:
        out["partitions_dropped"] = await drop_old_partitions()
    except Exception:
        log.exception("dropping old partitions failed")
        out["partitions_dropped"] = "failed"
        ok = False
    out["ran_out_of_time"] = time.monotonic() >= deadline
    secs = round(time.monotonic() - started, 2)
    async with db.service_tx() as conn:
        await conn.execute("INSERT INTO platform.job_runs (job, seconds, ok, details) VALUES ('retention', $1, $2, $3)", secs, ok, out)
    if any(isinstance(v, int) and v for v in out.values()):
        log.info("retention removed %s in %.1fs", {k: v for k, v in out.items() if v}, secs)
    return out
