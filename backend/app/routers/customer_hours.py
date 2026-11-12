"""The customer's opening hours. Two alerts need them: "Counting stopped" (while open) and "After-hours activity" (while closed)."""
from datetime import time

from fastapi import APIRouter, Depends
from pydantic import Field

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..schemas import Strict

router = APIRouter(prefix="/v1", tags=["customer: opening hours"])


class HoursIn(Strict):
    open: time | None = None          # both empty: no opening hours
    close: time | None = None
    closed_days: list[int] = Field(default_factory=list, max_length=6)      # 0 = Monday ... 6 = Sunday; at most six, a place closed every day has no hours


def _out(r) -> dict:
    return {"open": r["open_time"].strftime("%H:%M") if r["open_time"] else None, "close": r["close_time"].strftime("%H:%M") if r["close_time"] else None,
            "closed_days": sorted(r["closed_days"] or []), "set": bool(r["open_time"] and r["close_time"])}


@router.get("/opening-hours")
async def get_hours(ctx: CustomerCtx = Depends(customer_ctx())):
    return _out(await ctx.conn.fetchrow("SELECT open_time, close_time, closed_days FROM app.tenants WHERE id=$1", ctx.tenant_id))


@router.put("/opening-hours")
async def set_hours(body: HoursIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if (body.open is None) != (body.close is None):
        raise ApiError(422, "invalid_hours", "Set both the opening and the closing time, or neither.")
    if body.open is not None and body.open == body.close:
        raise ApiError(422, "invalid_hours", "The opening and closing times cannot be the same.")
    days = sorted(set(body.closed_days))
    if any(d < 0 or d > 6 for d in days):
        raise ApiError(422, "invalid_hours", "A day of the week is a number from 0 (Monday) to 6 (Sunday).")
    r = await ctx.conn.fetchrow("UPDATE app.tenants SET open_time=$2, close_time=$3, closed_days=$4 WHERE id=$1 RETURNING open_time, close_time, closed_days",
                                ctx.tenant_id, body.open, body.close, days if body.open is not None else [])
    await ctx.audit("opening_hours.update", "tenant", ctx.tenant_id, _out(r))
    return _out(r)
