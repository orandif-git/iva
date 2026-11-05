"""Staff create sign-ins for a customer's people. There is no public sign-up: an account exists only after staff invite it, and the person
sets their own password from a one-time link. Until email is wired up, the link is shown to staff, who send it (for example on WhatsApp)."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import UserInviteIn, UserPatchIn
from ..services import auth

router = APIRouter(prefix="/admin/v1", tags=["admin: customer users"])
_WHO = ("owner", "support")


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
async def reset_link(tenant_id: UUID, user_id: UUID, reason: str | None = Query(None, min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx(*_WHO))):
    """A one-time link that lets the person choose a new password (or finish an invitation). A new link cancels the old one."""
    await _tenant(ctx, tenant_id)
    u = await ctx.conn.fetchrow("SELECT id, status FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    if not u:
        raise ApiError(404, "user_not_found", "User not found.")
    if u["status"] == "disabled":
        raise ApiError(409, "user_disabled", "This user is disabled.")
    kind = "invite" if u["status"] == "invited" else "reset"
    raw, exp = await auth.make_link_token(ctx.conn, kind, user_id, ctx.staff.id)
    await ctx.audit("user.reset_link", tenant_id=tenant_id, target_type="user", target_id=user_id, reason=reason, after={"kind": kind})
    return {"user_id": user_id, "link": auth.link(raw), "expires_at": exp}


@router.post("/tenants/{tenant_id}/users/{user_id}/disable", status_code=204)
async def disable_user(tenant_id: UUID, user_id: UUID, reason: str | None = Query(None, min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    await _tenant(ctx, tenant_id)
    n = await ctx.conn.fetchval("UPDATE app.tenant_users SET status='disabled' WHERE id=$1 AND tenant_id=$2 RETURNING 1", user_id, tenant_id)
    if not n:
        raise ApiError(404, "user_not_found", "User not found.")
    await auth.revoke_all(ctx.conn, "customer", user_id)
    await ctx.audit("user.disable", tenant_id=tenant_id, target_type="user", target_id=user_id, reason=reason)


async def _last_admin_guard(ctx: AdminCtx, tenant_id: UUID, user_id: UUID) -> None:
    u = await ctx.conn.fetchrow("SELECT role::text AS role, status FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    if u and u["role"] == "admin" and u["status"] != "disabled" and not await ctx.conn.fetchval(
            "SELECT 1 FROM app.tenant_users WHERE tenant_id=$1 AND role='admin' AND status<>'disabled' AND id<>$2 LIMIT 1", tenant_id, user_id):
        raise ApiError(409, "last_admin", "This is the only administrator. Invite another administrator first.")


@router.patch("/tenants/{tenant_id}/users/{user_id}")
async def edit_user(tenant_id: UUID, user_id: UUID, body: UserPatchIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    await _tenant(ctx, tenant_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(422, "nothing_to_change", "Send at least one field to change.")
    before = await ctx.conn.fetchrow("SELECT name, email::text AS email, phone, role::text AS role FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    if not before:
        raise ApiError(404, "user_not_found", "User not found.")
    if changes.get("role") == "regular":
        await _last_admin_guard(ctx, tenant_id, user_id)
    if "email" in changes:
        changes["email"] = changes["email"].lower()
    cols = list(changes)
    sets = ", ".join(f"{c} = ${i + 3}" + ("::app.tenant_role" if c == "role" else "") for i, c in enumerate(cols))
    try:
        await ctx.conn.execute(f"UPDATE app.tenant_users SET {sets} WHERE id=$1 AND tenant_id=$2", user_id, tenant_id, *[changes[c] for c in cols])
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "email_in_use", "This email already has an account in this organisation.")
    if "email" in changes and changes["email"] != before["email"]:
        await auth.revoke_all(ctx.conn, "customer", user_id)           # the old address no longer identifies them
    await ctx.audit("user.update", tenant_id=tenant_id, target_type="user", target_id=user_id, before={c: before[c] for c in cols}, after={c: changes[c] for c in cols})
    r = await ctx.conn.fetchrow("SELECT id, email::text AS email, name, phone, role::text AS role, status FROM app.tenant_users WHERE id=$1", user_id)
    return dict(r)


@router.post("/tenants/{tenant_id}/users/{user_id}/enable", status_code=204)
async def enable_user(tenant_id: UUID, user_id: UUID, reason: str | None = Query(None, min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    await _tenant(ctx, tenant_id)
    n = await ctx.conn.fetchval("UPDATE app.tenant_users SET status = CASE WHEN password_hash IS NULL THEN 'invited' ELSE 'active' END, failed_logins=0, locked_until=NULL "
                                "WHERE id=$1 AND tenant_id=$2 AND status='disabled' RETURNING 1", user_id, tenant_id)
    if not n:
        raise ApiError(409, "not_disabled", "Only a disabled person can be enabled.")
    await ctx.audit("user.enable", tenant_id=tenant_id, target_type="user", target_id=user_id, reason=reason)


@router.delete("/tenants/{tenant_id}/users/{user_id}", status_code=204)
async def delete_user(tenant_id: UUID, user_id: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    await _tenant(ctx, tenant_id)
    u = await ctx.conn.fetchrow("SELECT email::text AS email, name FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    if not u:
        raise ApiError(404, "user_not_found", "User not found.")
    await _last_admin_guard(ctx, tenant_id, user_id)
    await auth.revoke_all(ctx.conn, "customer", user_id)
    await ctx.conn.execute("DELETE FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", user_id, tenant_id)
    await ctx.audit("user.delete", tenant_id=tenant_id, target_type="user", target_id=user_id, reason=reason, before={"email": u["email"], "name": u["name"]})
