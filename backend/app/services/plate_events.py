"""Plate event logic shared by ingestion (workers) and review (customers)."""
from datetime import datetime, timedelta

MATCH_WINDOW = timedelta(days=7)


async def find_entry(conn, tenant_id, plate: str, seen_at: datetime):
    """The most recent unmatched 'in' read of this plate before `seen_at`. Each entry can be matched by only one exit."""
    return await conn.fetchrow(
        """SELECT i.id, i.seen_at FROM app.plate_events i
           WHERE i.tenant_id=$1 AND i.plate_text=$2 AND i.direction='in' AND i.seen_at <= $3 AND i.seen_at > $4
             AND NOT EXISTS (SELECT 1 FROM app.plate_events o WHERE o.entry_event_id = i.id)
           ORDER BY i.seen_at DESC LIMIT 1""", tenant_id, plate, seen_at, seen_at - MATCH_WINDOW)


async def alert_if_blocked(conn, tenant_id, plate: str, camera_id, camera_name: str, direction, snapshot_key, evidence_id=None, expect_clip: bool = False):
    """Create one critical alert when a blocked plate is read. The same plate alerts at most once per 10 minutes. Returns the alert id, or None."""
    if not await conn.fetchval("SELECT 1 FROM app.vehicle_lists WHERE tenant_id=$1 AND plate_text=$2 AND list_type='blocked'", tenant_id, plate):
        return None
    from .alerts import raise_alert
    return await raise_alert(conn, tenant_id, "blocked_plate", title=f"Blocked vehicle {plate}", body=f"Seen at {camera_name} ({direction or 'direction unknown'}).",
                             camera_id=camera_id, camera_name=camera_name, details={"plate": plate, "direction": direction}, dedupe_key=f"blocked:{plate}",
                             cooldown_minutes=10, evidence_id=evidence_id, evidence_key=snapshot_key, expect_clip=expect_clip)
