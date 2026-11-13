"""Two things the console needs that are not a screen: one fast search across customers and recorders, and a live count feed.

Search is asked as the person types, so it never downloads every customer to find one name. The live feed is a server-sent event
stream the console opens with the same sign-in it already has; it is how the Today page notices a recorder going offline without
the person pressing refresh. Neither can see anything the role could not see on its own page.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..db import db
from ..config import get_settings
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import go2rtc
from pydantic import Field
from uuid import UUID

router = APIRouter(prefix="/admin/v1", tags=["admin: console"])
EVERY_SECONDS = 5.0


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(6, ge=1, le=20),
    ctx: AdminCtx = Depends(admin_ctx()),
):
    """Customers and recorders whose name, city, device id or customer contains what was typed. Pages are matched in the console
    itself, from the one route list, so this only looks at the data."""
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"          # what was typed is text, never a wildcard
    customers = await ctx.conn.fetch(
        "SELECT id, name, city FROM app.tenants WHERE name ILIKE $1 ESCAPE '\\' OR city ILIKE $1 ESCAPE '\\' ORDER BY name LIMIT $2", like, limit)
    recorders = await ctx.conn.fetch(
        """SELECT r.id, coalesce(r.name, r.device_id) AS name, r.device_id, t.name AS tenant_name
             FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id
            WHERE coalesce(r.name, '') ILIKE $1 ESCAPE '\\' OR r.device_id ILIKE $1 ESCAPE '\\' OR t.name ILIKE $1 ESCAPE '\\'
            ORDER BY coalesce(r.name, r.device_id) LIMIT $2""", like, limit)
    return {
        "customers": [{"id": str(r["id"]), "name": r["name"], "city": r["city"]} for r in customers],
        "recorders": [{"id": str(r["id"]), "name": r["name"], "device_id": r["device_id"], "tenant_name": r["tenant_name"]} for r in recorders],
        "total": len(customers) + len(recorders),
    }


async def _counts() -> dict:
    """The few numbers the Today page shows, read on their own short connection so a long stream never holds one."""
    async with db.admin_tx() as c:
        row = await c.fetchrow(
            """SELECT
                 (SELECT count(*) FROM app.alerts a JOIN app.tenants t ON t.id = a.tenant_id
                   WHERE a.acknowledged_at IS NULL AND (a.snoozed_until IS NULL OR a.snoozed_until < now())
                     AND t.status IN ('active', 'past_due')) AS alerts_open,
                 (SELECT count(*) FROM app.recorders WHERE status = 'offline') AS recorders_offline,
                 (SELECT count(*) FROM app.recorders WHERE status = 'never') AS recorders_never,
                 (SELECT count(*) FROM app.tenants WHERE status = 'trial') AS trials,
                 (SELECT count(*) FROM app.tenants WHERE status IN ('trial', 'active', 'past_due')) AS customers""")
    return {k: int(row[k] or 0) for k in row.keys()}


async def _feed(request: Request):
    # A proxy must not sit on the first bytes; the browser reconnects in 5 s by itself if the link drops.
    yield "retry: 5000\n\n"
    previous = None
    while True:
        if await request.is_disconnected():
            return
        try:
            now = await _counts()
        except Exception:
            now = None
        if now is not None and now != previous:
            payload = {"kind": "counts", **now}
            yield "event: counts\ndata: " + json.dumps(payload) + "\n\n"
            previous = now
        else:
            yield ": keep-alive\n\n"                       # a comment line, so the connection shows signs of life
        await asyncio.sleep(EVERY_SECONDS)


@router.get("/events")
async def events(request: Request, ctx: AdminCtx = Depends(admin_ctx(), scope="function")):
    """A stream of small count events: open platform alerts and recorders that stopped reporting. The browser reads it with
    fetch (the sign-in travels in a header), so no token ever appears in a URL."""
    return StreamingResponse(_feed(request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------------------------------------ smooth live video, for one customer
class SmoothIn(Strict):
    enabled: bool
    reason: str = Field(min_length=5, max_length=500)


@router.get("/tenants/{tenant_id}/smooth-video")
async def get_smooth(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    """Whether this customer has smooth live video, whether the server can give it (go2rtc is set up), and how many smooth views are open on the server now."""
    on = await ctx.conn.fetchval("SELECT smooth_video FROM app.tenants WHERE id=$1", tenant_id)
    if on is None:
        raise ApiError(404, "tenant_not_found", "Customer not found.")
    viewers = await ctx.conn.fetchval("SELECT count(*) FROM app.live_sessions WHERE smooth AND ended_at IS NULL AND expires_at > now()")
    return {"enabled": on, "available": go2rtc.configured(), "viewers_now": int(viewers or 0), "max": get_settings().smooth_video_max}


@router.put("/tenants/{tenant_id}/smooth-video")
async def set_smooth(tenant_id: UUID, body: SmoothIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await ctx.conn.fetchval("SELECT smooth_video FROM app.tenants WHERE id=$1", tenant_id)
    if cur is None:
        raise ApiError(404, "tenant_not_found", "Customer not found.")
    await ctx.conn.execute("UPDATE app.tenants SET smooth_video=$2 WHERE id=$1", tenant_id, body.enabled)
    await ctx.audit("tenant.smooth_video", tenant_id=tenant_id, target_type="tenant", target_id=tenant_id, reason=body.reason, before={"enabled": cur}, after={"enabled": body.enabled})
    return {"enabled": body.enabled, "available": go2rtc.configured()}
