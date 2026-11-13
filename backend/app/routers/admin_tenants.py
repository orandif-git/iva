import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..pagination import decode_cursor, encode_cursor
from ..schemas import ImpersonateIn, ImpersonateOut, PlanIn, ReasonIn, TenantCreate, TenantOut, TenantPatch
from ..security import issue_token

router = APIRouter(prefix="/admin/v1", tags=["admin: tenants"])

TENANT_SELECT = """
SELECT t.id, t.name, t.slug::text AS slug, t.city, t.state, t.status::text AS status, t.plan_id, t.partner_id, t.camera_limit,
       t.price_override_paise, t.trial_ends_at, t.ends_on, t.msg_language, t.retention_events_days, t.retention_snapshots_days, t.suspended_at, t.suspended_reason, t.created_at,
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


@router.get("/tenants/{tenant_id}/health")
async def tenant_health(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    """How a customer is doing right now, for the customer page."""
    await _get(ctx, tenant_id)
    c = ctx.conn
    cams = await c.fetchrow("SELECT count(*) FILTER (WHERE enabled) AS enabled, count(*) FILTER (WHERE enabled AND stream_state='streaming') AS streaming, max(last_frame_at) AS last_frame FROM app.cameras WHERE tenant_id=$1", tenant_id)
    login = await c.fetchval("SELECT max(last_login_at) FROM app.tenant_users WHERE tenant_id=$1", tenant_id)
    return {"cameras_enabled": cams["enabled"], "cameras_streaming": cams["streaming"], "last_frame_at": cams["last_frame"], "last_sign_in_at": login,
            "alerts_7d": await c.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND created_at > now() - interval '7 days'", tenant_id),
            "messages_failed_7d": await c.fetchval("SELECT count(*) FROM app.notifications WHERE tenant_id=$1 AND status='failed' AND created_at > now() - interval '7 days'", tenant_id)}


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, ctx: AdminCtx = Depends(admin_ctx("owner", "onboarding"))):
    if not await ctx.conn.fetchval("SELECT 1 FROM platform.plans WHERE id=$1 AND active", body.plan_id):
        raise ApiError(422, "unknown_plan", "That plan does not exist or is not active.")
    if body.partner_id and not await ctx.conn.fetchval("SELECT 1 FROM platform.partners WHERE id=$1 AND status='active'", body.partner_id):
        raise ApiError(422, "unknown_partner", "That partner does not exist or is paused.")
    base = _slug(body.name)
    tenant_id = None
    for attempt in range(5):
        slug = base if attempt == 0 else f"{base}-{secrets.token_hex(2)}"
        try:
            async with ctx.conn.transaction():
                tenant_id = await ctx.conn.fetchval(
                    """INSERT INTO app.tenants (name, slug, city, plan_id, partner_id, camera_limit, status, trial_ends_at, ends_on, msg_language, retention_events_days, retention_snapshots_days)
                       VALUES ($1,$2,$3,$4,$5,$6,'trial', now() + interval '14 days', $7, $8, $9, $9) RETURNING id""",
                    body.name, slug, body.city, body.plan_id, body.partner_id, body.camera_limit, body.ends_on, body.msg_language,
                    await ctx.conn.fetchval("SELECT coalesce((SELECT keep_days FROM platform.data_retention WHERE id), 30)"),
                )
            break
        except asyncpg.UniqueViolationError:
            continue
    if tenant_id is None:
        raise ApiError(409, "slug_conflict", "Try a slightly different name.")
    out = await _get(ctx, tenant_id)
    await ctx.audit("tenant.create", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, after={"name": body.name, "plan_id": body.plan_id})
    return out


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    return await _get(ctx, tenant_id)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def patch_tenant(tenant_id: UUID, body: TenantPatch, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "nothing_to_change", "Send at least one field to change.")
    before = await _get(ctx, tenant_id)
    if "camera_limit" in changes and changes["camera_limit"] < before["cameras_in_use"]:
        raise ApiError(409, "limit_below_usage", f"{before['cameras_in_use']} cameras are already in use. Remove some first.")
    if changes.get("partner_id") and not await ctx.conn.fetchval("SELECT 1 FROM platform.partners WHERE id=$1", changes["partner_id"]):
        raise ApiError(422, "unknown_partner", "That partner does not exist.")
    cols = list(changes)                                   # keys are validated by the model, never by the client
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    await ctx.conn.execute(f"UPDATE app.tenants SET {sets} WHERE id = $1", tenant_id, *[changes[c] for c in cols])
    after = await _get(ctx, tenant_id)
    await ctx.audit("tenant.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id,
                    before={c: before[c] for c in cols}, after={c: after[c] for c in cols})
    return after


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
async def suspend(tenant_id: UUID, body: ReasonIn, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    before = await _get(ctx, tenant_id)
    if before["status"] == "suspended":
        raise ApiError(409, "already_suspended", "This tenant is already suspended.")
    await ctx.conn.execute("UPDATE app.tenants SET status='suspended', suspended_at=now(), suspended_reason=$2 WHERE id=$1", tenant_id, body.reason)
    # End any open view-as-customer sessions for this tenant.
    await ctx.conn.execute("UPDATE platform.impersonation_sessions SET ended_at=now() WHERE tenant_id=$1 AND ended_at IS NULL", tenant_id)
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


@router.post("/tenants/{tenant_id}/plan", response_model=TenantOut)
async def change_plan(tenant_id: UUID, body: PlanIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    plan = await ctx.conn.fetchrow("SELECT id, features FROM platform.plans WHERE id=$1 AND active", body.plan_id)
    if not plan:
        raise ApiError(422, "unknown_plan", "That plan does not exist or is not active.")
    before = await _get(ctx, tenant_id)
    if before["plan_id"] == body.plan_id:
        raise ApiError(409, "same_plan", "The tenant is already on this plan.")
    if not plan["features"].get("anpr") and await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND use_anpr AND enabled", tenant_id):
        raise ApiError(409, "plan_feature_in_use", "Cameras are using number plate reading, which this plan does not include. Turn it off first.")
    await ctx.conn.execute("UPDATE app.tenants SET plan_id=$2, price_override_paise=NULL WHERE id=$1", tenant_id, body.plan_id)
    await ctx.audit("plan.change", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, before={"plan_id": before["plan_id"]}, after={"plan_id": body.plan_id})
    return await _get(ctx, tenant_id)


# ------------------------------------------------------------ view as customer
@router.post("/tenants/{tenant_id}/impersonate", response_model=ImpersonateOut, status_code=201)
async def impersonate(tenant_id: UUID, body: ImpersonateIn, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    await _get(ctx, tenant_id)
    expires = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
    sid = await ctx.conn.fetchval(
        """INSERT INTO platform.impersonation_sessions (staff_id, tenant_id, reason, notified, expires_at)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        ctx.staff.id, tenant_id, body.reason, body.notify, expires,
    )
    await ctx.audit("impersonate.start", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, after={"session_id": str(sid), "minutes": body.minutes})
    # TODO(notifications slice): when body.notify is true, email the tenant admins that support is viewing the account.
    token = issue_token("customer", {"sub": str(ctx.staff.id), "tenant_id": str(tenant_id), "imp": str(sid), "read_only": True},
                        ttl_seconds=body.minutes * 60)
    return ImpersonateOut(session_id=sid, token=token, expires_at=expires)


@router.delete("/impersonations/{session_id}", status_code=204)
async def end_impersonation(session_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    s = await ctx.conn.fetchrow("SELECT staff_id, tenant_id, started_at, ended_at FROM platform.impersonation_sessions WHERE id=$1", session_id)
    if not s:
        raise ApiError(404, "session_not_found", "Session not found.")
    if s["staff_id"] != ctx.staff.id and ctx.staff.role != "owner":
        raise ApiError(403, "forbidden", "Only the person who started a session, or an owner, can end it.")
    if s["ended_at"] is None:
        await ctx.conn.execute("UPDATE platform.impersonation_sessions SET ended_at=now() WHERE id=$1", session_id)
        await ctx.audit("impersonate.end", tenant_id=s["tenant_id"], target_type="impersonation", target_id=session_id, reason="Session ended")
