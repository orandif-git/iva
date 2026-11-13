import re
import secrets
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..pagination import decode_cursor, encode_cursor
from ..schemas import ReasonIn, TenantCreate, TenantOut, TenantPatch

router = APIRouter(prefix="/admin/v1", tags=["admin: tenants"])

TENANT_SELECT = """
SELECT t.id, t.name, t.slug::text AS slug, t.city, t.state, t.status::text AS status, t.plan_id, t.partner_id, t.camera_limit,
       t.price_override_paise, t.trial_ends_at, t.ends_on, t.msg_language, t.owner_name, t.owner_email, coalesce((SELECT array_agg(o.phone ORDER BY o.position) FROM platform.tenant_owner_phones o WHERE o.tenant_id=t.id), '{}') AS owner_phones, t.retention_events_days, t.retention_snapshots_days, t.suspended_at, t.suspended_reason, t.created_at,
       (SELECT string_agg(r.name, ', ' ORDER BY r.name) FROM app.recipients r WHERE r.tenant_id=t.id AND r.active) AS people,
       (SELECT max(n.created_at) FROM app.notifications n WHERE n.tenant_id=t.id) AS last_message_at,
       (SELECT count(*) FROM app.cameras c WHERE c.tenant_id=t.id AND c.enabled) AS cameras_in_use,
       (SELECT count(*) FROM app.recorders r WHERE r.tenant_id=t.id) AS recorders_total,
       (SELECT count(*) FROM app.recorders r WHERE r.tenant_id=t.id AND r.status='online') AS recorders_online
FROM app.tenants t
"""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "tenant"


async def _get(ctx: AdminCtx, tenant_id: UUID) -> dict:
    row = await ctx.conn.fetchrow(TENANT_SELECT + " WHERE t.id=$1", tenant_id)
    if not row:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    return dict(row)


@router.get("/tenants")
async def list_tenants(
    status: str | None = Query(None, pattern="^(trial|active|past_due|suspended|cancelled)$"),
    partner_id: UUID | None = None,
    q: str | None = Query(None, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    ctx: AdminCtx = Depends(admin_ctx()),
):
    where, args = [], []

    def add(sql, value):
        args.append(value)
        where.append(sql.replace("?", f"${len(args)}"))

    if status:
        add("t.status = ?::app.tenant_status", status)
    if partner_id:
        add("t.partner_id = ?", partner_id)
    if q:
        add("(t.name ILIKE ? OR t.city ILIKE ?)", f"%{q}%")   # both ? become the same parameter
    c = decode_cursor(cursor)
    if c:
        args.extend([datetime.fromisoformat(c["created_at"]), UUID(c["id"])])
        where.append(f"(t.created_at, t.id) < (${len(args) - 1}, ${len(args)})")
    args.append(limit + 1)
    sql = TENANT_SELECT + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY t.created_at DESC, t.id DESC LIMIT ${len(args)}"
    rows = [dict(r) for r in await ctx.conn.fetch(sql, *args)]
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = encode_cursor({"created_at": rows[-1]["created_at"].isoformat(), "id": str(rows[-1]["id"])}) if more else None
    return {"items": [TenantOut(**r) for r in rows], "next_cursor": nxt}


def _owner_phones(raw: list[str]) -> list[str]:
    """The owner's numbers in one form (+91XXXXXXXXXX). The same number twice is refused."""
    from .shop_alerts import normalize_phone
    out = [normalize_phone(p) for p in raw]
    if len(set(out)) != len(out):
        raise ApiError(422, "duplicate_owner_phone", "The same number is entered twice.")
    return out


async def _set_owner_phones(conn, tenant_id, phones: list[str]) -> None:
    """Saves the owner's numbers. A number that is already an owner number of another customer is refused, with that customer's name."""
    for p in phones:
        other = await conn.fetchrow("SELECT t.name FROM platform.tenant_owner_phones o JOIN app.tenants t ON t.id=o.tenant_id WHERE o.phone=$1 AND o.tenant_id<>$2", p, tenant_id)
        if other:
            raise ApiError(409, "owner_phone_taken", f"{p} is already an owner number of {other['name']}.")
    await conn.execute("DELETE FROM platform.tenant_owner_phones WHERE tenant_id=$1", tenant_id)
    for i, p in enumerate(phones, 1):
        await conn.execute("INSERT INTO platform.tenant_owner_phones (tenant_id, phone, position) VALUES ($1,$2,$3)", tenant_id, p, i)


def _masked(phones) -> list[str]:
    return [p[:3] + "•" * max(len(p) - 7, 0) + p[-4:] for p in phones]


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, ctx: AdminCtx = Depends(admin_ctx("owner", "onboarding"))):
    if not await ctx.conn.fetchval("SELECT 1 FROM platform.plans WHERE id=$1 AND active", body.plan_id):
        raise ApiError(422, "unknown_plan", "That plan does not exist or is not active.")
    if body.partner_id and not await ctx.conn.fetchval("SELECT 1 FROM platform.partners WHERE id=$1 AND status='active'", body.partner_id):
        raise ApiError(422, "unknown_partner", "That partner does not exist or is paused.")
    phones = _owner_phones(body.owner_phones)
    for p in phones:                                        # a clear refusal before anything is made
        other = await ctx.conn.fetchval("SELECT t.name FROM platform.tenant_owner_phones o JOIN app.tenants t ON t.id=o.tenant_id WHERE o.phone=$1", p)
        if other:
            raise ApiError(409, "owner_phone_taken", f"{p} is already an owner number of {other}.")
    base = _slug(body.name)
    tenant_id = None
    for attempt in range(5):
        slug = base if attempt == 0 else f"{base}-{secrets.token_hex(2)}"
        try:
            async with ctx.conn.transaction():
                tenant_id = await ctx.conn.fetchval(
                    """INSERT INTO app.tenants (name, slug, city, plan_id, partner_id, camera_limit, status, trial_ends_at, ends_on, msg_language, retention_events_days, retention_snapshots_days,
                                               owner_name, owner_email)
                       VALUES ($1,$2,$3,$4,$5,$6,'trial', now() + interval '14 days', $7, $8, $9, $9, $10, $11) RETURNING id""",
                    body.name, slug, body.city, body.plan_id, body.partner_id, body.camera_limit, body.ends_on, body.msg_language,
                    await ctx.conn.fetchval("SELECT coalesce((SELECT keep_days FROM platform.data_retention WHERE id), 30)"), body.owner_name, body.owner_email or None,
                )
                await _set_owner_phones(ctx.conn, tenant_id, phones)
            break
        except asyncpg.UniqueViolationError:
            continue
    if tenant_id is None:
        raise ApiError(409, "slug_conflict", "Try a slightly different name.")
    out = await _get(ctx, tenant_id)
    await ctx.audit("tenant.create", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, after={"name": body.name, "plan_id": body.plan_id, "owner_phones": _masked(phones)})
    return out


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    return await _get(ctx, tenant_id)


PROFILE_FIELDS = {"name", "city", "owner_name", "owner_email", "owner_phones"}      # Onboarding may correct these; the rest is for the Owner and Admin


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def patch_tenant(tenant_id: UUID, body: TenantPatch, ctx: AdminCtx = Depends(admin_ctx("owner", "onboarding"))):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "nothing_to_change", "Send at least one field to change.")
    if ctx.staff.role == "onboarding" and set(changes) - PROFILE_FIELDS:
        raise ApiError(403, "forbidden", "Your role can change the name, city and owner details only.")
    before = await _get(ctx, tenant_id)
    phones = _owner_phones(changes.pop("owner_phones")) if "owner_phones" in changes else None
    if "owner_email" in changes:
        changes["owner_email"] = changes["owner_email"] or None
    if "owner_name" in changes and not changes["owner_name"]:
        raise ApiError(422, "owner_name_required", "The owner needs a name.")
    if "camera_limit" in changes and changes["camera_limit"] < before["cameras_in_use"]:
        raise ApiError(409, "limit_below_usage", f"{before['cameras_in_use']} cameras are already in use. Remove some first.")
    if changes.get("partner_id") and not await ctx.conn.fetchval("SELECT 1 FROM platform.partners WHERE id=$1", changes["partner_id"]):
        raise ApiError(422, "unknown_partner", "That partner does not exist.")
    cols = list(changes)                                   # keys are validated by the model, never by the client
    async with ctx.conn.transaction():
        if cols:
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await ctx.conn.execute(f"UPDATE app.tenants SET {sets} WHERE id = $1", tenant_id, *[changes[c] for c in cols])
        if phones is not None:
            await _set_owner_phones(ctx.conn, tenant_id, phones)
    after = await _get(ctx, tenant_id)
    b, a = {c: before[c] for c in cols}, {c: after[c] for c in cols}
    if phones is not None:                                 # the activity log keeps only the last four digits
        b["owner_phones"], a["owner_phones"] = _masked(before["owner_phones"]), _masked(after["owner_phones"])
    await ctx.audit("tenant.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, before=b, after=a)
    return after


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
async def suspend(tenant_id: UUID, body: ReasonIn, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    before = await _get(ctx, tenant_id)
    if before["status"] == "suspended":
        raise ApiError(409, "already_suspended", "This tenant is already suspended.")
    await ctx.conn.execute("UPDATE app.tenants SET status='suspended', suspended_at=now(), suspended_reason=$2 WHERE id=$1", tenant_id, body.reason)
    await ctx.audit("tenant.suspend", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, before={"status": before["status"]}, after={"status": "suspended"})
    return await _get(ctx, tenant_id)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantOut)
async def reactivate(tenant_id: UUID, body: ReasonIn, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    before = await _get(ctx, tenant_id)
    if before["status"] != "suspended":
        raise ApiError(409, "not_suspended", "Only a suspended tenant can be reactivated.")
    await ctx.conn.execute("UPDATE app.tenants SET status='active', suspended_at=NULL, suspended_reason=NULL WHERE id=$1", tenant_id)
    await ctx.audit("tenant.reactivate", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, before={"status": "suspended"}, after={"status": "active"})
    return await _get(ctx, tenant_id)


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Removes a customer and everything of theirs (recorders, cameras, people, alerts, messages). It cannot be undone. The record of who did it stays in the audit log."""
    before = await _get(ctx, tenant_id)
    await ctx.audit("tenant.delete", tenant_id=None, target_type="tenant", target_id=tenant_id, reason=reason, before={"name": before["name"], "status": before["status"]})
    await ctx.conn.execute("DELETE FROM app.tenants WHERE id=$1", tenant_id)


# ------------------------------------------------------------ view as customer
