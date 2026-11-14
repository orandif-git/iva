"""Raising an alert, and queueing the messages it causes.

The alert row and its message rows are written in the caller's transaction. If the transaction commits, the messages are safely queued
(outbox pattern); if it rolls back, neither exists. Sending happens later, in the delivery service."""
import re
from datetime import datetime, time, timedelta, timezone
from ..config import get_settings
from uuid import UUID
from zoneinfo import ZoneInfo

from . import alert_catalog
from .alert_catalog import ALERT_TYPES

PER_RECIPIENT_PER_HOUR = 20           # a flood guard for ordinary alerts; a critical alert is never held back by it
# A rule the customer has never touched. Every System Alert that exists is on from the start, and so are blocked vehicles; they appear in the app until recipients are added.
# The daily and weekly summaries are on for everyone (Platform Admin can change that); a number of days of video to keep depends on what the customer wants, so they ask for it.
OPT_IN = {"retention_low"}
DEFAULT_ENABLED = {k for k in alert_catalog.BUILT if ALERT_TYPES[k]["family"] == alert_catalog.SYSTEM and k not in OPT_IN} | {"blocked_plate"}


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


async def default_recipients(conn, tenant_id: UUID) -> list:
    return [r["id"] for r in await conn.fetch("SELECT id FROM app.recipients WHERE tenant_id=$1 AND active AND phone IS NOT NULL", tenant_id)]


async def raise_alert(conn, tenant_id: UUID, rule_type: str, *, body: str = "", title: str | None = None, camera_id=None, camera_name: str = "",
                      details: dict | None = None, dedupe_key: str | None = None, cooldown_minutes: int = 10, evidence_id=None,
                      evidence_key: str | None = None, severity: str | None = None, expect_clip: bool = False, recipient_ids: list | None = None,
                      direct_ready: bool = True) -> UUID | None:
    """Returns the new alert id, or None when nothing was raised (rule off, or the same alert was raised recently).
    `recipient_ids` replaces the rule's recipients (the summaries use it for "the account owner"). `direct_ready` says whether a summary can be sent straight to WhatsApp now."""
    meta = ALERT_TYPES[rule_type]
    if rule_type not in await alert_catalog.visible(conn, tenant_id):      # switched off by the platform, or not part of this customer's plan
        return None
    rule = await conn.fetchrow("SELECT * FROM app.alert_rules WHERE tenant_id=$1 AND rule_type=$2", tenant_id, rule_type)
    if (rule["enabled"] if rule else rule_type in DEFAULT_ENABLED) is False:
        return None
    if dedupe_key:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{tenant_id}:{dedupe_key}")
        if await conn.fetchval("SELECT 1 FROM app.alerts WHERE tenant_id=$1 AND dedupe_key=$2 AND created_at > now() - make_interval(mins => $3)",
                               tenant_id, dedupe_key, cooldown_minutes):
            return None
    tenant = await conn.fetchrow("SELECT timezone, msg_whatsapp, msg_clips FROM app.tenants WHERE id=$1", tenant_id)
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
    level = severity or meta["severity"]
    critical = level == "critical"                     # must never be missed: quiet hours and the flood guard do not hold it
    alert_id = await conn.fetchval(
        """INSERT INTO app.alerts (tenant_id, rule_id, rule_type, camera_id, severity, title, body, details, dedupe_key, evidence_key, evidence_id, clip_wait_until)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id""",
        tenant_id, rule["id"] if rule else None, rule_type, camera_id, level, title, body, {**(details or {}), **payload},
        dedupe_key, evidence_key, evidence_id, wait_until)
    hold = None
    ep = await conn.fetchrow("SELECT active, payload_template FROM platform.webhook_endpoints WHERE alert_type=$1", rule_type)
    if wait_until and ep and ep["active"] and "{clip_url}" in ep["payload_template"]:
        hold = wait_until            # only worth holding the event back when its payload carries the clip
    quiet = (not critical) and bool(rule) and in_quiet_hours(now.astimezone(tz).time(), rule["quiet_start"], rule["quiet_end"])
    if recipient_ids is not None:
        who = recipient_ids
    elif rule and (rule["recipients"] or rule["recipients_set"]):
        who = list(rule["recipients"])
    else:                                              # nobody has chosen yet: everyone of this customer with a mobile number (the summaries are never sent this way)
        who = [] if rule_type in alert_catalog.DIRECT_SEND else await default_recipients(conn, tenant_id)
    if who:
        await fan_out(conn, tenant_id, tenant, alert_id, rule_type, who, payload, quiet, hold_until=hold, critical=critical, direct_ready=direct_ready)
    return alert_id


async def fan_out(conn, tenant_id, tenant, alert_id, alert_type: str, recipient_ids: list[UUID], payload: dict, quiet: bool = False, hold_until=None, critical: bool = False,
                  direct_ready: bool = True) -> int:
    """One queued event per person with a mobile number. Alerts go out by WhatsApp only, as a webhook event (see messaging/webhook.py).
    A message that must not be sent (switched off, quiet hours, too many in an hour, no webhook set up) is still recorded, as suppressed, with the reason.
    There is no monthly limit. A critical alert ignores quiet hours and the hourly flood guard."""
    people = await conn.fetch("SELECT id, name, phone FROM app.recipients WHERE tenant_id=$1 AND active AND id = ANY($2::uuid[])", tenant_id, recipient_ids)
    direct = alert_type in alert_catalog.DIRECT_SEND
    endpoint = direct_ready if direct else await conn.fetchval("SELECT active FROM platform.webhook_endpoints WHERE alert_type=$1", alert_type)
    n = 0
    for p in people:
        if not p["phone"]:
            continue
        why = None
        if not tenant["msg_whatsapp"]:
            why = "WhatsApp messages are switched off for this account."
        elif not endpoint:
            why = "WhatsApp summaries are not set up yet." if direct else "No webhook is set up for this alert yet."
        elif quiet:
            why = "Quiet hours."
        elif not critical and await conn.fetchval("SELECT count(*) FROM app.notifications WHERE recipient_id=$1 AND channel='whatsapp' AND created_at > now() - interval '1 hour' "
                                 "AND status <> 'suppressed'", p["id"]) >= PER_RECIPIENT_PER_HOUR:
            why = "Too many messages to this person in the last hour."
        row = await conn.fetchval(
            """INSERT INTO app.notifications (tenant_id, alert_id, recipient_id, channel, recipient, payload, status, error, next_attempt_at, event)
               VALUES ($1,$2,$3,'whatsapp',$4,$5,$6::app.delivery_status,$7, coalesce($8::timestamptz, now()), $9) ON CONFLICT DO NOTHING RETURNING id""",
            tenant_id, alert_id, p["id"], p["phone"], payload, "suppressed" if why else "queued", why, hold_until, alert_catalog.event_name(alert_type))
        if row:
            n += 1
    return n
