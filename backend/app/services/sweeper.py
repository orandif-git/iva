import asyncio
import logging

from ..config import get_settings
from ..db import db
from . import link_history
from .alerts import raise_alert

log = logging.getLogger("gateinsight.sweeper")


async def mark_stale_recorders(timeout_seconds: int) -> int:
    """A recorder that has not sent a heartbeat for `timeout_seconds` is offline."""
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """UPDATE app.recorders SET status='offline'
               WHERE status='online' AND last_heartbeat_at < now() - make_interval(secs => $1)
               RETURNING id, tenant_id, device_id""", float(timeout_seconds))
        for r in rows:
            await link_history.record(conn, r["tenant_id"], r["id"], "offline")
            await raise_alert(conn, r["tenant_id"], "recorder_offline", body=f"Recorder {r['device_id']} stopped contacting us.", camera_name=r["device_id"], details={"recorder": r["device_id"]},
                              dedupe_key=f"recorder_offline:{r['id']}", cooldown_minutes=60)
    return len(rows)


async def mark_stale_cameras(timeout_seconds: int) -> int:
    """A camera whose worker has not reported for `timeout_seconds` is offline (the worker reports every 15 s)."""
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """UPDATE app.cameras SET stream_state='offline', stream_state_at=now(), analysis_fps=NULL,
                      stream_error = coalesce(stream_error, 'The analysis worker stopped reporting.')
               WHERE stream_state IN ('streaming','reconnecting') AND stream_seen_at < now() - make_interval(secs => $1)
               RETURNING id, tenant_id, name""", float(timeout_seconds))
        for r in rows:
            await raise_alert(conn, r["tenant_id"], "camera_offline", body="No video is being received.", camera_id=r["id"], camera_name=r["name"],
                              dedupe_key=f"camera_offline:{r['id']}", cooldown_minutes=60)
    return len(rows)


async def run_forever() -> None:
    s = get_settings()
    while True:
        try:
            n = await mark_stale_recorders(s.heartbeat_timeout_seconds)
            if n:
                log.info("marked %d recorders offline", n)
            m = await mark_stale_cameras(60)
            if m:
                log.info("marked %d cameras offline", m)
        except Exception:
            log.exception("sweeper failed")
        await asyncio.sleep(s.sweep_interval_seconds)
