"""Staff can see every recorder and how to connect it. Recorders are connected, renamed and removed by the customer, not here."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import AdminRecorderOut, RecorderConnection
from ..services import recorders as svc

router = APIRouter(prefix="/admin/v1", tags=["admin: recorders"])


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
