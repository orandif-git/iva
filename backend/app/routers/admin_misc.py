from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..db import db
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..pagination import decode_cursor, encode_cursor
from ..schemas import Name, Secret, Strict
from ..services import auth, passwords
from pydantic import Field

router = APIRouter(prefix="/admin/v1", tags=["admin"])


async def _devices(conn, staff_id) -> int:
    """How many places this person is signed in on right now (each sign-in is one family of refresh tokens)."""
    return await conn.fetchval("SELECT count(DISTINCT family) FROM platform.auth_tokens WHERE account_type='staff' AND account_id=$1 AND kind='refresh' AND revoked_at IS NULL AND used_at IS NULL AND expires_at > now()", staff_id) or 0


@router.get("/me")
async def me(ctx: AdminCtx = Depends(admin_ctx())):
    r = await ctx.conn.fetchrow("SELECT email::text AS email, last_login_at FROM platform.staff_users WHERE id=$1", ctx.staff.id)
    return {"id": ctx.staff.id, "name": ctx.staff.name, "role": ctx.staff.role, "email": r["email"], "last_login_at": r["last_login_at"], "devices": await _devices(ctx.conn, ctx.staff.id)}


class MePatch(Strict):
    name: Name


class MePassword(Strict):
    current_password: Secret
    new_password: Secret
    refresh_token: str | None = Field(None, max_length=600)        # this device's own sign-in, so it stays signed in


class MeDevices(Strict):
    refresh_token: str | None = Field(None, max_length=600)


async def _my_family(conn, staff_id, refresh_token: str | None):
    if not refresh_token:
        return None
    return await conn.fetchval("SELECT family FROM platform.auth_tokens WHERE token_hash=$1 AND kind='refresh' AND account_type='staff' AND account_id=$2 AND revoked_at IS NULL",
                               auth.token_hash(refresh_token), staff_id)


@router.patch("/me")
async def edit_me(body: MePatch, ctx: AdminCtx = Depends(admin_ctx())):
    """Your own name. (Your email and role are changed by an Owner or Admin under Console users.)"""
    await ctx.conn.execute("UPDATE platform.staff_users SET name=$2 WHERE id=$1", ctx.staff.id, body.name)
    await ctx.audit("staff.profile", target_type="staff", target_id=ctx.staff.id, before={"name": ctx.staff.name}, after={"name": body.name})
    return {"id": ctx.staff.id, "name": body.name, "role": ctx.staff.role}


@router.post("/me/change-password", status_code=204)
async def change_my_password(body: MePassword, ctx: AdminCtx = Depends(admin_ctx())):
    """Needs the current password. Wrong tries count like wrong sign-ins do. Every other device is signed out; this one stays."""
    u = await ctx.conn.fetchrow("SELECT id, email::text AS email, password_hash FROM platform.staff_users WHERE id=$1", ctx.staff.id)
    if not await passwords.verify_async(body.current_password, u["password_hash"]):
        async with db.admin_tx() as c2:                            # kept even though this request ends in an error
            await auth._record_failure(c2, "platform.staff_users", [u["id"]])
            await AdminCtx(c2, ctx.staff, ctx.request_id, ctx.ip).audit("staff.password_change_failed", target_type="staff", target_id=u["id"])
        raise ApiError(401, "wrong_credentials", "Your current password is not right.")
    if body.new_password == body.current_password:
        raise ApiError(422, "same_password", "Choose a password you have not used before.")
    passwords.check_policy(body.new_password, u["email"])
    keep = await _my_family(ctx.conn, u["id"], body.refresh_token)
    await ctx.conn.execute("UPDATE platform.staff_users SET password_hash=$2, failed_logins=0, locked_until=NULL WHERE id=$1", u["id"], await passwords.hash_async(body.new_password))
    await auth.revoke_all(ctx.conn, u["id"], except_family=keep)
    await ctx.audit("staff.password_change", target_type="staff", target_id=u["id"], after={"other_devices_signed_out": True})


@router.post("/me/sign-out-others")
async def sign_out_my_other_devices(body: MeDevices, ctx: AdminCtx = Depends(admin_ctx())):
    keep = await _my_family(ctx.conn, ctx.staff.id, body.refresh_token)
    if keep is None:
        raise ApiError(409, "no_session", "Sign in again first, so this device can be kept signed in.")
    await auth.revoke_all(ctx.conn, ctx.staff.id, except_family=keep)
    await ctx.audit("staff.sign_out_others", target_type="staff", target_id=ctx.staff.id)
    return {"devices": await _devices(ctx.conn, ctx.staff.id)}


@router.get("/overview")
async def overview(ctx: AdminCtx = Depends(admin_ctx())):
    c = ctx.conn
    tenants = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.tenants GROUP BY 1")}
    rec = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.recorders GROUP BY 1")}
    in_use = await c.fetchval("SELECT count(*) FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE c.enabled AND t.status <> 'suspended'")
    licensed = await c.fetchval("SELECT coalesce(sum(camera_limit),0) FROM app.tenants WHERE status <> 'suspended'")
    states = {r["stream_state"]: r["n"] for r in await c.fetch("SELECT stream_state, count(*) AS n FROM app.cameras WHERE enabled GROUP BY 1")}
    out = {"tenants": tenants, "recorders": rec, "cameras_in_use": in_use, "cameras_licensed": licensed, "cameras_by_state": states, "mrr_paise": None}
    if ctx.staff.role == "owner":                       # revenue is visible to the owner only
        out["mrr_paise"] = await c.fetchval(
            """SELECT coalesce(sum(coalesce(t.price_override_paise, p.price_per_camera_paise) * x.n),0)::bigint
               FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id
               CROSS JOIN LATERAL (SELECT count(*) AS n FROM app.cameras c WHERE c.tenant_id=t.id AND c.enabled) x
               WHERE t.status IN ('active','past_due')""")
    return out


@router.get("/audit")
async def audit_log(
    action: str | None = Query(None, max_length=60),
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
    q: str | None = Query(None, max_length=80),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = None,
    ctx: AdminCtx = Depends(admin_ctx("owner", "support")),
):
    where, args = [], []
    for col, val in (("action", action), ("tenant_id", tenant_id), ("actor_id", actor_id)):
        if val is not None:
            args.append(val); where.append(f"{col} = ${len(args)}")
    if q:
        args.append(f"%{q}%"); where.append(f"(actor_name ILIKE ${len(args)} OR action ILIKE ${len(args)} OR reason ILIKE ${len(args)})")
    if since:
        args.append(since); where.append(f"ts >= ${len(args)}")
    if until:
        args.append(until); where.append(f"ts < ${len(args)}")
    c = decode_cursor(cursor)
    if c:
        args.append(int(c["id"])); where.append(f"id < ${len(args)}")
    args.append(limit + 1)
    rows = [dict(r) for r in await ctx.conn.fetch(
        """SELECT id, ts, actor_type::text AS actor_type, actor_id, actor_name, actor_role, tenant_id, action, target_type, target_id, reason, host(ip) AS ip
           FROM platform.audit_log""" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY id DESC LIMIT ${len(args)}", *args)]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "next_cursor": encode_cursor({"id": rows[-1]["id"]}) if more else None}


# ------------------------------------------------------------------------------ the front page: what needs a person
def _since(ts) -> str:
    secs = max(0, int((datetime.now(timezone.utc) - ts).total_seconds())) if ts else None
    if secs is None:
        return "never"
    if secs < 3600:
        return f"{max(1, secs // 60)} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


