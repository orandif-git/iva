from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import ChannelOut, RecorderCreateCustomer, RecorderCredentials, RecorderOut, RecorderStatus
from ..services import recorders as svc

router = APIRouter(prefix="/v1", tags=["customer: recorders"])


@router.get("/me")
async def me(ctx: CustomerCtx = Depends(customer_ctx())):
    t = await ctx.conn.fetchrow(
        """SELECT t.id, t.name, t.status::text AS status, t.plan_id, t.camera_limit, p.name AS plan_name, p.features,
                  (SELECT count(*) FROM app.cameras c WHERE c.enabled) AS cameras_in_use
           FROM app.tenants t JOIN platform.plans p ON p.id = t.plan_id WHERE t.id = $1""", ctx.tenant_id)
    return {"user": {"id": ctx.user_id, "name": ctx.name, "role": ctx.role}, "viewing_as_staff": ctx.viewing_as_staff, "tenant": dict(t)}


@router.get("/recorders", response_model=list[RecorderOut])
async def list_recorders(ctx: CustomerCtx = Depends(customer_ctx())):
    rows = await ctx.conn.fetch(f"SELECT {svc.RECORDER_COLS} FROM app.recorders r ORDER BY r.created_at DESC")
    return [dict(r) for r in rows]


@router.post("/recorders", response_model=RecorderCredentials, status_code=201)
async def add_recorder(body: RecorderCreateCustomer, response: Response, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    out = await svc.create_recorder(ctx.conn, ctx.tenant_id, body.brand, body.site_id, ctx.user_id, body.protocol)
    await ctx.audit("recorder.register", "recorder", out["recorder_id"], {"device_id": out["device_id"], "brand": body.brand, "protocol": out["protocol"]})
    response.headers["Cache-Control"] = "no-store"          # the response carries the key; nothing may cache it
    return out


async def _recorder(ctx: CustomerCtx, recorder_id: UUID):
    row = await ctx.conn.fetchrow(f"SELECT {svc.RECORDER_COLS} FROM app.recorders r WHERE r.id=$1", recorder_id)
    if not row:                                           # also what another tenant's recorder looks like: row-level security hides it
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    return row


@router.get("/recorders/{recorder_id}", response_model=RecorderOut)
async def get_recorder(recorder_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    return dict(await _recorder(ctx, recorder_id))


@router.get("/recorders/{recorder_id}/status", response_model=RecorderStatus)
async def recorder_status(recorder_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Polled by the connect screen every couple of seconds until all four checks are true."""
    r = await _recorder(ctx, recorder_id)
    return {"status": r["status"], "checks": svc.link_checks(r), "channel_count": r["channel_count"], "last_heartbeat_at": r["last_heartbeat_at"]}


@router.get("/recorders/{recorder_id}/channels", response_model=list[ChannelOut])
async def channels(recorder_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    await _recorder(ctx, recorder_id)
    rows = await ctx.conn.fetch(
        """SELECT ch.channel_no, ch.name, ch.active,
                  EXISTS (SELECT 1 FROM app.cameras c WHERE c.recorder_id = ch.recorder_id AND c.channel_no = ch.channel_no) AS enabled_as_camera
           FROM app.recorder_channels ch WHERE ch.recorder_id = $1 ORDER BY ch.channel_no""", recorder_id)
    return [dict(r) for r in rows]


@router.post("/recorders/{recorder_id}/rotate-key", response_model=RecorderCredentials)
async def rotate(recorder_id: UUID, response: Response, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    await _recorder(ctx, recorder_id)
    out = await svc.rotate_key(ctx.conn, recorder_id)
    await ctx.audit("key.rotate", "recorder", recorder_id, {"device_id": out["device_id"]})
    response.headers["Cache-Control"] = "no-store"
    return out
