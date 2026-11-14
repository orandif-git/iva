"""Face ID or fingerprint sign-in: set it up on this phone, see and remove your own, and (Owner and Admin) remove another person's after a lost phone."""
from uuid import UUID

from fastapi import APIRouter, Depends

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import PasskeyRegisterIn
from ..services import passkeys

router = APIRouter(prefix="/admin/v1/passkeys", tags=["admin: face id"])


@router.get("")
async def mine(ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch("SELECT id, name, created_at, last_used_at FROM platform.passkeys WHERE staff_id=$1 ORDER BY created_at", ctx.staff.id)
    return {"items": [dict(r) for r in rows]}


@router.post("/options")
async def options(ctx: AdminCtx = Depends(admin_ctx())):
    return await passkeys.registration_options(ctx.conn, ctx.staff.id)


@router.post("", status_code=201)
async def register(body: PasskeyRegisterIn, ctx: AdminCtx = Depends(admin_ctx())):
    row = await passkeys.register(ctx.conn, ctx.staff.id, body.clientDataJSON, body.attestationObject, body.name)
    await ctx.audit("passkey.add", target_type="staff", target_id=ctx.staff.id)
    return row


@router.delete("/{key_id}", status_code=204)
async def remove_mine(key_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    if await ctx.conn.fetchval("DELETE FROM platform.passkeys WHERE id=$1 AND staff_id=$2 RETURNING id", key_id, ctx.staff.id) is None:
        raise ApiError(404, "not_found", "That Face ID phone was not found.")
    await ctx.audit("passkey.remove", target_type="staff", target_id=ctx.staff.id)


@router.delete("/for/{staff_id}", status_code=204)
async def remove_for(staff_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """After a lost phone: end every Face ID sign-in of this person. Owner and Admin."""
    await ctx.conn.execute("DELETE FROM platform.passkeys WHERE staff_id=$1", staff_id)
    await ctx.audit("passkey.remove_all", target_type="staff", target_id=staff_id)
