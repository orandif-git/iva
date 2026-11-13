"""Owner-only management of the people who run the platform. A new person gets a one-time link to choose their own password.
Four roles: owner (everything), admin (everything except the WhatsApp connection and making Owners), onboarding (sets up new customers) and support (looks after live customers)."""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import StaffCreateIn, StaffPatchIn
from ..services import auth

router = APIRouter(prefix="/admin/v1/staff", tags=["admin: staff"])
COLS = "id, name, email::text AS email, role::text AS role, status, last_login_at, (password_hash IS NOT NULL) AS has_password, created_at"


async def _get(ctx: AdminCtx, sid: UUID):
    r = await ctx.conn.fetchrow(f"SELECT {COLS} FROM platform.staff_users WHERE id=$1", sid)
    if not r:
        raise ApiError(404, "staff_not_found", "Staff member not found.")
    return r


async def _another_owner(ctx: AdminCtx, sid: UUID) -> bool:
    return bool(await ctx.conn.fetchval("SELECT 1 FROM platform.staff_users WHERE role='owner' AND status='active' AND id<>$1 LIMIT 1", sid))


@router.get("")
async def list_staff(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    return {"items": [dict(r) for r in await ctx.conn.fetch(f"SELECT {COLS} FROM platform.staff_users ORDER BY created_at")]}


def _guard(ctx: AdminCtx, role_after: str | None, role_now: str | None = None) -> None:
    """An Admin runs the team but cannot make or change an Owner."""
    if ctx.staff.role != "owner" and "owner" in (role_after, role_now):
        raise ApiError(403, "owner_only", "Only an Owner can make or change an Owner.")


@router.post("", status_code=201)
async def create_staff(body: StaffCreateIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    _guard(ctx, body.role)
    try:
        sid = await ctx.conn.fetchval("INSERT INTO platform.staff_users (email, name, role) VALUES ($1,$2,$3::platform.staff_role) RETURNING id", body.email.lower(), body.name, body.role)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "email_in_use", "This email already has a staff account.")
    raw, exp = await auth.make_link_token(ctx.conn, "invite", sid, ctx.staff.id)
    await ctx.audit("staff.create", target_type="staff", target_id=sid, after={"email": body.email.lower(), "role": body.role})
    return {"staff_id": sid, "email": body.email.lower(), "role": body.role, "link": auth.link(raw), "expires_at": exp}


@router.patch("/{sid}")
async def edit_staff(sid: UUID, body: StaffPatchIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, sid)
    ch = body.model_dump(exclude_unset=True)
    _guard(ctx, ch.get("role"), cur["role"])
    if not ch:
        raise ApiError(422, "nothing_to_change", "Send at least one field to change.")
    if "role" in ch and ch["role"] != cur["role"]:
        if sid == ctx.staff.id:
            raise ApiError(409, "own_role", "You cannot change your own role. Ask another owner.")
        if cur["role"] == "owner" and not await _another_owner(ctx, sid):
            raise ApiError(409, "last_owner", "There must always be one active owner.")
    email = ch.get("email", cur["email"])
    try:
        await ctx.conn.execute("UPDATE platform.staff_users SET name=$2, role=$3::platform.staff_role, email=$4 WHERE id=$1", sid, ch.get("name", cur["name"]), ch.get("role", cur["role"]), email)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "email_taken", "Another person already uses this email.")
    if ch.get("role", cur["role"]) != cur["role"]:
        await auth.revoke_all(ctx.conn, sid)            # a new role starts with a fresh sign-in, so the old permissions cannot linger
    await ctx.audit("staff.update", target_type="staff", target_id=sid, before={"name": cur["name"], "role": cur["role"], "email": cur["email"]},
                    after={"name": ch.get("name", cur["name"]), "role": ch.get("role", cur["role"]), "email": email})
    return dict(await _get(ctx, sid))


@router.delete("/{sid}", status_code=204)
async def delete_staff(sid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Removes a person for good. Not yourself, and never the last active Owner. The records of what they looked at stay, with their name."""
    cur = await _get(ctx, sid)
    _guard(ctx, None, cur["role"])
    if sid == ctx.staff.id:
        raise ApiError(409, "own_account", "You cannot delete your own account.")
    if cur["role"] == "owner" and cur["status"] == "active" and not await _another_owner(ctx, sid):
        raise ApiError(409, "last_owner", "There must always be one active owner.")
    await auth.revoke_all(ctx.conn, sid)
    try:
        async with ctx.conn.transaction():
            await ctx.conn.execute("DELETE FROM platform.staff_users WHERE id=$1", sid)
    except asyncpg.ForeignKeyViolationError:
        raise ApiError(409, "has_history", "This person has history that must be kept, so they cannot be deleted. Switch them off instead.")
    await ctx.audit("staff.delete", target_type="staff", target_id=sid, reason=reason, before={"name": cur["name"], "email": cur["email"], "role": cur["role"]})


@router.post("/{sid}/disable", status_code=204)
async def disable_staff(sid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, sid)
    _guard(ctx, None, cur["role"])
    if sid == ctx.staff.id:
        raise ApiError(409, "own_account", "You cannot disable your own account.")
    if cur["role"] == "owner" and not await _another_owner(ctx, sid):
        raise ApiError(409, "last_owner", "There must always be one active owner.")
    await ctx.conn.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", sid)
    await auth.revoke_all(ctx.conn, sid)
    await ctx.audit("staff.disable", target_type="staff", target_id=sid, reason=reason)


@router.post("/{sid}/enable", status_code=204)
async def enable_staff(sid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    _guard(ctx, None, (await _get(ctx, sid))["role"])
    await ctx.conn.execute("UPDATE platform.staff_users SET status='active', failed_logins=0, locked_until=NULL WHERE id=$1", sid)
    await ctx.audit("staff.enable", target_type="staff", target_id=sid, reason=reason)


@router.post("/{sid}/reset-link", status_code=201)
async def staff_reset_link(sid: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, sid)
    _guard(ctx, None, cur["role"])
    if cur["status"] != "active":
        raise ApiError(409, "staff_disabled", "This account is disabled.")
    raw, exp = await auth.make_link_token(ctx.conn, "reset" if cur["has_password"] else "invite", sid, ctx.staff.id)
    await ctx.audit("staff.reset_link", target_type="staff", target_id=sid, reason=reason)
    return {"staff_id": sid, "link": auth.link(raw), "expires_at": exp}
