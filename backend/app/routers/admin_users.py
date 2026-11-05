"""Staff create sign-ins for a customer's people. There is no public sign-up: an account exists only after staff invite it, and the person
sets their own password from a one-time link. Until email is wired up, the link is shown to staff, who send it (for example on WhatsApp)."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import UserInviteIn
from ..services import auth

router = APIRouter(prefix="/admin/v1", tags=["admin: customer users"])
_WHO = ("owner", "technician", "support")


async def _tenant(ctx: AdminCtx, tenant_id: UUID):
    t = await ctx.conn.fetchrow("SELECT id, status::text AS status FROM app.tenants WHERE id=$1", tenant_id)
    if not t:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    return t


@router.get("/tenants/{tenant_id}/users")
async def list_users(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    await _tenant(ctx, tenant_id)
    rows = await ctx.conn.fetch(
        """SELECT id, email::text AS email, name, phone, role::text AS role, status, last_login_at, (password_hash IS NOT NULL) AS has_password
           FROM app.tenant_users WHERE tenant_id=$1 ORDER BY created_at""", tenant_id)
    return {"items": [dict(r) for r in rows]}


@router.post("/tenants/{tenant_id}/users", status_code=201)
async def invite_user(tenant_id: UUID, body: UserInviteIn, ctx: AdminCtx = Depends(admin_ctx(*_WHO))):
    t = await _tenant(ctx, tenant_id)
    if t["status"] == "cancelled":
        raise ApiError(409, "tenant_cancelled", "This account was cancelled.")
    try:
        async with ctx.conn.transaction():
            uid = await ctx.conn.fetchval(
                """INSERT INTO app.tenant_users (tenant_id, email, name, phone, role, status) VALUES ($1,$2,$3,$4,$5::app.tenant_role,'invited') RETURNING id""",
                tenant_id, body.email.lower(), body.name, body.phone, body.role)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "email_in_use", "This email already has an account in this organisation.")
    raw, exp = await auth.make_link_token(ctx.conn, "invite", uid, ctx.staff.id)
    await ctx.audit("user.invite", tenant_id=tenant_id, target_type="user", target_id=uid, after={"email": body.email.lower(), "role": body.role})
    return {"user_id": uid, "email": body.email.lower(), "role": body.role, "link": auth.link(raw), "expires_at": exp}


@router.post("/tenants/{tenant_id}/users/{user_id}/reset-link", status_code=201)
async def reset_link(tenant_id: UUID, user_id: UUID, ctx: AdminCtx = Depends(admin_ctx(*_WHO))):
    """A one-time link that lets the person choose a new password (or finish an invitation). A new link cancels the old one."""
    await _tenant(ctx, tenant_id)
    u = await ctx.conn.fetchrow("SELECT id, status FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    if not u:
        raise ApiError(404, "user_not_found", "User not found.")
    if u["status"] == "disabled":
        raise ApiError(409, "user_disabled", "This user is disabled.")
    kind = "invite" if u["status"] == "invited" else "reset"
    raw, exp = await auth.make_link_token(ctx.conn, kind, user_id, ctx.staff.id)
    await ctx.audit("user.reset_link", tenant_id=tenant_id, target_type="user", target_id=user_id, after={"kind": kind})
    return {"user_id": user_id, "link": auth.link(raw), "expires_at": exp}


@router.post("/tenants/{tenant_id}/users/{user_id}/disable", status_code=204)
async def disable_user(tenant_id: UUID, user_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    await _tenant(ctx, tenant_id)
    n = await ctx.conn.fetchval("UPDATE app.tenant_users SET status='disabled' WHERE id=$1 AND tenant_id=$2 RETURNING 1", user_id, tenant_id)
    if not n:
        raise ApiError(404, "user_not_found", "User not found.")
    await auth.revoke_all(ctx.conn, "customer", user_id)
    await ctx.audit("user.disable", tenant_id=tenant_id, target_type="user", target_id=user_id)
