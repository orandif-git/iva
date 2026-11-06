"""Staff can look at what the detector sees on a camera: the picture with boxes around the people and vehicles it found, the counting line
and the running count. It is produced only while someone is looking: opening the view asks the worker to start, and the worker stops by itself
about 45 seconds after the last request. Nothing is kept."""
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError

router = APIRouter(prefix="/admin/v1", tags=["admin: what the AI sees"])
KEEP_ON_SECONDS = 45


@router.post("/cameras/{camera_id}/ai-view")
async def ask(camera_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    """Opens the view or keeps it open. Call it again every 10 seconds or so while the picture is on screen."""
    row = await ctx.conn.fetchrow("SELECT tenant_id, enabled, coalesce(ai_view_until > now(), false) AS on FROM app.cameras WHERE id=$1", camera_id)
    if not row:
        raise ApiError(404, "camera_not_found", "Camera not found.")
    if not row["enabled"]:
        raise ApiError(409, "camera_paused", "This camera is paused, so there is nothing to show.")
    await ctx.conn.execute("UPDATE app.cameras SET ai_view_until = now() + make_interval(secs => $2) WHERE id=$1", camera_id, KEEP_ON_SECONDS)
    if not row["on"]:                                                           # a new look, not a renewal: log it once
        await ctx.audit("camera.ai_view", tenant_id=row["tenant_id"], target_type="camera", target_id=camera_id)
    return {"on": True, "keep_open_seconds": KEEP_ON_SECONDS}


@router.get("/cameras/{camera_id}/ai-view/image")
async def image(camera_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner", "support"))):
    row = await ctx.conn.fetchrow("SELECT jpeg, taken_at, extract(epoch FROM now() - taken_at) AS age FROM app.ai_view_frames WHERE camera_id=$1", camera_id)
    if not row or row["age"] > 60:
        raise ApiError(404, "no_picture_yet", "The picture is not ready yet. It appears a few seconds after you open the view.")
    return Response(bytes(row["jpeg"]), media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Taken-At": row["taken_at"].isoformat(), "X-Age-Seconds": str(int(row["age"]))})
