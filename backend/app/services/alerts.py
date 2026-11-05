"""Raising an alert, and queueing the messages it causes.

The alert row and its message rows are written in the caller's transaction. If the transaction commits, the messages are safely queued
(outbox pattern); if it rolls back, neither exists. Sending happens later, in the delivery service."""
import re
from datetime import datetime, time, timedelta, timezone
from ..config import get_settings
from uuid import UUID
from zoneinfo import ZoneInfo

from ..messaging.defaults import ALERT_TYPES

PER_RECIPIENT_PER_HOUR = 20
# A rule the customer has never touched. Only blocked vehicles are on from the start, and they appear in the app until recipients are added.
DEFAULT_ENABLED = {"blocked_plate"}


def in_quiet_hours(now_local: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    return start <= now_local < end if start < end else (now_local >= start or now_local < end)


def clean_text(s, limit: int = 160) -> str:
    """WhatsApp rejects variables with line breaks, tabs or long runs of spaces, so we flatten them."""
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit] or "-"


def clips_wanted(tenant, rule) -> bool:
    """Clips are made for blocked-vehicle alerts when the account allows them and the customer has not switched them off.
    The camera config the worker reads and the alert the backend raises both use this, so they always agree."""
    if not tenant["msg_clips"]:
        return False
    if rule is None:
        return True
    return bool(rule["enabled"]) and (rule["params"] or {}).get("clip", True) is not False


async def raise_alert(conn, tenant_id: UUID, rule_type: str, *, body: str = "", title: str | None = None, camera_id=None, camera_name: str = "",
                      details: dict | None = None, dedupe_key: str | None = None, cooldown_minutes: int = 10, evidence_id=None,
                      evidence_key: str | None = None, severity: str | None = None, expect_clip: bool = False) -> UUID | None:
    """Returns the new alert id, or None when nothing was raised (rule off, or the same alert was raised recently)."""
    meta = ALERT_TYPES[rule_type]
    rule = await conn.fetchrow("SELECT * FROM app.alert_rules WHERE tenant_id=$1 AND rule_type=$2", tenant_id, rule_type)
    if (rule["enabled"] if rule else rule_type in DEFAULT_ENABLED) is False:
        return None
    if dedupe_key:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{tenant_id}:{dedupe_key}")
        if await conn.fetchval("SELECT 1 FROM app.alerts WHERE tenant_id=$1 AND dedupe_key=$2 AND created_at > now() - make_interval(mins => $3)",
                               tenant_id, dedupe_key, cooldown_minutes):
            return None
    tenant = await conn.fetchrow("SELECT timezone, msg_whatsapp, msg_email, msg_clips, msg_monthly_cap FROM app.tenants WHERE id=$1", tenant_id)
    tz = ZoneInfo(tenant["timezone"])
    now = datetime.now(timezone.utc)
    title = title or meta["title"]
    payload = {"event": clean_text(title, 100), "location": clean_text(camera_name, 80), "time": now.astimezone(tz).strftime("%d %b, %I:%M %p"),
               "details": clean_text(body, 200)}
    if evidence_id is not None:        # only evidence that really belongs to this tenant can be attached
        if not await conn.fetchval("SELECT 1 FROM app.evidence WHERE id=$1 AND tenant_id=$2", evidence_id, tenant_id):
            evidence_id = None
    wait_until = None
    if expect_clip and clips_wanted(tenant, rule):
        wait_until = now + timedelta(seconds=get_settings().clip_wait_seconds)
    alert_id = await conn.fetchval(
        """INSERT INTO app.alerts (tenant_id, rule_id, rule_type, camera_id, severity, title, body, details, dedupe_key, evidence_key, evidence_id, clip_wait_until)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id""",
        tenant_id, rule["id"] if rule else None, rule_type, camera_id, severity or meta["severity"], title, body, {**(details or {}), **payload},
        dedupe_key, evidence_key, evidence_id, wait_until)
    hold = None
    if wait_until and await conn.fetchval("SELECT 1 FROM platform.message_templates WHERE channel='whatsapp' AND key='alert_clip' AND status='approved'"):
        hold = wait_until            # only worth holding the WhatsApp message back when a video template can carry the clip
    if rule and rule["recipients"] and rule["channels"]:
        quiet = in_quiet_hours(now.astimezone(tz).time(), rule["quiet_start"], rule["quiet_end"])
        await fan_out(conn, tenant_id, tenant, alert_id, list(rule["channels"]), list(rule["recipients"]), payload, quiet, hold_until=hold)
    return alert_id


async def fan_out(conn, tenant_id, tenant, alert_id, channels: list[str], recipient_ids: list[UUID], payload: dict, quiet: bool = False, hold_until=None) -> int:
    people = await conn.fetch("SELECT id, name, phone, email FROM app.recipients WHERE tenant_id=$1 AND active AND id = ANY($2::uuid[])", tenant_id, recipient_ids)
    used = await conn.fetchval(
        """SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND channel='whatsapp' AND status NOT IN ('suppressed','cancelled')
           AND created_at >= date_trunc('month', now() AT TIME ZONE $2) AT TIME ZONE $2""", tenant_id, tenant["timezone"])
    n = 0
    for p in people:
        for ch in channels:
            addr = p["phone"] if ch == "whatsapp" else p["email"] if ch == "email" else None
            if not addr:
                continue
            why = None
            if (ch == "whatsapp" and not tenant["msg_whatsapp"]) or (ch == "email" and not tenant["msg_email"]):
                why = f"{ch.title()} messages are switched off for this account."
            elif quiet:
                why = "Quiet hours."
            elif ch == "whatsapp" and tenant["msg_monthly_cap"] is not None and used >= tenant["msg_monthly_cap"]:
                why = "The monthly WhatsApp message limit has been reached."
            elif await conn.fetchval("SELECT count(*) FROM app.notifications WHERE recipient_id=$1 AND channel=$2::app.channel_kind AND created_at > now() - interval '1 hour' "
                                     "AND status <> 'suppressed'", p["id"], ch) >= PER_RECIPIENT_PER_HOUR:
                why = "Too many messages to this person in the last hour."
            row = await conn.fetchval(
                """INSERT INTO app.notifications (tenant_id, alert_id, recipient_id, channel, recipient, payload, status, error, next_attempt_at)
                   VALUES ($1,$2,$3,$4::app.channel_kind,$5,$6,$7::app.delivery_status,$8, coalesce($9::timestamptz, now())) ON CONFLICT DO NOTHING RETURNING id""",
                tenant_id, alert_id, p["id"], ch, addr, payload, "suppressed" if why else "queued", why, hold_until if ch == "whatsapp" else None)
            if row:
                n += 1
                if ch == "whatsapp" and not why:
                    used += 1
    return n
