"""Customer-built rules: what to watch, on which camera or area, with which numbers, and who hears about it.

A customer can have several rules of the same kind. Creating, changing and deleting a rule happens in one transaction, so a
half-saved rule cannot exist. `version` makes two edits of the same rule safe: the second one is told the rule changed."""
from datetime import datetime, time, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import alert_catalog, rules
from ..services.alerts import clean_text, fan_out
from ..services.alert_catalog import ALERT_TYPES

router = APIRouter(prefix="/v1", tags=["customer: rules"])
Channels = Literal["whatsapp"]
TESTS_PER_HOUR = 10


class RuleIn(Strict):
    name: str | None = Field(None, max_length=80)
    rule_type: str
    camera_id: UUID | None = None
    zone_id: UUID | None = None
    condition: dict = {}
    enabled: bool = True
    channels: list[Channels] = Field(default_factory=lambda: ["whatsapp"], max_length=1)
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=50)
    quiet_start: time | None = None
    quiet_end: time | None = None
    version: int | None = None                   # the version the person was looking at, when changing a rule


class RulePatch(Strict):
    name: str | None = Field(None, max_length=80)
    camera_id: UUID | None = None
    zone_id: UUID | None = None
    condition: dict | None = None
    enabled: bool | None = None
    channels: list[Channels] | None = Field(None, max_length=1)
    recipient_ids: list[UUID] | None = Field(None, max_length=50)
    quiet_start: time | None = None
    quiet_end: time | None = None
    version: int | None = None


def _default_name(rule_type: str, camera: str | None, area: str | None) -> str:
    title = ALERT_TYPES[rule_type]["title"]
    where = area or camera
    return (f"{title} · {where}" if where else title)[:80]


async def _people(conn, ids: list[UUID]) -> list:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    people = await conn.fetch("SELECT id, name, phone FROM app.recipients WHERE id = ANY($1::uuid[]) AND active", ids)
    if len(people) != len(ids):
        raise ApiError(422, "recipient_not_found", "One of the people you chose was not found.")
    return people


async def _warnings(conn, tenant_id: UUID, rule_type: str, enabled: bool, people: list) -> list[str]:
    tenant = await conn.fetchrow("SELECT msg_whatsapp FROM app.tenants WHERE id=$1", tenant_id)
    out = []
    if not tenant["msg_whatsapp"]:
        out.append("WhatsApp messages are switched off for your account. Contact support.")
    for p in people:
        if not p["phone"]:
            out.append(f"{p['name']} has no mobile number, so will not get WhatsApp messages.")
    if enabled and rule_type in ("counting_stopped", "after_hours") and not await conn.fetchval(
            "SELECT open_time IS NOT NULL AND close_time IS NOT NULL FROM app.tenants WHERE id=$1", tenant_id):
        out.append("Set your opening hours in Settings first. This alert needs them and does nothing until they are set.")
    if enabled and not people:
        out.append("No one will be messaged yet. The alert will only appear in the app until you choose people.")
    return out


async def _check_place(conn, tenant_id: UUID, camera_id, zone_id) -> tuple[str | None, str | None]:
    """Returns the camera and area names, and refuses a camera or area that is not this customer's."""
    if zone_id is not None and camera_id is None:
        raise ApiError(422, "camera_required", "Choose the camera the marked area is on.")
    camera = area = None
    if camera_id is not None:
        row = await conn.fetchrow("SELECT name FROM app.cameras WHERE id=$1", camera_id)
        if row is None:
            raise ApiError(404, "camera_not_found", "That camera was not found.")
        camera = row["name"]
    if zone_id is not None:
        row = await conn.fetchrow("SELECT z.camera_id, z.name, c.name AS camera FROM app.camera_zones z JOIN app.cameras c ON c.id=z.camera_id WHERE z.id=$1", zone_id)
        if row is None or row["camera_id"] != camera_id:
            raise ApiError(404, "area_not_found", "That marked area was not found on this camera.")
        area = row["name"] or row["camera"]
    return camera, area


@router.get("/rules")
async def list_rules(rule_type: str | None = Query(None, max_length=40), camera_id: UUID | None = None,
                     ctx: CustomerCtx = Depends(customer_ctx())):
    """Every rule this customer has. The old one-setting-per-kind rows are included as their account-wide default rule."""
    where, args = ["tenant_id=$1"], [ctx.tenant_id]
    if rule_type:
        args.append(rule_type)
        where.append(f"rule_type=${len(args)}")
    if camera_id:
        args.append(camera_id)
        where.append(f"camera_id=${len(args)}")
    rows = await ctx.conn.fetch(f"SELECT * FROM app.rules WHERE {' AND '.join(where)} ORDER BY rule_type, is_default DESC, created_at", *args)
    return [rules.out(r) for r in rows]


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    row = await ctx.conn.fetchrow("SELECT * FROM app.rules WHERE id=$1", rule_id)
    if row is None:
        raise ApiError(404, "rule_not_found", "That rule was not found.")
    return rules.out(row)


@router.post("/rules", status_code=201)
async def create_rule(body: RuleIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if body.rule_type not in await alert_catalog.visible(ctx.conn, ctx.tenant_id):
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    rules.check_quiet(body.quiet_start, body.quiet_end)
    rules.check_scope(body.rule_type, body.camera_id, body.zone_id)
    condition = rules.check_condition(body.rule_type, body.condition)
    camera, area = await _check_place(ctx.conn, ctx.tenant_id, body.camera_id, body.zone_id)
    people = await _people(ctx.conn, body.recipient_ids)
    ids = [p["id"] for p in people]
    name = (body.name or "").strip() or _default_name(body.rule_type, camera, area)
    row = await ctx.conn.fetchrow(
        """INSERT INTO app.rules (tenant_id, name, rule_type, camera_id, zone_id, condition, channels, recipients, quiet_start, quiet_end, enabled)
           VALUES ($1,$2,$3,$4,$5,$6,$7::app.channel_kind[],$8,$9,$10,$11) RETURNING *""",
        ctx.tenant_id, name, body.rule_type, body.camera_id, body.zone_id, condition, body.channels, ids, body.quiet_start, body.quiet_end, body.enabled)
    await ctx.audit("rule.create", "rule", row["id"], {"type": body.rule_type, "camera": str(body.camera_id) if body.camera_id else None,
                                                       "area": str(body.zone_id) if body.zone_id else None, "recipients": len(ids)})
    return {**rules.out(row), "warnings": await _warnings(ctx.conn, ctx.tenant_id, body.rule_type, body.enabled, people)}


@router.patch("/rules/{rule_id}")
async def change_rule(rule_id: UUID, body: RulePatch, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    before = await ctx.conn.fetchrow("SELECT * FROM app.rules WHERE id=$1", rule_id)
    if before is None:
        raise ApiError(404, "rule_not_found", "That rule was not found.")
    if body.version is not None and body.version != before["version"]:
        raise ApiError(409, "rule_changed", "Someone else changed this rule. Open it again to see the latest version.")
    fields = body.model_fields_set
    camera_id = body.camera_id if "camera_id" in fields else before["camera_id"]
    zone_id = body.zone_id if "zone_id" in fields else before["zone_id"]
    condition = rules.check_condition(before["rule_type"], body.condition) if body.condition is not None else dict(before["condition"] or {})
    people = await _people(ctx.conn, body.recipient_ids) if body.recipient_ids is not None else await _people(ctx.conn, list(before["recipients"]))
    if before["is_default"] and (camera_id is not None or zone_id is not None):
        raise ApiError(422, "default_is_account_wide", "This is the setting for every camera. Make a new rule to limit an alert to one camera or area.")
    rules.check_scope(before["rule_type"], camera_id, zone_id)
    camera, area = await _check_place(ctx.conn, ctx.tenant_id, camera_id, zone_id)
    quiet_start = body.quiet_start if "quiet_start" in fields else before["quiet_start"]
    quiet_end = body.quiet_end if "quiet_end" in fields else before["quiet_end"]
    rules.check_quiet(quiet_start, quiet_end)
    name = ((body.name if "name" in fields else before["name"]) or "").strip() or _default_name(before["rule_type"], camera, area)
    row = await ctx.conn.fetchrow(
        """UPDATE app.rules SET name=$2, camera_id=$3, zone_id=$4, condition=$5, channels=$6::app.channel_kind[], recipients=$7,
                  quiet_start=$8, quiet_end=$9, enabled=$10, version=version+1, updated_at=now()
            WHERE id=$1 RETURNING *""",
        rule_id, name, camera_id, zone_id, condition, body.channels or before["channels"], [p["id"] for p in people],
        quiet_start, quiet_end, body.enabled if body.enabled is not None else before["enabled"])
    if row["is_default"]:
        await rules.mirror_legacy(ctx.conn, row)            # the old one-setting-per-kind row follows, so every screen and count agrees
    await ctx.audit("rule.update", "rule", rule_id, {"type": before["rule_type"], "enabled": row["enabled"], "version": row["version"]})
    return {**rules.out(row), "warnings": await _warnings(ctx.conn, ctx.tenant_id, before["rule_type"], row["enabled"], people)}


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    cur = await ctx.conn.fetchrow("SELECT is_default FROM app.rules WHERE id=$1", rule_id)
    if cur is None:
        raise ApiError(404, "rule_not_found", "That rule was not found.")
    if cur["is_default"]:
        raise ApiError(409, "default_cannot_be_deleted", "This is the setting for every camera. Switch it off instead of deleting it.")
    row = await ctx.conn.fetchrow("DELETE FROM app.rules WHERE id=$1 RETURNING id, rule_type, is_default", rule_id)
    await ctx.audit("rule.delete", "rule", rule_id, {"type": row["rule_type"], "default": row["is_default"]})


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Sends one test message to each person on the rule, using the same queue as a real alert. It is marked as a test and
    never appears in the inbox; quiet hours do not hold it back."""
    row = await ctx.conn.fetchrow("SELECT * FROM app.rules WHERE id=$1", rule_id)
    if row is None:
        raise ApiError(404, "rule_not_found", "That rule was not found.")
    people = await ctx.conn.fetch("SELECT id, name, phone FROM app.recipients WHERE id = ANY($1::uuid[]) AND active", list(row["recipients"]))
    people = [p for p in people if p["phone"]]
    if not people:
        raise ApiError(422, "no_recipients", "Choose at least one person with a mobile number first.")
    if await ctx.conn.fetchval("SELECT count(*) FROM app.alerts WHERE rule_id=$1 AND details->>'test' = 'true' AND created_at > now() - interval '1 hour'", rule_id) >= TESTS_PER_HOUR:
        raise ApiError(429, "too_many_tests", "You have sent several test messages for this rule. Please try again in an hour.")
    tenant = await ctx.conn.fetchrow("SELECT timezone, msg_whatsapp, msg_clips FROM app.tenants WHERE id=$1", ctx.tenant_id)
    if not tenant["msg_whatsapp"]:
        raise ApiError(409, "whatsapp_off", "WhatsApp messages are switched off for your account. Contact support.")
    meta = ALERT_TYPES[row["rule_type"]]
    title = "Test: " + meta["title"]
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(tenant["timezone"]))
    payload = {"event": clean_text(title, 100), "location": row["name"][:80], "time": now.strftime("%d %b, %I:%M %p"),
               "details": "This is a test message. Nothing is wrong."}
    from ..db import db
    async with db.service_tx() as conn:        # the webhook set-up belongs to the platform; every row is still for this one tenant
        from ..services.alerts import raise_alert
        alert_id = await conn.fetchval(
            """INSERT INTO app.alerts (tenant_id, rule_id, rule_type, severity, title, body, details, acknowledged_by, acknowledged_at)
               VALUES ($1,$2,$3,'info',$4,$5,$6,$7,now()) RETURNING id""",
            ctx.tenant_id, rule_id, row["rule_type"], title, payload["details"], {**payload, "test": True}, ctx.user_id)
        await fan_out(conn, ctx.tenant_id, tenant, alert_id, row["rule_type"], [p["id"] for p in people], payload, quiet=False)
        rows = await conn.fetch("SELECT status::text AS status, error FROM app.notifications WHERE alert_id=$1", alert_id)
    await ctx.audit("rule.test", "rule", rule_id, {"recipients": len(people)})
    return {"queued": sum(1 for r in rows if r["status"] == "queued"), "sent_to": [p["name"] for p in people],
            "reason": next((r["error"] for r in rows if r["error"]), None)}


@router.get("/rules/{rule_id}/deliveries")
async def rule_deliveries(rule_id: UUID, limit: int = Query(20, ge=1, le=100), ctx: CustomerCtx = Depends(customer_ctx())):
    """What this rule sent lately, so support can tell "the customer's network" from "we never sent it"."""
    rows = await ctx.conn.fetch(
        """SELECT n.id, n.status::text AS status, n.error, n.recipient, r.name AS person, n.created_at, n.sent_at, a.title
             FROM app.notifications n LEFT JOIN app.recipients r ON r.id=n.recipient_id LEFT JOIN app.alerts a ON a.id=n.alert_id
            WHERE n.tenant_id=$1 AND a.rule_id=$2 ORDER BY n.created_at DESC LIMIT $3""", ctx.tenant_id, rule_id, limit)
    return [dict(r) for r in rows]
