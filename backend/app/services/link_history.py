"""When a recorder or camera goes down and comes back. Feeds "connection keeps dropping" and "back to normal".

Going offline is already reported as an alert (recorder offline, camera offline). This keeps the history of the link, so a connection that keeps
dropping can be seen, and tells the customer when something they were warned about is working again."""
from uuid import UUID

from .alerts import raise_alert


async def record(conn, tenant_id: UUID, recorder_id: UUID, status: str) -> None:
    await conn.execute("INSERT INTO app.recorder_link_events (tenant_id, recorder_id, status) VALUES ($1,$2,$3)", tenant_id, recorder_id, status)


async def recovered(conn, tenant_id: UUID, *, offline_rule: str, entity_id: UUID, name: str, camera_id: UUID | None = None):
    """Tells the customer it is back, but only if they were told it was down in the last week (a long outage still ends with good news). Returns the new alert id, or None."""
    base = f"{offline_rule}:{entity_id}"
    # The alert about this recorder or camera may carry its own ending (recorder_offline:<id>:<time> for one outage, :still for a reminder), so all of them count.
    told = await conn.fetchval("SELECT id FROM app.alerts WHERE tenant_id=$1 AND (dedupe_key=$2 OR starts_with(dedupe_key, $3)) AND created_at > now() - interval '7 days' ORDER BY created_at DESC LIMIT 1",
                               tenant_id, base, base + ":")
    if not told:
        return None
    return await raise_alert(conn, tenant_id, "back_to_normal", title=f"Service restored: {name}", body=f"{name} is working again.", camera_id=camera_id, camera_name=name,
                             details={"what_recovered": name}, dedupe_key=f"back:{told}", cooldown_minutes=24 * 60)
