"""What a customer controls about alerts: which kinds are on, who gets them, how, and when. Plus the alert list and the delivery log."""
import re
from datetime import datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field, field_validator

from ..config import get_settings
from ..db import db
from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..messaging import evidence
from ..services import alert_catalog, rules
from ..services.alert_catalog import ALERT_TYPES
from ..schemas import Strict
from ..services.alerts import DEFAULT_ENABLED

router = APIRouter(prefix="/v1", tags=["customer: alerts"])
Channel = Literal["whatsapp"]            # alerts go out by WhatsApp only


def normalize_phone(raw: str) -> str:
    d = re.sub(r"[\s\-().]", "", raw)
    if re.fullmatch(r"[6-9]\d{9}", d):
        return "+91" + d
    if re.fullmatch(r"0[6-9]\d{9}", d):
        return "+91" + d[1:]
    if re.fullmatch(r"91[6-9]\d{9}", d):
        return "+" + d
    if re.fullmatch(r"\+[1-9]\d{7,14}", d):
        return d
    raise ApiError(422, "invalid_phone", "Enter a mobile number like 98765 43210, or with the country code like +91 98765 43210.")


def mask_phone(p: str | None) -> str | None:
    return None if not p else p[:3] + "•" * max(len(p) - 7, 0) + p[-4:]


# --------------------------------------------------------------------------------- recipients
class RecipientIn(Strict):
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=120)
    consent_confirmed: bool = False

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("enter a valid email address")
        return v


class RecipientPatch(Strict):
    name: str | None = Field(None, min_length=1, max_length=80)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=120)
    active: bool | None = None
    consent_confirmed: bool = False


_R = "id, name, phone, email, active, consent_confirmed_at, created_at"


def _recipient(r) -> dict:
    return {"id": r["id"], "name": r["name"], "phone": r["phone"], "phone_masked": mask_phone(r["phone"]), "email": r["email"], "active": r["active"],
            "consent_confirmed_at": r["consent_confirmed_at"]}


@router.get("/recipients")
async def list_recipients(ctx: CustomerCtx = Depends(customer_ctx())):
    return [_recipient(r) for r in await ctx.conn.fetch(f"SELECT {_R} FROM app.recipients ORDER BY name, created_at")]


@router.post("/recipients", status_code=201)
async def add_recipient(body: RecipientIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    phone = normalize_phone(body.phone) if body.phone and body.phone.strip() else None
    if not phone and not body.email:
        raise ApiError(422, "contact_needed", "Enter a mobile number, an email address, or both.")
    if phone and not body.consent_confirmed:
        raise ApiError(422, "consent_required", "Please confirm that this person has agreed to receive WhatsApp messages from us.")
    if phone and await ctx.conn.fetchval("SELECT 1 FROM app.recipients WHERE phone=$1", phone):
        raise ApiError(409, "phone_exists", "This number is already added.")
    row = await ctx.conn.fetchrow(
        f"""INSERT INTO app.recipients (tenant_id, name, phone, email, consent_confirmed_by, consent_confirmed_at)
            VALUES ($1,$2,$3,$4,$5, CASE WHEN $3::text IS NULL THEN NULL ELSE now() END) RETURNING {_R}""",
        ctx.tenant_id, body.name.strip(), phone, body.email, ctx.user_id if phone else None)
    await ctx.audit("recipient.create", "recipient", row["id"], {"name": body.name, "has_phone": bool(phone), "has_email": bool(body.email), "consent_confirmed": bool(phone)})
    return _recipient(row)


@router.patch("/recipients/{rid}")
async def patch_recipient(rid: UUID, body: RecipientPatch, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    cur = await ctx.conn.fetchrow("SELECT * FROM app.recipients WHERE id=$1", rid)
    if not cur:
        raise ApiError(404, "recipient_not_found", "Person not found.")
    fields = body.model_fields_set - {"consent_confirmed"}
    new = {k: getattr(body, k) for k in fields}
    phone = cur["phone"]
    if "phone" in fields:
        phone = normalize_phone(new["phone"]) if new["phone"] and new["phone"].strip() else None
    email = new["email"] if "email" in fields else cur["email"]
    if email is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(422, "invalid_email", "Enter a valid email address.")
    if not phone and not email:
        raise ApiError(422, "contact_needed", "A person needs a mobile number or an email address.")
    consent_by, consent_at = cur["consent_confirmed_by"], cur["consent_confirmed_at"]
    if phone and phone != cur["phone"]:       # a different number is a different person to ask: consent is confirmed again
        if not body.consent_confirmed:
            raise ApiError(422, "consent_required", "Please confirm that this person has agreed to receive WhatsApp messages from us.")
        consent_by, consent_at = ctx.user_id, datetime.now(timezone.utc)
    if not phone:
        consent_by = consent_at = None
    if phone and phone != cur["phone"] and await ctx.conn.fetchval("SELECT 1 FROM app.recipients WHERE phone=$1 AND id<>$2", phone, rid):
        raise ApiError(409, "phone_exists", "This number is already added.")
    row = await ctx.conn.fetchrow(
        f"UPDATE app.recipients SET name=$2, phone=$3, email=$4, active=$5, consent_confirmed_by=$6, consent_confirmed_at=$7 WHERE id=$1 RETURNING {_R}",
        rid, new.get("name", cur["name"]), phone, email, new.get("active", cur["active"]) if new.get("active") is not None else cur["active"], consent_by, consent_at)
    await ctx.audit("recipient.update", "recipient", rid, {"fields": sorted(fields), "consent_reconfirmed": phone != cur["phone"] and bool(phone)})
    return _recipient(row)


@router.delete("/recipients/{rid}", status_code=204)
async def delete_recipient(rid: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if not await ctx.conn.fetchval("SELECT 1 FROM app.recipients WHERE id=$1", rid):
        raise ApiError(404, "recipient_not_found", "Person not found.")
    # Cancel first: deleting the person clears recipient_id on their messages, after which they could no longer be found.
    await ctx.conn.execute("UPDATE app.notifications SET status='cancelled', error='The person was removed.', locked_until=NULL WHERE recipient_id=$1 AND status='queued'", rid)
    await ctx.conn.execute("UPDATE app.alert_rules SET recipients = array_remove(recipients, $1)", rid)
    await ctx.conn.execute("UPDATE app.rules SET recipients = array_remove(recipients, $1)", rid)
    await ctx.conn.execute("DELETE FROM app.recipients WHERE id=$1", rid)
    await ctx.audit("recipient.delete", "recipient", rid)


# --------------------------------------------------------------------------------- alert types
class RuleIn(Strict):
    enabled: bool
    params: dict = {}
    channels: list[Channel] = Field(default_factory=lambda: ["whatsapp"], max_length=1)
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=50)
    quiet_start: time | None = None
    quiet_end: time | None = None


check_params = rules.check_params          # kept here for callers that imported it from this module


def _rule_out(rule_type: str, r) -> dict:
    meta = ALERT_TYPES[rule_type]
    enabled = r["enabled"] if r else rule_type in DEFAULT_ENABLED
    return {"type": rule_type, "family": meta["family"], "group": meta["group"], "ai": meta["family"] == alert_catalog.SMART,
            "title": meta["title"], "description": meta["description"], "severity": meta["severity"],
            "settings": {k: {**v} for k, v in meta["params"].items()}, "enabled": enabled,
            "params": ((r["condition"] if r is not None and "condition" in r.keys() else r["params"]) if r else {}) or {k: v["default"] for k, v in meta["params"].items()},
            "rule_id": str(r["id"]) if r else None,
            "channels": ["whatsapp"], "recipient_ids": list(r["recipients"]) if r else [],
            "quiet_start": r["quiet_start"].strftime("%H:%M") if r and r["quiet_start"] else None,
            "quiet_end": r["quiet_end"].strftime("%H:%M") if r and r["quiet_end"] else None}


@router.get("/alert-types")
async def alert_types(ctx: CustomerCtx = Depends(customer_ctx())):
    rows = {r["rule_type"]: r for r in await ctx.conn.fetch("SELECT * FROM app.rules WHERE is_default")}
    return [_rule_out(t, rows.get(t)) for t in await alert_catalog.visible(ctx.conn, ctx.tenant_id)]


@router.put("/alert-types/{rule_type}")
async def set_alert_type(rule_type: str, body: RuleIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if rule_type not in await alert_catalog.visible(ctx.conn, ctx.tenant_id):       # an alert the customer does not have looks the same as one that does not exist
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    params = check_params(rule_type, body.params)
    if (body.quiet_start is None) != (body.quiet_end is None):
        raise ApiError(422, "invalid_quiet_hours", "Set both the start and the end of quiet hours, or neither.")
    ids = list(dict.fromkeys(body.recipient_ids))
    people = await ctx.conn.fetch("SELECT id, name, phone, email FROM app.recipients WHERE id = ANY($1::uuid[]) AND active", ids)
    if len(people) != len(ids):
        raise ApiError(422, "recipient_not_found", "One of the people you chose was not found.")
    tenant = await ctx.conn.fetchrow("SELECT msg_whatsapp FROM app.tenants WHERE id=$1", ctx.tenant_id)
    warnings = []
    if not tenant["msg_whatsapp"]:
        warnings.append("WhatsApp messages are switched off for your account. Contact support.")
    for p in people:
        if not p["phone"]:
            warnings.append(f"{p['name']} has no mobile number, so will not get WhatsApp messages.")
    if body.enabled and rule_type in ("counting_stopped", "after_hours") and not await ctx.conn.fetchval("SELECT open_time IS NOT NULL AND close_time IS NOT NULL FROM app.tenants WHERE id=$1", ctx.tenant_id):
        warnings.append("Set your opening hours in Settings first. This alert needs them and does nothing until they are set.")
    if body.enabled and not ids:
        warnings.append("No one will be messaged yet. The alert will only appear in the app until you choose people.")
    row = await ctx.conn.fetchrow(
        """INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params, channels, recipients, quiet_start, quiet_end)
           VALUES ($1,$2,$3,$4,$5::app.channel_kind[],$6,$7,$8)
           ON CONFLICT (tenant_id, rule_type) DO UPDATE SET enabled=$3, params=$4, channels=$5::app.channel_kind[], recipients=$6, quiet_start=$7, quiet_end=$8
           RETURNING *""", ctx.tenant_id, rule_type, body.enabled, params, body.channels, ids, body.quiet_start, body.quiet_end)
    await ctx.audit("alert_rule.update", "alert_rule", row["id"], {"type": rule_type, "enabled": body.enabled, "channels": body.channels, "recipients": len(ids), "params": params})
    # The account-wide default rule is the same setting seen from the rule builder, so both screens always agree.
    await rules.mirror_default(ctx.conn, ctx.tenant_id, rule_type, enabled=body.enabled, params=params, recipients=ids,
                               quiet_start=body.quiet_start, quiet_end=body.quiet_end)
    return {**_rule_out(rule_type, row), "warnings": warnings}


# --------------------------------------------------------------------------------- alerts and the delivery log
STATE_SQL = "CASE WHEN a.acknowledged_at IS NOT NULL THEN 'done' WHEN a.snoozed_until > now() THEN 'snoozed' ELSE 'open' END"


@router.get("/alerts/summary")
async def alert_summary(from_: datetime = Query(..., alias="from"), to: datetime = Query(...), ctx: CustomerCtx = Depends(customer_ctx())):
    """How many alerts of each kind happened, and where, in a period: the numbers behind the Activity screens. Test messages are not counted.
    A place is the area's name for area alerts, otherwise the camera's (or the recorder's) name."""
    if to <= from_ or (to - from_).days > 62:
        raise ApiError(422, "bad_period", "Ask for a period of at most 62 days, with the end after the start.")
    rows = await ctx.conn.fetch(
        """SELECT a.rule_type, coalesce(nullif(a.details->>'area', ''), c.name, nullif(a.details->>'recorder', ''), '') AS place, count(*)::int AS count, max(a.created_at) AS last_at
           FROM app.alerts a LEFT JOIN app.cameras c ON c.id = a.camera_id
           WHERE a.created_at >= $1 AND a.created_at < $2 AND NOT coalesce((a.details->>'test')::boolean, false)
           GROUP BY 1, 2 ORDER BY 3 DESC""", from_, to)
    return [dict(r) for r in rows]


EVIDENCE_LINK_MINUTES = 60          # a link made from inside the app is short-lived, unlike the ones sent on WhatsApp


async def _evidence(ctx: CustomerCtx, alert_id: UUID):
    row = await ctx.conn.fetchrow(
        """SELECT a.id, a.evidence_id, a.clip_id, e.storage_key, e.content_type, (e.expires_at > now()) AS image_live, (c.expires_at > now()) AS clip_live
           FROM app.alerts a LEFT JOIN app.evidence e ON e.id = a.evidence_id LEFT JOIN app.evidence c ON c.id = a.clip_id WHERE a.id=$1""", alert_id)
    if not row:
        raise ApiError(404, "alert_not_found", "Alert not found.")
    return row


@router.get("/alerts/{alert_id}/image")
async def alert_image(alert_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    """The picture kept with an alert (what the camera saw), for people signed in to this customer. It is deleted after the retention period the Jas iVision team sets."""
    row = await _evidence(ctx, alert_id)
    gone = ApiError(404, "picture_not_found", "There is no picture for this alert. It may have been deleted after the retention period.")
    if not row["evidence_id"] or not row["image_live"]:
        raise gone
    data = await evidence.store().get(row["storage_key"])
    if data is None:
        raise gone
    return Response(data, media_type=row["content_type"], headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/alerts/{alert_id}/evidence-link")
async def alert_evidence_link(alert_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    """A link to the page that shows the alert's picture and video, good for an hour. Made for the person who is signed in; every one is logged."""
    row = await _evidence(ctx, alert_id)
    live = (row["evidence_id"] if row["image_live"] else None) or (row["clip_id"] if row["clip_live"] else None)
    if not live:
        raise ApiError(404, "picture_not_found", "There is nothing stored for this alert. It may have been deleted after the retention period.")
    token = evidence.make_token(live, EVIDENCE_LINK_MINUTES * 60)
    await ctx.audit("alert.evidence_link", "alert", alert_id)
    return {"url": f"{get_settings().public_base_url.rstrip('/')}/e/{token}", "expires_in_minutes": EVIDENCE_LINK_MINUTES, "has_image": bool(row["image_live"] and row["evidence_id"]), "has_clip": bool(row["clip_live"] and row["clip_id"])}


@router.get("/alerts")
async def list_alerts(limit: int = Query(50, ge=1, le=200), before: datetime | None = None, unacknowledged: bool = False,
                      state: Literal["open", "snoozed", "done"] | None = None, ctx: CustomerCtx = Depends(customer_ctx())):
    """The alert inbox. `state` is open (needs you), snoozed (hidden for a while) or done. Test messages are never listed."""
    rows = await ctx.conn.fetch(
        f"""SELECT a.id, a.rule_type, a.severity, a.title, a.body, a.created_at, a.acknowledged_at, a.snoozed_until, {STATE_SQL} AS state, a.camera_id, c.name AS camera_name,
                  (a.evidence_id IS NOT NULL) AS has_evidence, (a.clip_id IS NOT NULL) AS has_clip,
                  count(n.id) FILTER (WHERE n.status IN ('sent','delivered','read')) AS messages_sent, count(n.id) FILTER (WHERE n.status='failed') AS messages_failed,
                  count(n.id) FILTER (WHERE n.status='queued') AS messages_waiting
           FROM app.alerts a LEFT JOIN app.cameras c ON c.id=a.camera_id LEFT JOIN app.notifications n ON n.alert_id=a.id
           WHERE ($2::timestamptz IS NULL OR a.created_at < $2) AND (NOT $3 OR a.acknowledged_at IS NULL) AND NOT coalesce((a.details->>'test')::boolean, false)
             AND ($4::text IS NULL OR {STATE_SQL} = $4)
           GROUP BY a.id, c.name ORDER BY a.created_at DESC LIMIT $1""", limit, before, unacknowledged, state)
    return [dict(r) for r in rows]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    """Mark as done."""
    row = await ctx.conn.fetchrow("UPDATE app.alerts SET acknowledged_by=$2, acknowledged_at=now(), snoozed_until=NULL WHERE id=$1 AND acknowledged_at IS NULL RETURNING id, acknowledged_at", alert_id, ctx.user_id)
    if not row:
        if await ctx.conn.fetchval("SELECT 1 FROM app.alerts WHERE id=$1", alert_id):
            raise ApiError(409, "already_acknowledged", "Already acknowledged.")
        raise ApiError(404, "alert_not_found", "Alert not found.")
    await ctx.audit("alert.acknowledge", "alert", alert_id)
    return dict(row)


class SnoozeIn(Strict):
    minutes: int = Field(60, ge=5, le=1440)


@router.post("/alerts/{alert_id}/snooze")
async def snooze(alert_id: UUID, body: SnoozeIn | None = None, ctx: CustomerCtx = Depends(customer_ctx())):
    """Hides an open alert from "Needs you" for a while (one hour unless told otherwise). It comes back by itself when the time is up."""
    minutes = (body or SnoozeIn()).minutes
    row = await ctx.conn.fetchrow("UPDATE app.alerts SET snoozed_until = now() + make_interval(mins => $2) WHERE id=$1 AND acknowledged_at IS NULL RETURNING id, snoozed_until", alert_id, minutes)
    if not row:
        if await ctx.conn.fetchval("SELECT 1 FROM app.alerts WHERE id=$1", alert_id):
            raise ApiError(409, "already_acknowledged", "This alert is already done.")
        raise ApiError(404, "alert_not_found", "Alert not found.")
    await ctx.audit("alert.snooze", "alert", alert_id, {"minutes": minutes})
    return dict(row)


class TestIn(Strict):
    recipient_id: UUID


@router.post("/alert-types/{rule_type}/test")
async def send_test(rule_type: str, body: TestIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Sends one test message for this alert to one person, so the customer can see what an alert looks like. It uses the same route as a real alert, but is marked as a test,
    is already done, and never shows in the inbox. Quiet hours and the hourly limit do not apply."""
    if rule_type not in await alert_catalog.visible(ctx.conn, ctx.tenant_id):
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    person = await ctx.conn.fetchrow("SELECT id, name, phone FROM app.recipients WHERE id=$1 AND active", body.recipient_id)
    if not person:
        raise ApiError(404, "recipient_not_found", "That person was not found.")
    if not person["phone"]:
        raise ApiError(422, "no_mobile_number", f"{person['name']} has no mobile number.")
    tenant = await ctx.conn.fetchrow("SELECT timezone, msg_whatsapp, msg_clips FROM app.tenants WHERE id=$1", ctx.tenant_id)
    if not tenant["msg_whatsapp"]:
        raise ApiError(409, "whatsapp_off", "WhatsApp messages are switched off for your account. Contact support.")
    from ..services.alerts import clean_text, fan_out
    from zoneinfo import ZoneInfo
    meta = ALERT_TYPES[rule_type]
    title = "Test: " + meta["title"]
    payload = {"event": clean_text(title, 100), "location": "Test", "time": datetime.now(timezone.utc).astimezone(ZoneInfo(tenant["timezone"])).strftime("%d %b, %I:%M %p"),
               "details": "This is a test message. Nothing is wrong."}
    async with db.service_tx() as c:        # the webhook set-up belongs to the platform, so the queueing runs with the service's rights; every row is still for this one tenant
        alert_id = await c.fetchval(
            """INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, details, acknowledged_by, acknowledged_at) VALUES ($1,$2,'info',$3,$4,$5,$6, now()) RETURNING id""",
            ctx.tenant_id, rule_type, title, payload["details"], {**payload, "test": True}, ctx.user_id)
        await fan_out(c, ctx.tenant_id, tenant, alert_id, rule_type, [person["id"]], payload, quiet=False)
        status = await c.fetchrow("SELECT status::text AS status, error FROM app.notifications WHERE alert_id=$1", alert_id)
    await ctx.audit("alert.test", "alert", alert_id, {"type": rule_type})
    return {"sent": bool(status and status["status"] == "queued"), "status": status["status"] if status else "none", "reason": status["error"] if status else None, "to": person["name"]}


@router.get("/notifications")
async def delivery_log(limit: int = Query(50, ge=1, le=200), status: str | None = Query(None, pattern="^(queued|sent|delivered|read|failed|suppressed|cancelled)$"),
                       ctx: CustomerCtx = Depends(customer_ctx())):
    rows = await ctx.conn.fetch(
        """SELECT n.id, n.alert_id, a.title, n.channel::text AS channel, n.recipient, r.name AS person, n.status::text AS status, n.error, n.created_at, n.sent_at,
                  n.delivered_at, n.fallback_of IS NOT NULL AS is_fallback
           FROM app.notifications n LEFT JOIN app.alerts a ON a.id=n.alert_id LEFT JOIN app.recipients r ON r.id=n.recipient_id
           WHERE ($2::text IS NULL OR n.status::text = $2) ORDER BY n.created_at DESC LIMIT $1""", limit, status)
    out = []
    for r in rows:
        d = dict(r)
        d["recipient"] = mask_phone(d["recipient"]) if d["channel"] == "whatsapp" else d["recipient"]
        out.append(d)
    return out
