"""Staff can see every recorder and how to connect it. Customers connect their own Dahua recorders, rename them and remove them;
only staff can add the other types (GB28181 recorders and the "other" brand), here."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import AdminRecorderOut, RecorderConnection, RecorderCreateAdmin, RecorderCredentials
from ..services import recorders as svc

router = APIRouter(prefix="/admin/v1", tags=["admin: recorders"])
ROLES = ("owner",)


@router.post("/tenants/{tenant_id}/recorders", response_model=RecorderCredentials, status_code=201)
async def add_recorder(tenant_id: UUID, body: RecorderCreateAdmin, response: Response, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """Adds a recorder for a customer, including the types customers cannot add themselves (GB28181, the "other" brand).
    Hikvision, ISUP and cloud_api are refused. The answer carries the key, shown once and never stored in the audit log."""
    out = await svc.create_recorder(ctx.conn, tenant_id, body.brand, body.site_id, None, body.protocol, body.name, staff=True)
    await ctx.audit("recorder.admin_register", tenant_id=tenant_id, target_type="recorder", target_id=out["recorder_id"], reason=body.reason,
                    after={"device_id": out["device_id"], "brand": body.brand, "protocol": out["protocol"]})
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get("/recorders", response_model=list[AdminRecorderOut])
async def list_recorders(
    status: str | None = Query(None, pattern="^(never|online|offline)$"),
    tenant_id: UUID | None = None,
    q: str | None = Query(None, max_length=80),
    limit: int = Query(200, ge=1, le=500),
    ctx: AdminCtx = Depends(admin_ctx()),
):
    where, args = [], []
    if status:
        args.append(status); where.append(f"r.status = ${len(args)}::app.link_status")
    if tenant_id:
        args.append(tenant_id); where.append(f"r.tenant_id = ${len(args)}")
    if q:
        args.append(f"%{q}%"); where.append(f"(r.device_id ILIKE ${len(args)} OR t.name ILIKE ${len(args)})")
    args.append(limit)
    rows = await ctx.conn.fetch(
        f"SELECT {svc.RECORDER_COLS}, t.name AS tenant_name FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id"
        + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY r.created_at DESC LIMIT ${len(args)}", *args)
    return [dict(r) for r in rows]


@router.get("/recorders/{recorder_id}/connection", response_model=RecorderConnection)
async def recorder_connection(recorder_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    """The details to type into the recorder, without the password (which is shown only when it is made; rotate-key makes a new one)."""
    row = await ctx.conn.fetchrow("SELECT id, device_id, brand::text AS brand, protocol::text AS protocol FROM app.recorders WHERE id=$1", recorder_id)
    if not row:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    return svc.connection_details(row)
