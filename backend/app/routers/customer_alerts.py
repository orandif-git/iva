"""What a customer controls about alerts: which kinds are on, who gets them, how, and when. Plus the alert list and the delivery log."""
import re
from datetime import datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..messaging.defaults import ALERT_TYPES
from ..schemas import Strict
from ..services.alerts import DEFAULT_ENABLED, fan_out

router = APIRouter(prefix="/v1", tags=["customer: alerts"])
Channel = Literal["whatsapp", "email"]


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
    await ctx.conn.execute("DELETE FROM app.recipients WHERE id=$1", rid)
    await ctx.audit("recipient.delete", "recipient", rid)


@router.post("/recipients/{rid}/test")
async def test_message(rid: UUID, channel: Channel = Query(...), ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Sends a short test so the customer can see it works. Three a day per person."""
    r = await ctx.conn.fetchrow("SELECT * FROM app.recipients WHERE id=$1 AND active", rid)
    if not r:
        raise ApiError(404, "recipient_not_found", "Person not found.")
    if (channel == "whatsapp" and not r["phone"]) or (channel == "email" and not r["email"]):
        raise ApiError(422, "no_contact", f"This person has no {'mobile number' if channel == 'whatsapp' else 'email address'}.")
    if await ctx.conn.fetchval("SELECT count(*) FROM app.alerts a JOIN app.notifications n ON n.alert_id=a.id WHERE a.rule_type='test' AND n.recipient_id=$1 "
                               "AND a.created_at > now() - interval '1 day'", rid) >= 3:
        raise ApiError(429, "test_limit", "Three tests a day per person is the limit. Try again tomorrow.")
    tenant = await ctx.conn.fetchrow("SELECT timezone, msg_whatsapp, msg_email, msg_monthly_cap FROM app.tenants WHERE id=$1", ctx.tenant_id)
    from zoneinfo import ZoneInfo
    payload = {"event": "Test message", "location": "Setup", "time": datetime.now(ZoneInfo(tenant["timezone"])).strftime("%d %b, %I:%M %p"), "details": "This is a test from Jas iVision."}
    alert_id = await ctx.conn.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, details) VALUES ($1,'test','info','Test message','Sent from your settings',$2) RETURNING id",
                                       ctx.tenant_id, payload)
    n = await fan_out(ctx.conn, ctx.tenant_id, tenant, alert_id, [channel], [rid], payload)
    if not n:
        raise ApiError(409, "not_sent", "The message could not be queued.")
    status = await ctx.conn.fetchval("SELECT status::text FROM app.notifications WHERE alert_id=$1", alert_id)
    if status == "suppressed":
        raise ApiError(409, "not_sent", await ctx.conn.fetchval("SELECT error FROM app.notifications WHERE alert_id=$1", alert_id))
    return {"queued": True}


# --------------------------------------------------------------------------------- alert types
class RuleIn(Strict):
    enabled: bool
    params: dict = {}
    channels: list[Channel] = Field(default_factory=lambda: ["whatsapp"], max_length=2)
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=50)
    quiet_start: time | None = None
    quiet_end: time | None = None


def check_params(rule_type: str, params: dict) -> dict:
    spec = ALERT_TYPES[rule_type]["params"]
    unknown = set(params) - set(spec)
    if unknown:
        raise ApiError(422, "invalid_params", f"Unknown setting: {', '.join(sorted(unknown))}.")
    out = {}
    for k, s in spec.items():
        v = params.get(k, s["default"])
        if s["type"] == "boolean":
            if not isinstance(v, bool):
                raise ApiError(422, "invalid_params", f"{k} must be on or off.")
            out[k] = v
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (s["type"] == "integer" and int(v) != v):
            raise ApiError(422, "invalid_params", f"{k} must be a number.")
        if not s["min"] <= v <= s["max"]:
            raise ApiError(422, "invalid_params", f"{k} must be between {s['min']} and {s['max']}.")
        out[k] = int(v) if s["type"] == "integer" else v
    return out


def _rule_out(rule_type: str, r) -> dict:
    meta = ALERT_TYPES[rule_type]
    enabled = r["enabled"] if r else rule_type in DEFAULT_ENABLED
    return {"type": rule_type, "title": meta["title"], "description": meta["description"], "severity": meta["severity"],
            "settings": {k: {**v} for k, v in meta["params"].items()}, "enabled": enabled,
            "params": (r["params"] if r else {}) or {k: v["default"] for k, v in meta["params"].items()},
            "channels": list(r["channels"]) if r else ["whatsapp"], "recipient_ids": list(r["recipients"]) if r else [],
            "quiet_start": r["quiet_start"].strftime("%H:%M") if r and r["quiet_start"] else None,
            "quiet_end": r["quiet_end"].strftime("%H:%M") if r and r["quiet_end"] else None}


@router.get("/alert-types")
async def alert_types(ctx: CustomerCtx = Depends(customer_ctx())):
    rows = {r["rule_type"]: r for r in await ctx.conn.fetch("SELECT * FROM app.alert_rules")}
    return [_rule_out(t, rows.get(t)) for t in ALERT_TYPES]


@router.put("/alert-types/{rule_type}")
async def set_alert_type(rule_type: str, body: RuleIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if rule_type not in ALERT_TYPES:
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    params = check_params(rule_type, body.params)
    if (body.quiet_start is None) != (body.quiet_end is None):
        raise ApiError(422, "invalid_quiet_hours", "Set both the start and the end of quiet hours, or neither.")
    ids = list(dict.fromkeys(body.recipient_ids))
    people = await ctx.conn.fetch("SELECT id, name, phone, email FROM app.recipients WHERE id = ANY($1::uuid[]) AND active", ids)
    if len(people) != len(ids):
        raise ApiError(422, "recipient_not_found", "One of the people you chose was not found.")
    tenant = await ctx.conn.fetchrow("SELECT msg_whatsapp, msg_email FROM app.tenants WHERE id=$1", ctx.tenant_id)
    warnings = []
    if "whatsapp" in body.channels and not tenant["msg_whatsapp"]:
        warnings.append("WhatsApp messages are switched off for your account. Contact support.")
    if "email" in body.channels and not tenant["msg_email"]:
        warnings.append("Email messages are switched off for your account. Contact support.")
    for p in people:
        if "whatsapp" in body.channels and "email" not in body.channels and not p["phone"]:
            warnings.append(f"{p['name']} has no mobile number, so will not get WhatsApp messages.")
        if body.channels == ["email"] and not p["email"]:
            warnings.append(f"{p['name']} has no email address, so will not get emails.")
    if body.enabled and not ids:
        warnings.append("No one will be messaged yet. The alert will only appear in the app until you choose people.")
    row = await ctx.conn.fetchrow(
        """INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params, channels, recipients, quiet_start, quiet_end)
           VALUES ($1,$2,$3,$4,$5::app.channel_kind[],$6,$7,$8)
           ON CONFLICT (tenant_id, rule_type) DO UPDATE SET enabled=$3, params=$4, channels=$5::app.channel_kind[], recipients=$6, quiet_start=$7, quiet_end=$8
           RETURNING *""", ctx.tenant_id, rule_type, body.enabled, params, body.channels, ids, body.quiet_start, body.quiet_end)
    await ctx.audit("alert_rule.update", "alert_rule", row["id"], {"type": rule_type, "enabled": body.enabled, "channels": body.channels, "recipients": len(ids), "params": params})
    return {**_rule_out(rule_type, row), "warnings": warnings}


# --------------------------------------------------------------------------------- alerts and the delivery log
@router.get("/alerts")
async def list_alerts(limit: int = Query(50, ge=1, le=200), before: datetime | None = None, unacknowledged: bool = False, ctx: CustomerCtx = Depends(customer_ctx())):
    rows = await ctx.conn.fetch(
        """SELECT a.id, a.rule_type, a.severity, a.title, a.body, a.created_at, a.acknowledged_at, a.camera_id, c.name AS camera_name, (a.evidence_id IS NOT NULL) AS has_evidence, (a.clip_id IS NOT NULL) AS has_clip,
                  count(n.id) FILTER (WHERE n.status IN ('sent','delivered','read')) AS messages_sent, count(n.id) FILTER (WHERE n.status='failed') AS messages_failed,
                  count(n.id) FILTER (WHERE n.status='queued') AS messages_waiting
           FROM app.alerts a LEFT JOIN app.cameras c ON c.id=a.camera_id LEFT JOIN app.notifications n ON n.alert_id=a.id
           WHERE ($2::timestamptz IS NULL OR a.created_at < $2) AND (NOT $3 OR a.acknowledged_at IS NULL)
           GROUP BY a.id, c.name ORDER BY a.created_at DESC LIMIT $1""", limit, before, unacknowledged)
    return [dict(r) for r in rows]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(alert_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    row = await ctx.conn.fetchrow("UPDATE app.alerts SET acknowledged_by=$2, acknowledged_at=now() WHERE id=$1 AND acknowledged_at IS NULL RETURNING id, acknowledged_at", alert_id, ctx.user_id)
    if not row:
        if await ctx.conn.fetchval("SELECT 1 FROM app.alerts WHERE id=$1", alert_id):
            raise ApiError(409, "already_acknowledged", "Already acknowledged.")
        raise ApiError(404, "alert_not_found", "Alert not found.")
    await ctx.audit("alert.acknowledge", "alert", alert_id)
    return dict(row)


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
