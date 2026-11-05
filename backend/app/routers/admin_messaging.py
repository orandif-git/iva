"""Super-admin control of how messages leave the platform: the providers (WhatsApp BSP, email), the message templates, each account's
messaging limits, and the delivery log. Providers and templates are owner-only because they hold secrets and cost money."""
import json
import re
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..config import get_settings
from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..messaging import delivery, http_provider, netguard, render, smtp_provider
from ..messaging.defaults import DEFAULT_TEMPLATES
from ..schemas import Strict

router = APIRouter(prefix="/admin/v1/messaging", tags=["admin: messaging"])
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class ProviderIn(Strict):
    channel: Literal["whatsapp", "email"]
    name: str = Field(min_length=2, max_length=80)
    kind: Literal["http", "smtp"]
    config: dict
    secrets: dict[str, str] = Field(default_factory=dict, max_length=20)
    rate_per_second: float = Field(5, gt=0, le=100)
    reason: str = Field(min_length=5, max_length=500)


class ProviderPatch(Strict):
    name: str | None = Field(None, min_length=2, max_length=80)
    config: dict | None = None
    secrets: dict[str, str | None] | None = Field(None, max_length=20)      # a value of null removes that secret
    rate_per_second: float | None = Field(None, gt=0, le=100)
    reason: str = Field(min_length=5, max_length=500)


def _check_secrets(secrets: dict) -> None:
    for k, v in secrets.items():
        if not SECRET_NAME.match(k):
            raise ApiError(422, "invalid_secret_name", "Secret names use lowercase letters, numbers and underscores, for example api_key.")
        if v is not None and not (1 <= len(v) <= 4000):
            raise ApiError(422, "invalid_secret", f"The value of '{k}' is empty or too long.")


def _validate(kind: str, channel: str, config: dict, names: set[str]) -> None:
    allow = get_settings().messaging_allow_private_hosts
    try:
        if kind == "http":
            http_provider.validate_config(config, names, allow_private=allow)
        else:
            if channel != "email":
                raise render.RenderError("SMTP can only be used for email")
            smtp_provider.validate_config(config, allow_private=allow)
    except (render.RenderError, netguard.BlockedAddress) as e:
        raise ApiError(422, "invalid_recipe", str(e))


def _public(r, with_config: bool = True) -> dict:
    names = sorted(json.loads(vault().decrypt(bytes(r["secrets_ciphertext"]), str(r["id"]))).keys()) if r["secrets_ciphertext"] else []
    out = {"id": r["id"], "channel": r["channel"], "name": r["name"], "kind": r["kind"], "active": r["active"], "rate_per_second": float(r["rate_per_second"]),
           "secret_names": names, "updated_at": r["updated_at"]}
    if with_config:
        out["config"] = r["config"]
    return out


def _seal(pid: UUID, secrets: dict) -> bytes | None:
    return vault().encrypt(json.dumps(secrets), str(pid)) if secrets else None


async def _get(ctx: AdminCtx, pid: UUID):
    r = await ctx.conn.fetchrow("SELECT * FROM platform.messaging_providers WHERE id=$1", pid)
    if not r:
        raise ApiError(404, "provider_not_found", "Provider not found.")
    return r


def _secrets_of(r) -> dict:
    return json.loads(vault().decrypt(bytes(r["secrets_ciphertext"]), str(r["id"]))) if r["secrets_ciphertext"] else {}


# ----------------------------------------------------------------------------- providers
@router.get("/providers")
async def list_providers(ctx: AdminCtx = Depends(admin_ctx("owner", "support", "technician"))):
    return [_public(r) for r in await ctx.conn.fetch("SELECT * FROM platform.messaging_providers ORDER BY channel, created_at")]


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    _check_secrets(body.secrets)
    _validate(body.kind, body.channel, body.config, set(body.secrets))
    pid = await ctx.conn.fetchval("SELECT gen_random_uuid()")
    row = await ctx.conn.fetchrow(
        """INSERT INTO platform.messaging_providers (id, channel, name, kind, config, secrets_ciphertext, rate_per_second)
           VALUES ($1,$2::app.channel_kind,$3,$4,$5,$6,$7) RETURNING *""", pid, body.channel, body.name, body.kind, body.config, _seal(pid, body.secrets), body.rate_per_second)
    await ctx.audit("messaging.provider.create", target_type="messaging_provider", target_id=pid, reason=body.reason,
                    after={"name": body.name, "channel": body.channel, "kind": body.kind, "secret_names": sorted(body.secrets)})
    return _public(row)


@router.put("/providers/{pid}")
async def update_provider(pid: UUID, body: ProviderPatch, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, pid)
    secrets = _secrets_of(cur)
    if body.secrets is not None:
        _check_secrets(body.secrets)
        for k, v in body.secrets.items():
            if v is None:
                secrets.pop(k, None)
            else:
                secrets[k] = v
    config = body.config if body.config is not None else cur["config"]
    _validate(cur["kind"], cur["channel"], config, set(secrets))
    row = await ctx.conn.fetchrow(
        "UPDATE platform.messaging_providers SET name=$2, config=$3, secrets_ciphertext=$4, rate_per_second=$5, updated_at=now() WHERE id=$1 RETURNING *",
        pid, body.name or cur["name"], config, _seal(pid, secrets), body.rate_per_second or cur["rate_per_second"])
    await ctx.audit("messaging.provider.update", target_type="messaging_provider", target_id=pid, reason=body.reason,
                    after={"name": row["name"], "config_changed": body.config is not None, "secret_names": sorted(secrets), "secrets_changed": sorted(body.secrets or {})})
    return _public(row)


@router.post("/providers/{pid}/activate")
async def activate(pid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, pid)
    _validate(cur["kind"], cur["channel"], cur["config"], set(_secrets_of(cur)))
    await ctx.conn.execute("UPDATE platform.messaging_providers SET active=false WHERE channel=$1 AND active", cur["channel"])
    await ctx.conn.execute("UPDATE platform.messaging_providers SET active=true WHERE id=$1", pid)
    await ctx.audit("messaging.provider.activate", target_type="messaging_provider", target_id=pid, reason=reason, after={"channel": cur["channel"], "name": cur["name"]})
    return {"active": True}


@router.post("/providers/{pid}/deactivate")
async def deactivate(pid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, pid)
    await ctx.conn.execute("UPDATE platform.messaging_providers SET active=false WHERE id=$1", pid)
    await ctx.audit("messaging.provider.deactivate", target_type="messaging_provider", target_id=pid, reason=reason)
    return {"active": False}


@router.delete("/providers/{pid}", status_code=204)
async def delete_provider(pid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, pid)
    if cur["active"]:
        raise ApiError(409, "provider_active", "Deactivate it first, or activate another provider.")
    try:
        await ctx.conn.execute("DELETE FROM platform.messaging_providers WHERE id=$1", pid)
    except asyncpg.ForeignKeyViolationError:
        raise ApiError(409, "provider_in_use", "It has message history.")
    await ctx.audit("messaging.provider.delete", target_type="messaging_provider", target_id=pid, reason=reason, before={"name": cur["name"]})


class TestIn(Strict):
    to: str = Field(min_length=3, max_length=120)             # phone for WhatsApp, address for email
    template_key: str | None = Field(None, max_length=40)
    media_url: str | None = Field(None, max_length=500)
    send: bool = False                                        # false = show the request that would be sent, send nothing


@router.post("/providers/{pid}/test")
async def test_provider(pid: UUID, body: TestIn, ctx: AdminCtx = Depends(admin_ctx("owner", "technician"))):
    """Preview (default) or really send a sample message, so a recipe can be tried before any customer depends on it."""
    p = await _get(ctx, pid)
    secrets, s = _secrets_of(p), get_settings()
    sample_vars = ["Blocked vehicle TN72AB1234", "Main Gate", "05 Nov, 10:30 AM", "Seen entering"]
    if p["kind"] == "smtp":
        if not body.send:
            return {"preview": {"to": body.to, "subject": "[Jas iVision] Test message", "from": p["config"].get("from_email")}}
        res = await smtp_provider.send(p["config"], secrets, {"to": body.to, "name": "Test", "subject": "[Jas iVision] Test message",
                                                              "text": "This is a test message from Jas iVision.", "html": None},
                                       allow_private=s.messaging_allow_private_hosts, ssl_context=delivery.ssl_context())
    else:
        tpl = None
        if body.template_key:
            tpl = await ctx.conn.fetchrow("SELECT * FROM platform.message_templates WHERE channel=$1::app.channel_kind AND key=$2", p["channel"], body.template_key)
            if not tpl:
                raise ApiError(404, "template_not_found", "Template not found.")
        phone = body.to if body.to.startswith("+") else "+" + body.to
        msg = {"recipient": {"phone": phone, "name": "Test", "email": body.to}, "template": {"name": tpl["provider_template"] if tpl else "hello_world", "language": tpl["language"] if tpl else "en"},
               "vars": sample_vars[:len(tpl["variables"])] if tpl else [], "media_type": tpl["media_type"] if tpl else None,
               "media_url": body.media_url if tpl and tpl["media_type"] in ("image", "video") else None,
               "link_url": "https://example.com/e/sample", "link_token": "sample" if tpl and tpl["link_button"] else None, "message_id": "test",
               "alert": {"title": sample_vars[0], "body": sample_vars[3], "camera": sample_vars[1], "time": sample_vars[2]}}
        if not body.send:
            return {"preview": await http_provider.send(p["config"], secrets, msg, dry_run=True)}
        res = await http_provider.send(p["config"], secrets, msg, allow_private=s.messaging_allow_private_hosts, ssl_context=delivery.ssl_context())
    await ctx.audit("messaging.provider.test", target_type="messaging_provider", target_id=pid, after={"ok": res.ok, "to_hint": body.to[:3] + "…" + body.to[-2:]})
    return {"ok": res.ok, "permanent": res.permanent, "status": res.status, "message_id": res.message_id, "error": res.error}


# ----------------------------------------------------------------------------- templates
class TemplateIn(Strict):
    channel: Literal["whatsapp"] = "whatsapp"
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    provider_template: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    language: str = Field("en", pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    category: Literal["utility", "authentication", "marketing"] = "utility"
    description: str | None = Field(None, max_length=300)
    body_text: str = Field(min_length=5, max_length=1024)
    media_type: Literal["none", "image", "video"] = "none"
    link_button: bool = False
    variables: list[str] = Field(default_factory=list, max_length=10)


def _check_template(t: TemplateIn) -> None:
    nums = re.findall(r"\{\{(\d+)\}\}", t.body_text)
    if nums != [str(i) for i in range(1, len(t.variables) + 1)]:
        raise ApiError(422, "variables_mismatch", "The body must use {{1}}, {{2}}, ... in order, once each, matching the variable list.")
    if re.match(r"^\s*\{\{", t.body_text) or re.search(r"\}\}\s*$", t.body_text):
        raise ApiError(422, "variable_at_edge", "WhatsApp does not accept a variable at the very start or end of the body.")
    if t.category == "utility" and re.search(r"\b(offer|discount|sale|buy now|free)\b", t.body_text, re.I):
        raise ApiError(422, "promotional_wording", "Utility templates must not sound promotional. Meta will reject it.")
    bad = [v for v in t.variables if v not in ("event", "location", "time", "details")]
    if bad:
        raise ApiError(422, "unknown_variable", f"Unknown alert detail: {bad[0]}. Use event, location, time or details.")


@router.get("/templates")
async def list_templates(ctx: AdminCtx = Depends(admin_ctx("owner", "support", "technician"))):
    return [dict(r) for r in await ctx.conn.fetch("SELECT * FROM platform.message_templates ORDER BY channel, key, language")]


@router.post("/templates", status_code=201)
async def create_template(body: TemplateIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    _check_template(body)
    try:
        row = await ctx.conn.fetchrow(
            """INSERT INTO platform.message_templates (channel, key, provider_template, language, category, description, body_text, media_type, link_button, variables)
               VALUES ($1::app.channel_kind,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""", body.channel, body.key, body.provider_template, body.language, body.category,
            body.description, body.body_text, body.media_type, body.link_button, body.variables)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "template_exists", "A template with this key and language already exists.")
    await ctx.audit("messaging.template.create", target_type="message_template", target_id=row["id"], after={"key": body.key})
    return dict(row)


@router.post("/templates/install-defaults")
async def install_defaults(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Adds Jas iVision's standard templates as drafts. Templates that already exist are left alone."""
    added = []
    for t in DEFAULT_TEMPLATES:
        r = await ctx.conn.fetchval(
            """INSERT INTO platform.message_templates (channel, key, provider_template, language, category, description, body_text, media_type, link_button, variables)
               VALUES ($1::app.channel_kind,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (channel, key, language) DO NOTHING RETURNING key""",
            t["channel"], t["key"], t["provider_template"], t["language"], t["category"], t["description"], t["body_text"], t["media_type"], t["link_button"], t["variables"])
        if r:
            added.append(r)
    await ctx.audit("messaging.template.install_defaults", after={"added": added})
    return {"added": added}


@router.get("/templates/{tid}/submission")
async def submission_sheet(tid: UUID, ctx: AdminCtx = Depends(admin_ctx("owner", "support", "technician"))):
    """Everything to type into the provider's template form, including the samples Meta requires for each variable."""
    t = await ctx.conn.fetchrow("SELECT * FROM platform.message_templates WHERE id=$1", tid)
    if not t:
        raise ApiError(404, "template_not_found", "Template not found.")
    d = next((x for x in DEFAULT_TEMPLATES if x["key"] == t["key"]), {})
    return {"name": t["provider_template"], "language": t["language"], "category": t["category"].upper(), "header": None if t["media_type"] == "none" else t["media_type"].upper(),
            "body": t["body_text"], "samples": d.get("samples", []), "button": ({"type": "URL", "text": d.get("button_text", "View evidence"), "url": d.get("button_url", "https://example.com/e/{{1}}"),
                                                                                  "sample": "Ab3dEf"} if t["link_button"] else None)}


@router.put("/templates/{tid}/status")
async def set_template_status(tid: UUID, status: Literal["draft", "submitted", "approved", "rejected", "paused"], reason: str = Query(..., min_length=5, max_length=500),
                              ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Record what the provider says about the template. Only 'approved' templates are ever used to send."""
    r = await ctx.conn.fetchrow("UPDATE platform.message_templates SET status=$2 WHERE id=$1 RETURNING key", tid, status)
    if not r:
        raise ApiError(404, "template_not_found", "Template not found.")
    await ctx.audit("messaging.template.status", target_type="message_template", target_id=tid, reason=reason, after={"key": r["key"], "status": status})
    return {"status": status}


@router.delete("/templates/{tid}", status_code=204)
async def delete_template(tid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    if not await ctx.conn.fetchval("DELETE FROM platform.message_templates WHERE id=$1 RETURNING id", tid):
        raise ApiError(404, "template_not_found", "Template not found.")
    await ctx.audit("messaging.template.delete", target_type="message_template", target_id=tid, reason=reason)


# ----------------------------------------------------------------------------- per-account limits
class TenantMessaging(Strict):
    whatsapp: bool
    email: bool
    clips: bool = True
    monthly_cap: int | None = Field(None, ge=0, le=1_000_000)
    reason: str = Field(min_length=5, max_length=500)


@router.get("/tenants/{tenant_id}")
async def get_tenant_messaging(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    r = await ctx.conn.fetchrow(
        """SELECT msg_whatsapp, msg_email, msg_clips, msg_monthly_cap, (SELECT count(*) FROM app.notifications n WHERE n.tenant_id=t.id AND n.channel='whatsapp'
                  AND n.status NOT IN ('suppressed','cancelled') AND n.created_at >= date_trunc('month', now() AT TIME ZONE t.timezone) AT TIME ZONE t.timezone) AS whatsapp_this_month
           FROM app.tenants t WHERE t.id=$1""", tenant_id)
    if not r:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    return {"whatsapp": r["msg_whatsapp"], "email": r["msg_email"], "clips": r["msg_clips"], "monthly_cap": r["msg_monthly_cap"], "whatsapp_this_month": r["whatsapp_this_month"]}


@router.put("/tenants/{tenant_id}")
async def set_tenant_messaging(tenant_id: UUID, body: TenantMessaging, ctx: AdminCtx = Depends(admin_ctx("owner", "finance"))):
    cur = await ctx.conn.fetchrow("SELECT msg_whatsapp, msg_email, msg_clips, msg_monthly_cap FROM app.tenants WHERE id=$1", tenant_id)
    if not cur:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    await ctx.conn.execute("UPDATE app.tenants SET msg_whatsapp=$2, msg_email=$3, msg_clips=$5, msg_monthly_cap=$4 WHERE id=$1", tenant_id, body.whatsapp, body.email, body.monthly_cap, body.clips)
    await ctx.audit("messaging.tenant.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason,
                    before={"whatsapp": cur["msg_whatsapp"], "email": cur["msg_email"], "clips": cur["msg_clips"], "monthly_cap": cur["msg_monthly_cap"]},
                    after={"whatsapp": body.whatsapp, "email": body.email, "clips": body.clips, "monthly_cap": body.monthly_cap})
    return {"whatsapp": body.whatsapp, "email": body.email, "clips": body.clips, "monthly_cap": body.monthly_cap}


# ----------------------------------------------------------------------------- delivery log
@router.get("/notifications")
async def all_notifications(tenant_id: UUID | None = None, status: str | None = Query(None, pattern="^(queued|sent|delivered|read|failed|suppressed|cancelled)$"),
                            limit: int = Query(50, ge=1, le=200), ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch(
        """SELECT n.id, n.tenant_id, t.name AS tenant, n.channel::text AS channel, n.recipient, n.status::text AS status, n.template, n.attempts, n.error, n.created_at, n.sent_at,
                  n.fallback_of IS NOT NULL AS is_fallback, a.title
           FROM app.notifications n JOIN app.tenants t ON t.id=n.tenant_id LEFT JOIN app.alerts a ON a.id=n.alert_id
           WHERE ($1::uuid IS NULL OR n.tenant_id=$1) AND ($2::text IS NULL OR n.status::text=$2) ORDER BY n.created_at DESC LIMIT $3""", tenant_id, status, limit)
    return [dict(r) for r in rows]


@router.get("/summary")
async def summary(ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch("SELECT channel::text AS channel, status::text AS status, count(*) AS n FROM app.notifications WHERE created_at > now() - interval '24 hours' GROUP BY 1,2")
    out: dict = {}
    for r in rows:
        out.setdefault(r["channel"], {})[r["status"]] = r["n"]
    return {"last_24_hours": out}
