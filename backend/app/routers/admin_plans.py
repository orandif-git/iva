"""Plans: what customers can choose, what it costs and what it includes. Anyone on staff can look; only the owner changes them.

When a price changes, each plan says what happens to customers already on it:
  next  they move to the new price from their next invoice
  keep  they keep the price they had (it is saved on the customer); only new customers pay the new price"""
import re
import secrets
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict

router = APIRouter(prefix="/admin/v1", tags=["admin: plans"])

SELECT = """SELECT p.id, p.name, p.price_per_camera_paise, p.stream_mode, p.features, p.active, p.price_change_policy,
                   (SELECT count(*) FROM app.tenants t WHERE t.plan_id = p.id) AS customers,
                   (SELECT count(*) FROM app.tenants t WHERE t.plan_id = p.id AND t.price_override_paise IS NOT NULL) AS customers_on_old_price
            FROM platform.plans p"""


class PlanIn(Strict):
    name: str = Field(min_length=2, max_length=60)
    price_per_camera_paise: int = Field(ge=0, le=10_000_000)
    stream_mode: Literal["snapshots", "realtime_5fps"] = "snapshots"
    anpr: bool = False
    review_queue: bool = False
    price_change_policy: Literal["next", "keep"] = "next"
    reason: str = Field(min_length=5, max_length=500)


class PlanPatch(Strict):
    name: str | None = Field(None, min_length=2, max_length=60)
    price_per_camera_paise: int | None = Field(None, ge=0, le=10_000_000)
    stream_mode: Literal["snapshots", "realtime_5fps"] | None = None
    anpr: bool | None = None
    review_queue: bool | None = None
    price_change_policy: Literal["next", "keep"] | None = None
    active: bool | None = None
    reason: str = Field(min_length=5, max_length=500)


def _out(r) -> dict:
    d = dict(r)
    f = d["features"] or {}
    d["anpr"], d["review_queue"] = bool(f.get("anpr")), bool(f.get("review_queue"))
    return d


async def _get(ctx: AdminCtx, pid: str):
    r = await ctx.conn.fetchrow(SELECT + " WHERE p.id=$1", pid)
    if not r:
        raise ApiError(404, "plan_not_found", "Plan not found.")
    return r


@router.get("/plans")
async def list_plans(include_retired: bool = False, ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch(SELECT + (" " if include_retired else " WHERE p.active ") + "ORDER BY p.price_per_camera_paise, p.name")
    return {"items": [_out(r) for r in rows]}


@router.post("/plans", status_code=201)
async def create_plan(body: PlanIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    base = re.sub(r"[^a-z0-9]+", "_", body.name.lower()).strip("_")[:30] or "plan"
    features = {"anpr": body.anpr, "review_queue": body.review_queue}
    for attempt in range(5):
        pid = base if attempt == 0 else f"{base}_{secrets.token_hex(2)}"
        try:
            await ctx.conn.execute(
                "INSERT INTO platform.plans (id, name, price_per_camera_paise, stream_mode, features, price_change_policy) VALUES ($1,$2,$3,$4,$5,$6)",
                pid, body.name, body.price_per_camera_paise, body.stream_mode, features, body.price_change_policy)
            break
        except asyncpg.UniqueViolationError:
            continue
    else:
        raise ApiError(409, "plan_exists", "A plan with a very similar name already exists.")
    await ctx.audit("plan.create", target_type="plan", reason=body.reason, after={"id": pid, "name": body.name, "price_paise": body.price_per_camera_paise})
    return _out(await _get(ctx, pid))


@router.put("/plans/{plan_id}")
async def update_plan(plan_id: str, body: PlanPatch, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, plan_id)
    f = dict(cur["features"] or {})
    if body.anpr is not None:
        if f.get("anpr") and not body.anpr and await ctx.conn.fetchval(
                "SELECT count(*) FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE t.plan_id=$1 AND c.use_anpr AND c.enabled", plan_id):
            raise ApiError(409, "plan_feature_in_use", "Customers on this plan have cameras reading number plates. They would lose it, so turn it off for them first.")
        f["anpr"] = body.anpr
    if body.review_queue is not None:
        f["review_queue"] = body.review_queue
    policy = body.price_change_policy or cur["price_change_policy"]
    price = cur["price_per_camera_paise"] if body.price_per_camera_paise is None else body.price_per_camera_paise
    active = cur["active"] if body.active is None else body.active
    async with ctx.conn.transaction():
        await ctx.conn.execute(
            "UPDATE platform.plans SET name=$2, price_per_camera_paise=$3, stream_mode=$4, features=$5, active=$6, price_change_policy=$7 WHERE id=$1",
            plan_id, body.name or cur["name"], price, body.stream_mode or cur["stream_mode"], f, active, policy)
        if price != cur["price_per_camera_paise"]:
            if policy == "keep":     # customers who follow the plan price today keep what they were paying
                await ctx.conn.execute("UPDATE app.tenants SET price_override_paise=$2 WHERE plan_id=$1 AND price_override_paise IS NULL", plan_id, cur["price_per_camera_paise"])
            else:                    # everyone moves to the new price, including customers who were still on an older one
                await ctx.conn.execute("UPDATE app.tenants SET price_override_paise=NULL WHERE plan_id=$1", plan_id)
    await ctx.audit("plan.update", target_type="plan", reason=body.reason,
                    before={"id": plan_id, "price_paise": cur["price_per_camera_paise"], "active": cur["active"]},
                    after={"id": plan_id, "price_paise": price, "policy": policy, "active": active})
    return _out(await _get(ctx, plan_id))


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(plan_id: str, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, plan_id)
    if cur["customers"]:
        raise ApiError(409, "plan_in_use", "Customers are on this plan. Retire it instead: new customers cannot choose it and current ones keep it.")
    try:
        await ctx.conn.execute("DELETE FROM platform.plans WHERE id=$1", plan_id)
    except asyncpg.ForeignKeyViolationError:
        raise ApiError(409, "plan_in_use", "This plan appears in usage or billing history. Retire it instead.")
    await ctx.audit("plan.delete", target_type="plan", reason=reason, before={"id": plan_id, "name": cur["name"]})
