"""Alerts that come from looking at the data over time rather than from a single event: vehicles that stay too long, and crowding."""
import logging

from ..db import db
from .alerts import raise_alert

log = logging.getLogger("gateinsight.detectors")


async def vehicle_overstay() -> int:
    """A vehicle that entered more than `max_hours` ago and has no matching exit. One alert per visit."""
    n = 0
    async with db.service_tx() as conn:
        rules = await conn.fetch("SELECT tenant_id, params FROM app.alert_rules WHERE rule_type='vehicle_overstay' AND enabled")
        for rule in rules:
            hours = float((rule["params"] or {}).get("max_hours", 8))
            rows = await conn.fetch(
                """SELECT i.id, i.plate_text, i.seen_at, i.camera_id, c.name AS camera FROM app.plate_events i JOIN app.cameras c ON c.id=i.camera_id
                   WHERE i.tenant_id=$1 AND i.direction='in' AND i.plate_text IS NOT NULL AND i.review_status <> 'needs_review'
                     AND i.seen_at < now() - make_interval(hours => $2) AND i.seen_at > now() - interval '7 days'
                     AND NOT EXISTS (SELECT 1 FROM app.plate_events o WHERE o.entry_event_id = i.id)
                   ORDER BY i.seen_at LIMIT 50""", rule["tenant_id"], hours)
            for r in rows:
                if await raise_alert(conn, rule["tenant_id"], "vehicle_overstay", title=f"Vehicle {r['plate_text']} has stayed too long",
                                     body=f"Entered at {r['camera']} and has not left after {hours:g} hours.", camera_id=r["camera_id"], camera_name=r["camera"],
                                     details={"plate": r["plate_text"]}, dedupe_key=f"overstay:{r['id']}", cooldown_minutes=60 * 24 * 7):
                    n += 1
    return n


async def crowding() -> int:
    """People counted in minus out today (the tenant's day), across all cameras, at or above the limit. At most one alert an hour.
    This is an estimate: a person missed at the door is never corrected, so counts drift. It is meant as a warning, not a head count."""
    n = 0
    async with db.service_tx() as conn:
        rules = await conn.fetch("SELECT r.tenant_id, r.params, t.timezone FROM app.alert_rules r JOIN app.tenants t ON t.id=r.tenant_id WHERE r.rule_type='crowding' AND r.enabled")
        for rule in rules:
            limit = int((rule["params"] or {}).get("people", 50))
            inside = await conn.fetchval(
                """SELECT greatest(coalesce(sum(people_in) - sum(people_out), 0), 0) FROM app.footfall_counts
                   WHERE tenant_id=$1 AND bucket_start >= date_trunc('day', now() AT TIME ZONE $2) AT TIME ZONE $2""", rule["tenant_id"], rule["timezone"])
            if inside >= limit and await raise_alert(conn, rule["tenant_id"], "crowding", body=f"About {inside} people are inside. Your limit is {limit}.",
                                                     camera_name="All cameras", details={"people": int(inside), "limit": limit}, dedupe_key="crowding", cooldown_minutes=60):
                n += 1
    return n
