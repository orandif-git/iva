"""The people who get messages, and the events (alert rules) of one customer.

The Control Center (routers/admin_shop.py) calls these with one customer's data in view. They are not web routes of their own."""
import re
from datetime import datetime, time, timezone
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from ..db import db
from ..deps import CustomerCtx
from ..errors import ApiError
from ..services import alert_catalog
from ..services.alert_catalog import ALERT_TYPES
from ..schemas import Strict
from ..services.alerts import DEFAULT_ENABLED



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


async def list_recipients(ctx: CustomerCtx):
    return [_recipient(r) for r in await ctx.conn.fetch(f"SELECT {_R} FROM app.recipients ORDER BY name, created_at")]


async def add_recipient(body: RecipientIn, ctx: CustomerCtx):
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


async def patch_recipient(rid: UUID, body: RecipientPatch, ctx: CustomerCtx):
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


async def delete_recipient(rid: UUID, ctx: CustomerCtx):
    if not await ctx.conn.fetchval("SELECT 1 FROM app.recipients WHERE id=$1", rid):
        raise ApiError(404, "recipient_not_found", "Person not found.")
    # Cancel first: deleting the person clears recipient_id on their messages, after which they could no longer be found.
    await ctx.conn.execute("UPDATE app.notifications SET status='cancelled', error='The person was removed.', locked_until=NULL WHERE recipient_id=$1 AND status='queued'", rid)
    await ctx.conn.execute("UPDATE app.alert_rules SET recipients = array_remove(recipients, $1)", rid)
    await ctx.conn.execute("DELETE FROM app.recipients WHERE id=$1", rid)
    await ctx.audit("recipient.delete", "recipient", rid)


class RuleIn(Strict):
    enabled: bool
    params: dict = {}
    channels: list[Channel] = Field(default_factory=lambda: ["whatsapp"], max_length=1)
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
    return {"type": rule_type, "family": meta["family"], "group": meta["group"], "ai": meta["family"] == alert_catalog.SMART,
            "title": meta["title"], "description": meta["description"], "severity": meta["severity"],
            "settings": {k: {**v} for k, v in meta["params"].items()}, "enabled": enabled,
            "params": (r["params"] if r else {}) or {k: v["default"] for k, v in meta["params"].items()},
            "channels": ["whatsapp"], "recipient_ids": list(r["recipients"]) if r else [],
            "quiet_start": r["quiet_start"].strftime("%H:%M") if r and r["quiet_start"] else None,
            "quiet_end": r["quiet_end"].strftime("%H:%M") if r and r["quiet_end"] else None}


async def alert_types(ctx: CustomerCtx):
    rows = {r["rule_type"]: r for r in await ctx.conn.fetch("SELECT * FROM app.alert_rules")}
    return [_rule_out(t, rows.get(t)) for t in await alert_catalog.visible(ctx.conn, ctx.tenant_id)]


async def set_alert_type(rule_type: str, body: RuleIn, ctx: CustomerCtx):
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
    return {**_rule_out(rule_type, row), "warnings": warnings}


class TestIn(Strict):
    recipient_id: UUID


async def send_test(rule_type: str, body: TestIn, ctx: CustomerCtx):
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
