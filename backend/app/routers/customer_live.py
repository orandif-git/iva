"""Live view and AI view for the customer's own cameras, from the Cameras menu. Owners and managers only; every view is recorded.

A view is a session (see services/live.py): it has a clock, can be extended up to a limit, and stops delivering pictures the moment it ends.
For now the pictures are refreshed stills (about one a second), which every recorder can give; smooth video can replace them without changing this API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from typing import Literal

from cryptography.exceptions import InvalidTag

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..crypto import vault
from ..services import go2rtc, live, live_stream, snapshot

router = APIRouter(prefix="/v1", tags=["customer: live view"])
LIVE_FRAME_MAX_AGE = 1.5          # seconds; a picture newer than this is reused, so a few viewers cost the recorder one picture


class StartIn(Strict):
    mode: Literal["live", "ai"] = "live"


@router.post("/cameras/{camera_id}/live", status_code=201)
async def start_view(camera_id: UUID, body: StartIn | None = None, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    mode = (body or StartIn()).mode
    s = await live.start(ctx.conn, ctx.tenant_id, ctx.user_id, camera_id, mode)
    await ctx.audit("camera.live_view", "camera", camera_id, {"mode": mode, "session": str(s["id"])})
    return s


@router.get("/live/{session_id}")
async def view_status(session_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    return await _mine(ctx, session_id)


@router.post("/live/{session_id}/extend")
async def keep_watching(session_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    await _mine(ctx, session_id)
    s = await live.extend(ctx.conn, session_id)
    return s


@router.delete("/live/{session_id}")
async def stop_view(session_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    await _mine(ctx, session_id)
    return await live.stop(ctx.conn, session_id)


@router.get("/live/{session_id}/frame")
async def frame(session_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """The current picture for an open view. In AI view it has the boxes and the marked areas drawn on it."""
    s = await live.open_session(ctx.conn, session_id)
    await _mine(ctx, session_id)
    cam_id = s["camera_id"]
    if s["mode"] == "ai":
        await ctx.conn.execute("UPDATE app.cameras SET ai_view_until = now() + make_interval(secs => $2) WHERE id=$1", cam_id, live.AI_KEEP_ALIVE)
        row = await ctx.conn.fetchrow("SELECT jpeg, extract(epoch FROM now() - taken_at) AS age FROM app.ai_view_frames WHERE camera_id=$1", cam_id)
        if not row or row["age"] > 60:
            raise ApiError(404, "no_picture_yet", "The picture is not ready yet. It appears a few seconds after you open the view.")
        return Response(bytes(row["jpeg"]), media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Age-Seconds": str(int(row["age"]))})
    row = await ctx.conn.fetchrow(
        """SELECT c.source_ciphertext, c.channel_no, r.device_id, r.protocol::text AS protocol
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id = c.recorder_id WHERE c.id=$1""", cam_id)

    def decrypt():
        try:
            return vault().decrypt(bytes(row["source_ciphertext"]), str(cam_id))
        except InvalidTag:
            raise ApiError(500, "source_unreadable", "The stored address failed its integrity check.")
    jpeg = await snapshot.get(cam_id, row, decrypt, max_age=LIVE_FRAME_MAX_AGE)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/live/{session_id}/whep")
async def whep(session_id: UUID, request: Request, ctx: CustomerCtx = Depends(customer_ctx("admin"), scope="function")):
    """Smooth video: the app posts an SDP offer and gets go2rtc's answer. An ordinary error means "use the picture stream instead",
    which is why this shares the same session and permission checks as /stream."""
    s = await live.open_session(ctx.conn, session_id)
    await _mine(ctx, session_id)
    if s["mode"] != "live":
        raise ApiError(409, "use_frames", "AI view is drawn picture by picture. Use the frame address.")
    cam_id = s["camera_id"]
    row = await ctx.conn.fetchrow(
        """SELECT c.source_ciphertext, c.channel_no, r.device_id, r.protocol::text AS protocol
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id = c.recorder_id WHERE c.id=$1""", cam_id)

    def decrypt():
        try:
            return vault().decrypt(bytes(row["source_ciphertext"]), str(cam_id))
        except InvalidTag:
            raise ApiError(500, "source_unreadable", "The stored address failed its integrity check.")
    offer = (await request.body()).decode("utf-8", "replace")
    if not offer.lstrip().startswith("v="):
        raise ApiError(400, "bad_offer", "The video player sent an empty offer. Try again.")
    answer = await go2rtc.whep(cam_id, row, decrypt, offer)
    return Response(answer, media_type="application/sdp", headers={"Cache-Control": "no-store"})


@router.get("/live/{session_id}/stream")
async def stream(session_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"), scope="function")):
    """Moving pictures for a live view (not AI view, which uses /frame): a multipart stream of JPEGs that ends when the view's time is up.
    If the video cannot be started the answer is an ordinary error, and the app falls back to refreshed pictures."""
    s = await live.open_session(ctx.conn, session_id)
    await _mine(ctx, session_id)
    if s["mode"] != "live":
        raise ApiError(409, "use_frames", "AI view is drawn picture by picture. Use the frame address.")
    cam_id = s["camera_id"]
    row = await ctx.conn.fetchrow(
        """SELECT c.source_ciphertext, c.channel_no, r.device_id, r.protocol::text AS protocol
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id = c.recorder_id WHERE c.id=$1""", cam_id)

    def decrypt():
        try:
            return vault().decrypt(bytes(row["source_ciphertext"]), str(cam_id))
        except InvalidTag:
            raise ApiError(500, "source_unreadable", "The stored address failed its integrity check.")
    place = live_stream.slot()
    if place is None:
        raise ApiError(429, "video_busy", "Too many cameras are being watched right now. Try again in a moment.")
    try:
        url, fmt = await snapshot.source_for(row, decrypt)
    except BaseException:
        place.release()
        raise
    return StreamingResponse(live_stream.stream(session_id, url, fmt, place), media_type=f"multipart/x-mixed-replace; boundary={live_stream.BOUNDARY}",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}, background=BackgroundTask(place.release))


@router.get("/live-history")
async def history(limit: int = Query(50, ge=1, le=200), ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Who watched which camera, when and for how long."""
    await live.close_expired(ctx.conn)
    rows = await ctx.conn.fetch(
        """SELECT s.id, s.mode, s.started_at, s.ended_at, s.ended_why, s.extensions, c.name AS camera_name, u.name AS user_name,
                  extract(epoch FROM coalesce(s.ended_at, now()) - s.started_at)::int AS seconds
           FROM app.live_sessions s JOIN app.cameras c ON c.id=s.camera_id LEFT JOIN app.tenant_users u ON u.id=s.user_id ORDER BY s.started_at DESC LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def _mine(ctx: CustomerCtx, session_id: UUID) -> dict:
    """Everyone with admin rights in the account can see a session; only the person who started it can drive it."""
    s = await live.get(ctx.conn, session_id)
    owner = await ctx.conn.fetchval("SELECT user_id FROM app.live_sessions WHERE id=$1", session_id)
    if owner != ctx.user_id:
        raise ApiError(403, "not_your_view", "Only the person who opened this view can control it.")
    return s
