"""Notifications on a console user's phone: turn them on for this phone, choose what to hear about, send a test. Every console user manages their own."""
import json
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import StringConstraints

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import staff_push
from ..config import get_settings

router = APIRouter(prefix="/admin/v1/push", tags=["admin: notifications"])
Url = Annotated[str, StringConstraints(min_length=20, max_length=1000, pattern=r"^https://")]
Key = Annotated[str, StringConstraints(min_length=8, max_length=200)]


class Keys(Strict):
    p256dh: Key
    auth: Key


class SubscribeIn(Strict):
    endpoint: Url
    keys: Keys
    device: Annotated[str, StringConstraints(max_length=120)] = ""


class UnsubscribeIn(Strict):
    endpoint: Url


class PrefsIn(Strict):
    categories: dict[str, bool]


async def _mine(ctx: AdminCtx):
    row = await ctx.conn.fetchrow("SELECT push_prefs FROM platform.staff_users WHERE id=$1", ctx.staff.id)
    prefs = row["push_prefs"]
    return json.loads(prefs) if isinstance(prefs, str) else prefs


@router.get("")
async def settings(ctx: AdminCtx = Depends(admin_ctx())):
    on = staff_push.chosen(ctx.staff.role, await _mine(ctx))
    n = await ctx.conn.fetchval("SELECT count(*)::int FROM platform.push_subscriptions WHERE staff_id=$1", ctx.staff.id)
    return {"enabled": staff_push.enabled(), "public_key": get_settings().vapid_public_key if staff_push.enabled() else None, "devices": n,
            "categories": [{"key": k, "label": label, "help": hint, "on": k in on} for k, label, hint in staff_push.CATEGORIES]}


@router.put("/preferences")
async def set_preferences(body: PrefsIn, ctx: AdminCtx = Depends(admin_ctx())):
    unknown = set(body.categories) - set(staff_push.KEYS)
    if unknown:
        raise ApiError(422, "unknown_category", "That kind of notification does not exist.")
    mine = await _mine(ctx) or {}
    mine.update(body.categories)
    await ctx.conn.execute("UPDATE platform.staff_users SET push_prefs=$2::jsonb WHERE id=$1", ctx.staff.id, json.dumps(mine))
    return {"categories": sorted(staff_push.chosen(ctx.staff.role, mine))}


@router.post("/subscribe", status_code=204)
async def subscribe(body: SubscribeIn, ctx: AdminCtx = Depends(admin_ctx())):
    if not staff_push.enabled():
        raise ApiError(503, "push_off", "Notifications are not set up on the server yet.")
    await ctx.conn.execute(
        """INSERT INTO platform.push_subscriptions (staff_id, endpoint, p256dh, auth, device) VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (endpoint) DO UPDATE SET staff_id=EXCLUDED.staff_id, p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, device=EXCLUDED.device, failures=0""",
        ctx.staff.id, body.endpoint, body.keys.p256dh, body.keys.auth, body.device)
    await ctx.audit("push.subscribe", target_type="staff", target_id=ctx.staff.id)


@router.post("/unsubscribe", status_code=204)
async def unsubscribe(body: UnsubscribeIn, ctx: AdminCtx = Depends(admin_ctx())):
    await ctx.conn.execute("DELETE FROM platform.push_subscriptions WHERE endpoint=$1 AND staff_id=$2", body.endpoint, ctx.staff.id)


@router.post("/test")
async def test(ctx: AdminCtx = Depends(admin_ctx())):
    """Send a notification to this person's own phones now."""
    if not staff_push.enabled():
        raise ApiError(503, "push_off", "Notifications are not set up on the server yet.")
    r = await staff_push.notify(ctx.conn, "test", "Jas iVision", "This is a test. Notifications are working on this phone.", "/console/#more", tag="test", only_staff=ctx.staff.id)
    if not (r["ok"] or r["gone"] or r["error"]):
        raise ApiError(409, "no_device", "Turn notifications on for this phone first.")
    return r
