import asyncio
import logging

from ..config import get_settings
from ..db import db
from . import link_history, platform_fault
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
    """A camera that has not been reported on for `timeout_seconds` is offline (its worker reports every 15 s), and the customer is told.

    Two cases are NOT the camera's fault and are handled differently:
      our own analysis worker has stopped   the camera is left as it was and nothing is said to the customer; a platform fault is opened and our staff are told
      the camera's recorder is offline      the camera is marked offline, but the customer already has "Recorder offline": one alert, not one for each camera"""
    tell = None
    async with db.service_tx() as conn:
        of, silent = await platform_fault.silent_shards(conn)
        held = 0
        if silent:
            held = await conn.fetchval(
                """SELECT count(*) FROM app.cameras c WHERE c.stream_state IN ('streaming','reconnecting') AND c.stream_seen_at < now() - make_interval(secs => $1)
                     AND abs(hashtext(c.id::text)::bigint) % $2 = ANY($3::int[])""", float(timeout_seconds), of, silent)
        tell = await platform_fault.track(conn, held, silent)
        rows = await conn.fetch(
            """UPDATE app.cameras c SET stream_state='offline', stream_state_at=now(), analysis_fps=NULL,
                      stream_error = coalesce(c.stream_error, 'No video is being received from this camera.')
               WHERE c.stream_state IN ('streaming','reconnecting') AND c.stream_seen_at < now() - make_interval(secs => $1)
                 AND NOT ($2 > 0 AND abs(hashtext(c.id::text)::bigint) % greatest($2, 1) = ANY($3::int[]))
               RETURNING c.id, c.tenant_id, c.name, (SELECT r.status::text FROM app.recorders r WHERE r.id = c.recorder_id) AS recorder_status""",
            float(timeout_seconds), of, silent)
        for r in rows:
            if r["recorder_status"] == "offline":
                continue
            await raise_alert(conn, r["tenant_id"], "camera_offline", body="No video is being received.", camera_id=r["id"], camera_name=r["name"],
                              dedupe_key=f"camera_offline:{r['id']}", cooldown_minutes=60)
    if tell:
        await platform_fault.tell_staff(*tell)
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
