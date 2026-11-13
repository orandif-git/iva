"""Marked areas on a camera picture: people in an area, a queue, and a staff area where phone use is watched.

Each area has a name and its own limits (see services/areas.py). A kind of area can be used only when the alert that goes with it is on for the customer
(it is built, the platform owner has it on, and the customer's plan or staff gave it to them); otherwise it is as if it did not exist."""
from uuid import UUID

from fastapi import APIRouter, Depends

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import AreasPut
from ..services import alert_catalog as cat
from ..services.areas import LABEL, SPEC, alert_for_kind, clean_params

router = APIRouter(prefix="/v1", tags=["customer: areas"])
CUSTOMER_KINDS = ["area_count", "queue_area", "staff_area"]        # what a customer's own app knows about
KINDS = list(cat.AREA_ALERTS)                                      # what the Control Center knows about


def _kinds(ctx) -> list[str]:
    return KINDS if getattr(ctx, "all_kinds", False) else CUSTOMER_KINDS


def _out(r) -> dict:
    p = dict(r["params"] or {})
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "points": r["points"], "params": p, "alert": alert_for_kind(r["kind"])}


async def _rows(ctx: CustomerCtx, camera_id: UUID):
    return await ctx.conn.fetch("SELECT id, kind::text AS kind, name, points, params FROM app.camera_zones WHERE camera_id=$1 AND kind::text = ANY($2::text[]) ORDER BY created_at, id", camera_id, _kinds(ctx))


async def _camera(ctx: CustomerCtx, camera_id: UUID):
    row = await ctx.conn.fetchrow("SELECT id, enabled, use_footfall, use_anpr FROM app.cameras WHERE id=$1", camera_id)
    if not row:
        raise ApiError(404, "camera_not_found", "Camera not found.")
    return row


@router.get("/area-kinds")
async def area_kinds(ctx: CustomerCtx = Depends(customer_ctx())):
    """The kinds of area this customer may draw, with the settings each one has. Kinds that are not theirs are not listed."""
    visible = set(await cat.visible(ctx.conn, ctx.tenant_id))
    return [{"kind": k, "label": LABEL[k], "alert": a, "title": cat.CATALOG[a]["title"], "settings": {n: {"default": d, "min": lo, "max": hi} for n, (d, lo, hi) in SPEC[k].items()}}
            for k, a in cat.AREA_ALERTS.items() if a in visible and k in _kinds(ctx)]


@router.get("/cameras/{camera_id}/areas")
async def get_areas(camera_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    await _camera(ctx, camera_id)
    return [_out(r) for r in await _rows(ctx, camera_id)]


@router.put("/cameras/{camera_id}/areas")
async def put_areas(camera_id: UUID, body: AreasPut, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Replaces the camera's areas with this list. An area sent with its id is changed and keeps its history; one without is new; one left out is removed."""
    cam = await _camera(ctx, camera_id)
    existing = {r["id"]: r for r in await _rows(ctx, camera_id)}
    visible = set(await cat.visible(ctx.conn, ctx.tenant_id))
    for a in body.areas:
        if a.kind not in _kinds(ctx):
            raise ApiError(403, "not_available", "That kind of area is not available here.")
        if cat.AREA_ALERTS[a.kind] not in visible:
            raise ApiError(403, "not_available", f"{LABEL[a.kind].capitalize()} is not part of your plan.")
        if a.id is not None and a.id not in existing:
            raise ApiError(404, "area_not_found", "One of the areas was not found on this camera.")
    keep = {a.id for a in body.areas if a.id}
    if not body.areas and cam["enabled"] and not (cam["use_footfall"] or cam["use_anpr"]):
        raise ApiError(409, "camera_needs_a_use", "This camera is only used for areas. Switch on people counting or plate reading first, or turn the camera off.")
    cleaned = []
    for a in body.areas:
        prev = existing[a.id]["params"] if a.id else None
        cleaned.append((a, clean_params(a.kind, a.params, previous=prev, user_id=ctx.user_id)))
    async with ctx.conn.transaction():
        for zid in set(existing) - keep:
            await ctx.conn.execute("DELETE FROM app.camera_zones WHERE id=$1", zid)
        for a, params in cleaned:
            if a.id:
                await ctx.conn.execute("UPDATE app.camera_zones SET name=$2, points=$3, params=$4 WHERE id=$1 AND kind::text=$5", a.id, a.name, a.points, params, a.kind)
            else:
                await ctx.conn.execute("INSERT INTO app.camera_zones (tenant_id, camera_id, kind, name, points, params, created_at) VALUES ($1,$2,$3::app.zone_kind,$4,$5,$6, clock_timestamp())",
                                       ctx.tenant_id, camera_id, a.kind, a.name, a.points, params)
        await ctx.conn.execute("UPDATE app.cameras SET use_areas=EXISTS (SELECT 1 FROM app.camera_zones WHERE camera_id=$1 AND kind::text = ANY($2::text[])) WHERE id=$1", camera_id, KINDS)
    await ctx.audit("areas.update", "camera", camera_id, {"areas": [{"kind": a.kind, "name": a.name} for a in body.areas]})
    return [_out(r) for r in await _rows(ctx, camera_id)]
