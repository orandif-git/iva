"""Alerts that come from looking at the data over time rather than from a single event: vehicles that stay too long, and crowding."""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
                                     details={"plate": r["plate_text"], "hours": f"{hours:g}"}, dedupe_key=f"overstay:{r['id']}", cooldown_minutes=60 * 24 * 7):
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


# ------------------------------------------------------------------------------------------------ System Alerts that watch the account and the devices
async def _param(conn, tenant_id, rule_type: str, key: str, default):
    r = await conn.fetchval("SELECT params FROM app.alert_rules WHERE tenant_id=$1 AND rule_type=$2", tenant_id, rule_type)
    return (r or {}).get(key, default)


async def connection_unstable() -> int:
    """A recorder that went offline `drops` times in the last hour (a customer can change the number). At most one alert every six hours for it."""
    n = 0
    async with db.service_tx() as conn:
        await conn.execute("DELETE FROM app.recorder_link_events WHERE at < now() - interval '14 days'")
        await conn.execute("DELETE FROM app.zone_samples WHERE at < now() - interval '3 days'")
        rows = await conn.fetch(
            """SELECT e.tenant_id, e.recorder_id, r.device_id, r.name, count(*)::int AS drops FROM app.recorder_link_events e JOIN app.recorders r ON r.id = e.recorder_id
               WHERE e.status='offline' AND e.at > now() - interval '1 hour' GROUP BY 1,2,3,4 HAVING count(*) >= 2""")
        for r in rows:
            if r["drops"] < int(await _param(conn, r["tenant_id"], "connection_unstable", "drops", 5)):
                continue
            name = r["name"] or r["device_id"]
            if await raise_alert(conn, r["tenant_id"], "connection_unstable", body=f"{name} went offline {r['drops']} times in the last hour. Check its internet connection and network cable.",
                                 camera_name=r["device_id"], details={"recorder": name, "drops": r["drops"]}, dedupe_key=f"unstable:{r['recorder_id']}", cooldown_minutes=360):
                n += 1
    return n


async def plan_limit() -> int:
    """The customer is using `percent` of the cameras their plan allows (90 unless they changed it). Once a month at most."""
    n = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """SELECT t.id, t.camera_limit, (SELECT count(*) FROM app.cameras c WHERE c.tenant_id = t.id AND c.enabled)::int AS used FROM app.tenants t
               WHERE t.status::text IN ('trial','active','past_due') AND t.camera_limit > 0""")
        for r in rows:
            pct = int(await _param(conn, r["id"], "plan_limit", "percent", 90)) if r["used"] else 100
            if r["used"] * 100 >= r["camera_limit"] * pct and await raise_alert(
                    conn, r["id"], "plan_limit", title="Camera limit nearly reached", body=f"You are using {r['used']} of the {r['camera_limit']} cameras your plan allows.",
                    camera_name="Your account", details={"used": r["used"], "limit": r["camera_limit"]}, dedupe_key=f"planlimit:{r['camera_limit']}", cooldown_minutes=60 * 24 * 30):
                n += 1
    return n


async def message_allowance() -> int:
    """The customer has used `percent` of this month's WhatsApp messages (80 unless they changed it). Only for a customer who has a monthly cap."""
    n = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """SELECT t.id, t.msg_monthly_cap AS cap, t.timezone, (SELECT count(*) FROM app.notifications n WHERE n.tenant_id = t.id AND n.channel='whatsapp'
                      AND n.status NOT IN ('suppressed','cancelled') AND n.created_at >= date_trunc('month', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone)::int AS used
               FROM app.tenants t WHERE t.msg_monthly_cap > 0 AND t.status::text IN ('trial','active','past_due')""")
        for r in rows:
            pct = int(await _param(conn, r["id"], "message_allowance", "percent", 80))
            if r["used"] * 100 >= r["cap"] * pct:
                month = datetime.now(ZoneInfo(r["timezone"])).strftime("%Y-%m")
                if await raise_alert(conn, r["id"], "message_allowance", title="Message allowance nearly used", body=f"You have used {r['used']} of your {r['cap']} WhatsApp messages this month.",
                                     camera_name="Your account", details={"used": r["used"], "limit": r["cap"]}, dedupe_key=f"msgcap:{month}", cooldown_minutes=60 * 24 * 31):
                    n += 1
    return n


async def payment_due() -> int:
    """An issued invoice that is due within `days` (3 unless changed), and again once it is overdue. Paid and void invoices are never reported."""
    n = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch(
            """SELECT i.id, i.tenant_id, i.number, i.due_at, i.total_paise, t.timezone FROM app.invoices i JOIN app.tenants t ON t.id = i.tenant_id
               WHERE i.status='issued' AND i.due_at IS NOT NULL AND i.due_at < now() + interval '14 days' AND t.status::text <> 'cancelled'""")
        now = datetime.now(timezone.utc)
        for r in rows:
            days = int(await _param(conn, r["tenant_id"], "payment_due", "days", 3))
            overdue = r["due_at"] <= now
            if not overdue and r["due_at"] > now + timedelta(days=days):
                continue
            due = r["due_at"].astimezone(ZoneInfo(r["timezone"])).strftime("%d %b")
            amount = f"₹{r['total_paise'] / 100:,.0f}"
            title = "Payment overdue" if overdue else "Payment due soon"
            body = f"Invoice {r['number']} of {amount} {'was due on' if overdue else 'is due on'} {due}."
            if await raise_alert(conn, r["tenant_id"], "payment_due", title=title, body=body, camera_name="Your account", details={"invoice": r["number"], "due_date": due},
                                 dedupe_key=f"invoice_{'overdue' if overdue else 'due'}:{r['id']}", cooldown_minutes=60 * 24 * 30):
                n += 1
    return n


async def daily_summary() -> int:
    """One short message a day, at the hour the customer chose (8 unless changed), for a customer who has turned it on."""
    n = 0
    async with db.service_tx() as conn:
        rules = await conn.fetch("SELECT r.tenant_id, r.params, t.timezone FROM app.alert_rules r JOIN app.tenants t ON t.id = r.tenant_id "
                                 "WHERE r.rule_type='daily_summary' AND r.enabled AND t.status::text IN ('trial','active','past_due')")
        for rule in rules:
            local = datetime.now(ZoneInfo(rule["timezone"]))
            if local.hour != int((rule["params"] or {}).get("hour", 8)):
                continue
            s = await conn.fetchrow(
                """SELECT (SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled)::int AS cams,
                          (SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled AND stream_state='streaming')::int AS streaming,
                          (SELECT count(*) FROM app.recorders WHERE tenant_id=$1)::int AS recs, (SELECT count(*) FROM app.recorders WHERE tenant_id=$1 AND status='online')::int AS online,
                          (SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type <> 'daily_summary' AND created_at > now() - interval '24 hours')::int AS alerts,
                          (SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type <> 'daily_summary' AND acknowledged_at IS NULL)::int AS unseen""", rule["tenant_id"])
            text = (f"{s['streaming']} of {s['cams']} cameras working, {s['online']} of {s['recs']} recorders online. "
                    f"{s['alerts']} alert{'' if s['alerts'] == 1 else 's'} in the last 24 hours, {s['unseen']} not yet seen.")
            if await raise_alert(conn, rule["tenant_id"], "daily_summary", title="Daily health summary", body=text, camera_name="All cameras", details={"summary": text},
                                 dedupe_key=f"daily:{local.date()}", cooldown_minutes=20 * 60):
                n += 1
    return n
