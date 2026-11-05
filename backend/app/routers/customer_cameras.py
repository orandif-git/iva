from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import CameraCreate, CameraOut, CameraPatch, ZoneOut, ZonesPut

router = APIRouter(prefix="/v1", tags=["customer: cameras"])

CAM_COLS = "c.id, c.recorder_id, c.channel_no, c.name, c.enabled, c.use_footfall, c.use_anpr, c.stream, c.last_frame_at, c.stream_state"


async def _plan_features(conn, tenant_id) -> dict:
    return await conn.fetchval("SELECT p.features FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id WHERE t.id=$1", tenant_id)


@router.get("/cameras", response_model=list[CameraOut])
async def list_cameras(ctx: CustomerCtx = Depends(customer_ctx())):
    return [dict(r) for r in await ctx.conn.fetch(f"SELECT {CAM_COLS} FROM app.cameras c ORDER BY c.created_at")]


@router.post("/cameras", response_model=CameraOut, status_code=201)
async def add_camera(body: CameraCreate, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    # Lock the tenant row so two simultaneous requests cannot both take the last licence.
    tenant = await ctx.conn.fetchrow("SELECT camera_limit FROM app.tenants WHERE id=$1 FOR UPDATE", ctx.tenant_id)
    rec = await ctx.conn.fetchval("SELECT 1 FROM app.recorders WHERE id=$1", body.recorder_id)
    if not rec:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    ch = await ctx.conn.fetchrow("SELECT active FROM app.recorder_channels WHERE recorder_id=$1 AND channel_no=$2", body.recorder_id, body.channel_no)
    if not ch or not ch["active"]:
        raise ApiError(422, "channel_not_found", "That channel was not reported by the recorder.")
    if body.use_anpr and not (await _plan_features(ctx.conn, ctx.tenant_id)).get("anpr"):
        raise ApiError(403, "plan_feature_missing", "Number plate reading is not included in your plan.")
    in_use = await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE enabled")
    if in_use >= tenant["camera_limit"]:
        raise ApiError(409, "camera_limit_reached", f"Your plan allows {tenant['camera_limit']} cameras.")
    try:
        async with ctx.conn.transaction():
            row = await ctx.conn.fetchrow(
                f"""INSERT INTO app.cameras AS c (tenant_id, recorder_id, channel_no, name, use_footfall, use_anpr)
                    VALUES ($1,$2,$3,$4,$5,$6) RETURNING {CAM_COLS}""",
                ctx.tenant_id, body.recorder_id, body.channel_no, body.name, body.use_footfall, body.use_anpr)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "camera_exists", "This channel is already set up as a camera.")
    await ctx.audit("camera.create", "camera", row["id"], {"name": body.name, "footfall": body.use_footfall, "anpr": body.use_anpr})
    return dict(row)


async def _camera(ctx: CustomerCtx, camera_id: UUID):
    row = await ctx.conn.fetchrow(f"SELECT {CAM_COLS} FROM app.cameras c WHERE c.id=$1", camera_id)
    if not row:
        raise ApiError(404, "camera_not_found", "Camera not found.")
    return row


@router.patch("/cameras/{camera_id}", response_model=CameraOut)
async def patch_camera(camera_id: UUID, body: CameraPatch, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    cur = await _camera(ctx, camera_id)
    ch = body.model_dump(exclude_unset=True)
    if not ch:
        raise ApiError(422, "nothing_to_change", "Send at least one field to change.")
    merged = {**dict(cur), **ch}
    if merged["enabled"] and not (merged["use_footfall"] or merged["use_anpr"]):
        raise ApiError(422, "use_required", "Choose at least one use: footfall or number plates.")
    if ch.get("use_anpr") and not (await _plan_features(ctx.conn, ctx.tenant_id)).get("anpr"):
        raise ApiError(403, "plan_feature_missing", "Number plate reading is not included in your plan.")
    if ch.get("enabled") and not cur["enabled"]:
        tenant = await ctx.conn.fetchrow("SELECT camera_limit FROM app.tenants WHERE id=$1 FOR UPDATE", ctx.tenant_id)
        if await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE enabled") >= tenant["camera_limit"]:
            raise ApiError(409, "camera_limit_reached", f"Your plan allows {tenant['camera_limit']} cameras.")
    cols = list(ch)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    await ctx.conn.execute(f"UPDATE app.cameras SET {sets} WHERE id=$1", camera_id, *[ch[c] for c in cols])
    await ctx.audit("camera.update", "camera", camera_id, ch)
    return dict(await _camera(ctx, camera_id))


@router.delete("/cameras/{camera_id}", status_code=204)
async def delete_camera(camera_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    await _camera(ctx, camera_id)
    await ctx.conn.execute("DELETE FROM app.cameras WHERE id=$1", camera_id)
    await ctx.audit("camera.delete", "camera", camera_id)


@router.get("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
async def get_zones(camera_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    await _camera(ctx, camera_id)
    return [dict(r) for r in await ctx.conn.fetch("SELECT id, kind::text AS kind, points, in_direction FROM app.camera_zones WHERE camera_id=$1 ORDER BY kind", camera_id)]


@router.put("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
async def put_zones(camera_id: UUID, body: ZonesPut, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    await _camera(ctx, camera_id)
    kinds = [z.kind for z in body.zones]
    if len(set(kinds)) != len(kinds):
        raise ApiError(422, "duplicate_zone", "Send at most one counting line and one plate zone.")
    for z in body.zones:
        if z.kind == "count_line" and len(z.points) != 2:
            raise ApiError(422, "bad_line", "A counting line has exactly two points.")
        if z.kind == "anpr_zone" and len(z.points) < 3:
            raise ApiError(422, "bad_zone", "A plate zone needs at least three points.")
    await ctx.conn.execute("DELETE FROM app.camera_zones WHERE camera_id=$1", camera_id)
    for z in body.zones:
        await ctx.conn.execute(
            "INSERT INTO app.camera_zones (tenant_id, camera_id, kind, points, in_direction) VALUES ($1,$2,$3::app.zone_kind,$4,$5)",
            ctx.tenant_id, camera_id, z.kind, z.points, z.in_direction)
    await ctx.audit("zones.update", "camera", camera_id, {"kinds": kinds})
    return [dict(r) for r in await ctx.conn.fetch("SELECT id, kind::text AS kind, points, in_direction FROM app.camera_zones WHERE camera_id=$1 ORDER BY kind", camera_id)]
