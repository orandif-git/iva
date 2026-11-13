from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import ChannelOut, RecorderConnection, RecorderCreateCustomer, RecorderCredentials, RecorderOut, RecorderPatch, RecorderStatus
from ..services import recorders as svc

router = APIRouter(prefix="/v1", tags=["customer: recorders"])


@router.get("/me")
async def me(ctx: CustomerCtx = Depends(customer_ctx())):
    t = await ctx.conn.fetchrow(
        """SELECT t.id, t.name, t.status::text AS status, t.plan_id, t.camera_limit, p.name AS plan_name, p.features,
                  (SELECT count(*) FROM app.cameras c WHERE c.enabled) AS cameras_in_use
           FROM app.tenants t JOIN platform.plans p ON p.id = t.plan_id WHERE t.id = $1""", ctx.tenant_id)
    return {"user": {"id": ctx.user_id, "name": ctx.name, "role": ctx.role}, "tenant": dict(t)}


@router.get("/recorders", response_model=list[RecorderOut])
async def list_recorders(ctx: CustomerCtx = Depends(customer_ctx())):
    rows = await ctx.conn.fetch(f"SELECT {svc.RECORDER_COLS} FROM app.recorders r ORDER BY r.created_at DESC")
    return [dict(r) for r in rows]


@router.post("/recorders", response_model=RecorderCredentials, status_code=201)
async def add_recorder(body: RecorderCreateCustomer, response: Response, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    out = await svc.create_recorder(ctx.conn, ctx.tenant_id, body.brand, body.site_id, ctx.user_id, body.protocol, body.name)
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


@router.patch("/recorders/{recorder_id}", response_model=RecorderOut)
async def rename_recorder(recorder_id: UUID, body: RecorderPatch, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    before = await _recorder(ctx, recorder_id)
    await ctx.conn.execute("UPDATE app.recorders SET name=$2 WHERE id=$1", recorder_id, body.name)
    await ctx.audit("recorder.rename", "recorder", recorder_id, {"device_id": before["device_id"], "name": body.name})
    return dict(await _recorder(ctx, recorder_id))


@router.delete("/recorders/{recorder_id}", status_code=204)
async def delete_recorder(recorder_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Removes the recorder, its channels and its cameras, with those cameras' counts and plate reads (alerts and message logs stay).
    The recorder is refused the next time it tries to register, and the gateway drops its connection at the next heartbeat."""
    row = await _recorder(ctx, recorder_id)
    cams = await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE recorder_id=$1", recorder_id)
    await ctx.conn.execute("DELETE FROM app.recorders WHERE id=$1", recorder_id)
    await ctx.audit("recorder.delete", "recorder", recorder_id, {"device_id": row["device_id"], "cameras_removed": cams})


@router.get("/recorders/{recorder_id}/connection", response_model=RecorderConnection)
async def recorder_connection(recorder_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """The details to type into the recorder (address, port, id, viewer user), without the password. The password is shown only when it is made;
    to get a new one use rotate-key."""
    return svc.connection_details(await _recorder(ctx, recorder_id))


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
