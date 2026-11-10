"""Devices that get push notifications: the app registers its address after sign-in (and when it changes), and removes it on sign-out."""
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import Field

from ..db import db
from ..deps import CustomerCtx, customer_ctx
from ..schemas import Strict
from ..services import push

router = APIRouter(prefix="/v1", tags=["customer: push notifications"])


class DeviceIn(Strict):
    token: str = Field(min_length=20, max_length=4096)
    platform: Literal["web", "android", "ios"]


class TokenIn(Strict):
    token: str = Field(min_length=20, max_length=4096)


@router.get("/push")
async def status(ctx: CustomerCtx = Depends(customer_ctx())):
    """Whether this server can send push at all, so the app only asks for permission when it will be used."""
    return {"enabled": push.enabled(), "devices": await ctx.conn.fetchval("SELECT count(*) FROM app.push_devices WHERE user_id=$1", ctx.user_id)}


@router.post("/devices", status_code=204)
async def register(body: DeviceIn, ctx: CustomerCtx = Depends(customer_ctx())):
    # A device belongs to the account signed in on it now. If it was registered for someone else before (another account, or another person on a shared
    # computer), that registration goes first, so an alert for one account can never reach a screen that belongs to another.
    async with db.admin_tx() as c:
        await c.execute("DELETE FROM app.push_devices WHERE token=$1 AND (tenant_id<>$2 OR user_id<>$3)", body.token, ctx.tenant_id, ctx.user_id)
    await ctx.conn.execute(
        """INSERT INTO app.push_devices (tenant_id, user_id, token, platform) VALUES ($1,$2,$3,$4)
           ON CONFLICT (token) DO UPDATE SET platform=EXCLUDED.platform, last_seen_at=now()""", ctx.tenant_id, ctx.user_id, body.token, body.platform)
    return Response(status_code=204)


@router.post("/devices/remove", status_code=204)
async def remove(body: TokenIn, ctx: CustomerCtx = Depends(customer_ctx())):
    await ctx.conn.execute("DELETE FROM app.push_devices WHERE token=$1 AND user_id=$2", body.token, ctx.user_id)
    return Response(status_code=204)
