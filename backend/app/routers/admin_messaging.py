"""Super-admin control of how messages leave the platform: the email server used for scheduled reports, each account's messaging limits, and the delivery log.
Alerts are not sent from here: they leave as webhook events (see admin_webhooks.py). The email server is owner-only because it holds a password."""
import json
import re
from typing import Literal
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..config import get_settings
from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..messaging import netguard, smtp_provider
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
class TestIn(Strict):
    to: str = Field(min_length=3, max_length=120)             # the address to send a test to
    send: bool = False                                        # false = show what would be sent, send nothing


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
    await ctx.conn.execute("UPDATE app.tenants SET msg_whatsapp=$2, msg_email=$3, msg_clips=$4, msg_monthly_cap=NULL WHERE id=$1", tenant_id, body.whatsapp, body.email, body.clips)       # no monthly limit for now: one that is sent is not kept
    await ctx.audit("messaging.tenant.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason,
                    before={"whatsapp": cur["msg_whatsapp"], "email": cur["msg_email"], "clips": cur["msg_clips"], "monthly_cap": cur["msg_monthly_cap"]},
                    after={"whatsapp": body.whatsapp, "email": body.email, "clips": body.clips})
    return {"whatsapp": body.whatsapp, "email": body.email, "clips": body.clips, "monthly_cap": None}


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


_GROUPS = {"Sent": ("sent", "delivered", "read"), "Failed": ("failed",), "Waiting": ("queued",), "Held back": ("suppressed", "cancelled")}


@router.get("/log")
async def message_log(
    date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
    tenant_id: UUID | None = None, group: str | None = Query(None, pattern="^(Sent|Failed|Waiting|Held back)$"),
    q: str | None = Query(None, max_length=80), limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=100000),
    ctx: AdminCtx = Depends(admin_ctx()),
):
    """The message log for a range of days (India time, both ends included), with the search done here so it finds old messages too. Returns the page and the total."""
    where, args = [], []

    def add(sql, v):
        args.append(v)
        where.append(sql.replace("?", f"${len(args)}"))

    if date_from:
        add("n.created_at >= (?::date)::timestamp AT TIME ZONE 'Asia/Kolkata'", date_from)
    if date_to:
        add("n.created_at < ((?::date) + 1)::timestamp AT TIME ZONE 'Asia/Kolkata'", date_to)
    if tenant_id:
        add("n.tenant_id = ?", tenant_id)
    if group:
        add("n.status::text = ANY(?::text[])", list(_GROUPS[group]))
    if q and q.strip():
        add("(t.name ILIKE ? OR n.recipient ILIKE ? OR n.error ILIKE ? OR n.event ILIKE ? OR a.title ILIKE ?)", "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM app.notifications n JOIN app.tenants t ON t.id=n.tenant_id LEFT JOIN app.alerts a ON a.id=n.alert_id{w}"
    total = await ctx.conn.fetchval(f"SELECT count(*) {base}", *args)
    rows = await ctx.conn.fetch(
        f"""SELECT n.id, n.tenant_id, t.name AS tenant, n.channel::text AS channel, n.recipient, n.status::text AS status, n.event, n.attempts, n.response_status, n.error, n.created_at, n.sent_at, a.title
            {base} ORDER BY n.created_at DESC, n.id LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}""", *args, limit, offset)
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/summary")
async def summary(ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch("SELECT channel::text AS channel, status::text AS status, count(*) AS n FROM app.notifications WHERE created_at > now() - interval '24 hours' GROUP BY 1,2")
    out: dict = {}
    for r in rows:
        out.setdefault(r["channel"], {})[r["status"]] = r["n"]
    return {"last_24_hours": out}
