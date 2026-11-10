"""Super-admin control of how messages leave the platform: the email server used for scheduled reports, each account's messaging limits, and the delivery log.
Alerts are not sent from here: they leave as webhook events (see admin_webhooks.py). The email server is owner-only because it holds a password."""
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
from ..messaging import delivery, netguard, smtp_provider
from ..schemas import Strict

router = APIRouter(prefix="/admin/v1/messaging", tags=["admin: messaging"])
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class ProviderIn(Strict):
    channel: Literal["email"]
    name: str = Field(min_length=2, max_length=80)
    kind: Literal["smtp"]
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
    try:
        smtp_provider.validate_config(config, allow_private=get_settings().messaging_allow_private_hosts)
    except (ValueError, netguard.BlockedAddress) as e:
        raise ApiError(422, "invalid_recipe", str(e))


def _public(r, with_config: bool = True) -> dict:
    names = sorted(json.loads(vault().decrypt(bytes(r["secrets_ciphertext"]), str(r["id"]))).keys()) if r["secrets_ciphertext"] else []
    out = {"id": r["id"], "channel": r["channel"], "name": r["name"], "kind": r["kind"], "active": r["active"], "rate_per_second": float(r["rate_per_second"]),
           "secret_names": names, "updated_at": r["updated_at"], "webhook_url": f"{get_settings().public_base_url.rstrip('/')}/hooks/messaging/{r['id']}"}
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
async def list_providers(ctx: AdminCtx = Depends(admin_ctx("owner"))):
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
    to: str = Field(min_length=3, max_length=120)             # the address to send a test to
    send: bool = False                                        # false = show what would be sent, send nothing


@router.post("/providers/{pid}/test")
async def test_provider(pid: UUID, body: TestIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Preview (default) or really send a test email, so the server can be tried before a report depends on it."""
    p = await _get(ctx, pid)
    if not body.send:
        return {"preview": {"to": body.to, "subject": "[Jas iVision] Test message", "from": p["config"].get("from_email")}}
    s = get_settings()
    res = await smtp_provider.send(p["config"], _secrets_of(p), {"to": body.to, "name": "Test", "subject": "[Jas iVision] Test message",
                                                                 "text": "This is a test message from Jas iVision.", "html": None},
                                   allow_private=s.messaging_allow_private_hosts, ssl_context=delivery.ssl_context())
    await ctx.audit("messaging.provider.test", target_type="messaging_provider", target_id=pid, after={"ok": res.ok, "to_hint": body.to[:3] + "…" + body.to[-2:]})
    return {"ok": res.ok, "permanent": res.permanent, "status": res.status, "message_id": res.message_id, "error": res.error}


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
async def set_tenant_messaging(tenant_id: UUID, body: TenantMessaging, ctx: AdminCtx = Depends(admin_ctx("owner"))):
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
        """SELECT n.id, n.tenant_id, t.name AS tenant, n.channel::text AS channel, n.recipient, n.status::text AS status, n.event, n.attempts, n.response_status, n.error, n.created_at, n.sent_at,
                  a.title
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
