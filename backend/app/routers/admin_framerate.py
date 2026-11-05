"""Super-admin control of how fast cameras are analysed, and the RTSP address the AI worker pulls from.

Frame rate costs real money (compute grows in direct proportion), so it is set here, not by customers.
Tenant-wide settings: owner and finance. Per-camera overrides and stream addresses: owner and technician."""
import re
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import FrameRatePatch, SourceIn
from ..services.framerate import PARTS, camera_override, resolve, tenant_policy

router = APIRouter(prefix="/admin/v1", tags=["admin: frame rate and sources"])

_T = ", ".join(f"t.fps_{p} AS t_{p}" for p in PARTS)
_C = ", ".join(f"c.fps_{p} AS c_{p}" for p in PARTS)
_FIELD = {"mode": "mode", "idle": "idle_fps", "active": "active_fps", "fixed": "fixed_fps"}


def _public_policy(p: dict) -> dict:
    return {_FIELD[k]: p[k] for k in PARTS}


async def _tenant(ctx: AdminCtx, tenant_id: UUID):
    row = await ctx.conn.fetchrow(f"SELECT t.id, t.name, {_T} FROM app.tenants t WHERE t.id=$1", tenant_id)
    if not row:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    return row


async def _camera(ctx: AdminCtx, camera_id: UUID):
    row = await ctx.conn.fetchrow(
        f"SELECT c.id, c.tenant_id, c.name, {_C}, {_T} FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id=$1", camera_id)
    if not row:
        raise ApiError(404, "camera_not_found", "Camera not found.")
    return row


def _merge(current: dict, body: FrameRatePatch) -> dict:
    new = dict(current)
    for part, field in _FIELD.items():
        v = getattr(body, field)
        if v is not None:
            new[part] = v
    return new


# ------------------------------------------------------------------------- tenant
@router.get("/tenants/{tenant_id}/frame-rate")
async def get_tenant_policy(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    return _public_policy(tenant_policy(await _tenant(ctx, tenant_id)))


@router.put("/tenants/{tenant_id}/frame-rate")
async def set_tenant_policy(tenant_id: UUID, body: FrameRatePatch, ctx: AdminCtx = Depends(admin_ctx("owner", "finance"))):
    row = await _tenant(ctx, tenant_id)
    cur = tenant_policy(row)
    new = _merge(cur, body)
    if new["idle"] > new["active"]:
        raise ApiError(422, "idle_above_active", "The idle rate cannot be faster than the active rate.")
    if new == cur:
        raise ApiError(409, "no_change", "These are already the tenant's settings.")
    await ctx.conn.execute("UPDATE app.tenants SET fps_mode=$2, fps_idle=$3, fps_active=$4, fps_fixed=$5 WHERE id=$1",
                           tenant_id, new["mode"], new["idle"], new["active"], new["fixed"])
    await ctx.audit("framerate.tenant.update", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason,
                    before=_public_policy(cur), after=_public_policy(new))
    return _public_policy(new)


@router.get("/tenants/{tenant_id}/cameras")
async def tenant_cameras(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    """Every camera of a tenant with its settings, the rate it really runs at, and its live health."""
    t = await _tenant(ctx, tenant_id)
    tp = tenant_policy(t)
    rows = await ctx.conn.fetch(
        f"""SELECT c.id, c.name, c.enabled, c.use_footfall, c.use_anpr, c.stream_state, c.stream_seen_at, c.analysis_fps, c.frames_dropped, c.last_frame_at,
                   c.stream_error, c.source_host, (c.source_ciphertext IS NOT NULL) AS has_source, {_C}
            FROM app.cameras c WHERE c.tenant_id=$1 ORDER BY c.created_at, c.name""", tenant_id)
    out = []
    for r in rows:
        ov = camera_override(r)
        out.append({
            "id": r["id"], "name": r["name"], "enabled": r["enabled"], "use_footfall": r["use_footfall"], "use_anpr": r["use_anpr"],
            "stream_state": r["stream_state"], "stream_seen_at": r["stream_seen_at"], "analysis_fps": r["analysis_fps"], "frames_dropped": r["frames_dropped"],
            "last_frame_at": r["last_frame_at"], "stream_error": r["stream_error"], "source_host": r["source_host"], "has_source": r["has_source"],
            "override": {_FIELD[p]: ov[p] for p in PARTS}, "effective": resolve(tp, ov)})
    return {"tenant_policy": _public_policy(tp), "cameras": out}


# ------------------------------------------------------------------------- camera
@router.put("/cameras/{camera_id}/frame-rate")
async def set_camera_override(camera_id: UUID, body: FrameRatePatch, ctx: AdminCtx = Depends(admin_ctx("owner", "technician"))):
    row = await _camera(ctx, camera_id)
    tp, cur = tenant_policy(row), camera_override(row)
    new = _merge(cur, body)
    eff = resolve(tp, new)
    if (new["idle"] if new["idle"] is not None else tp["idle"]) > (new["active"] if new["active"] is not None else tp["active"]):
        raise ApiError(422, "idle_above_active", "The idle rate cannot be faster than the active rate.")
    if new == cur:
        raise ApiError(409, "no_change", "These are already this camera's settings.")
    await ctx.conn.execute("UPDATE app.cameras SET fps_mode=$2, fps_idle=$3, fps_active=$4, fps_fixed=$5 WHERE id=$1",
                           camera_id, new["mode"], new["idle"], new["active"], new["fixed"])
    await ctx.audit("framerate.camera.update", tenant_id=row["tenant_id"], target_type="camera", target_id=camera_id, reason=body.reason,
                    before={_FIELD[p]: cur[p] for p in PARTS}, after={_FIELD[p]: new[p] for p in PARTS})
    return {"override": {_FIELD[p]: new[p] for p in PARTS}, "effective": eff}


@router.delete("/cameras/{camera_id}/frame-rate")
async def reset_camera_override(camera_id: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner", "technician"))):
    """Remove every override so the camera follows its tenant again."""
    row = await _camera(ctx, camera_id)
    cur = camera_override(row)
    if all(v is None for v in cur.values()):
        raise ApiError(409, "no_change", "This camera has no overrides.")
    await ctx.conn.execute("UPDATE app.cameras SET fps_mode=NULL, fps_idle=NULL, fps_active=NULL, fps_fixed=NULL WHERE id=$1", camera_id)
    await ctx.audit("framerate.camera.reset", tenant_id=row["tenant_id"], target_type="camera", target_id=camera_id, reason=reason,
                    before={_FIELD[p]: cur[p] for p in PARTS})
    return {"override": {_FIELD[p]: None for p in PARTS}, "effective": resolve(tenant_policy(row), {p: None for p in PARTS})}


# --------------------------------------------------------------------- stream source
def parse_source(url: str) -> str:
    """Checks an RTSP address and returns its host:port. Private and VPN addresses are allowed on purpose: customer sites are reached that way."""
    if len(url) > 500 or re.search(r"[\s\x00-\x1f]", url):
        raise ApiError(422, "invalid_source", "The address must be one line with no spaces.")
    try:
        u = urlparse(url)
        host, port = u.hostname, u.port
    except ValueError:
        raise ApiError(422, "invalid_source", "That is not a valid address.")
    if u.scheme not in ("rtsp", "rtsps") or not host:
        raise ApiError(422, "invalid_source", "Use an address like rtsp://user:password@host:554/path")
    return f"{host}:{port}" if port else host


@router.put("/cameras/{camera_id}/source")
async def set_source(camera_id: UUID, body: SourceIn, ctx: AdminCtx = Depends(admin_ctx("owner", "technician"))):
    row = await _camera(ctx, camera_id)
    host = parse_source(body.url)
    # Encrypted with the camera id bound in, like recorder keys. It is never returned, never logged, and never put in the audit log.
    await ctx.conn.execute("UPDATE app.cameras SET source_ciphertext=$2, source_host=$3, source_set_at=now() WHERE id=$1",
                           camera_id, vault().encrypt(body.url, str(camera_id)), host)
    await ctx.audit("camera.source.set", tenant_id=row["tenant_id"], target_type="camera", target_id=camera_id, reason=body.reason, after={"host": host})
    return {"source_host": host, "has_source": True}


@router.delete("/cameras/{camera_id}/source", status_code=204)
async def clear_source(camera_id: UUID, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner", "technician"))):
    row = await _camera(ctx, camera_id)
    await ctx.conn.execute("UPDATE app.cameras SET source_ciphertext=NULL, source_host=NULL, source_set_at=NULL WHERE id=$1", camera_id)
    await ctx.audit("camera.source.clear", tenant_id=row["tenant_id"], target_type="camera", target_id=camera_id, reason=reason)
