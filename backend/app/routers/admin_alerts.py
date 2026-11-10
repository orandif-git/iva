"""The alert catalogue for the platform owner: which alerts exist, an on/off switch for each, and the alerts of one customer.

A plan decides which alerts its customers have (see admin_plans.py). Here the owner can also add one alert to one customer, and take it away again.
A customer sees an alert only when it is built, switched on here, and part of their plan or added for them (see services/alert_catalog.py)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import alert_catalog as cat
from pydantic import Field

router = APIRouter(prefix="/admin/v1", tags=["admin: alerts"])


def _entry(k: str) -> dict:
    m = cat.CATALOG[k]
    return {"type": k, "event": cat.event_name(k), "family": m["family"], "group": m["group"], "ai": m["family"] == cat.SMART, "title": m["title"], "description": m["description"],
            "severity": m["severity"], "built": m["built"], "needs": m["needs"], "media": m["media"], "shortcodes": cat.shortcodes_for(k)}


@router.get("/alert-catalog")
async def catalog(ctx: AdminCtx = Depends(admin_ctx())):
    off = await cat.platform_off(ctx.conn)
    using = {r["rule_type"]: r["n"] for r in await ctx.conn.fetch("SELECT rule_type, count(*)::int AS n FROM app.alert_rules WHERE enabled GROUP BY rule_type")}
    plans = await ctx.conn.fetch("SELECT id, name, alert_types FROM platform.plans WHERE active ORDER BY price_per_camera_paise, name")
    out = []
    for k in cat.LISTED:
        has = [{"id": p["id"], "name": p["name"]} for p in plans if k in (p["alert_types"] if p["alert_types"] is not None else cat.BUILT)]
        out.append({**_entry(k), "enabled": cat.CATALOG[k]["built"] and k not in off, "customers_using": using.get(k, 0), "plans": has})
    return {"items": out}


class Switch(Strict):
    enabled: bool
    reason: str = Field(min_length=5, max_length=500)


@router.put("/alert-catalog/{alert_type}")
async def switch(alert_type: str, body: Switch, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    if alert_type not in cat.CATALOG:
        raise ApiError(404, "alert_not_found", "There is no alert with that name.")
    if not cat.CATALOG[alert_type]["built"]:
        raise ApiError(409, "alert_not_built", "This alert is not built yet, so it cannot be switched on.")
    before = alert_type not in await cat.platform_off(ctx.conn)
    await ctx.conn.execute(
        """INSERT INTO platform.alert_switches (alert_type, enabled, changed_by) VALUES ($1,$2,$3)
           ON CONFLICT (alert_type) DO UPDATE SET enabled=$2, changed_by=$3, changed_at=now()""", alert_type, body.enabled, ctx.staff.id)
    await ctx.audit("alert.switch", target_type="alert", target_id=alert_type, reason=body.reason, before={"enabled": before}, after={"enabled": body.enabled})
    return {**_entry(alert_type), "enabled": body.enabled}


# ------------------------------------------------------------------------------------------------ one customer
async def _tenant(ctx: AdminCtx, tenant_id: UUID):
    t = await ctx.conn.fetchrow("SELECT id, name FROM app.tenants WHERE id=$1", tenant_id)
    if not t:
        raise ApiError(404, "tenant_not_found", "Customer not found.")
    return t


@router.get("/tenants/{tenant_id}/alerts")
async def tenant_alerts(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    await _tenant(ctx, tenant_id)
    plan, plan_name = await cat.plan_types(ctx.conn, tenant_id)
    added = await cat.added_types(ctx.conn, tenant_id)
    off = await cat.platform_off(ctx.conn)
    items = []
    for k in cat.LISTED:
        if k in plan or k in added:
            items.append({**_entry(k), "source": "plan" if k in plan else "added", "also_added": k in plan and k in added, "visible": cat.CATALOG[k]["built"] and k not in off})
    return {"plan": plan_name, "items": items, "available": [_entry(k) for k in cat.LISTED if k not in plan and k not in added and cat.CATALOG[k]["built"]]}


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=500)


@router.put("/tenants/{tenant_id}/alerts/{alert_type}", status_code=201)
async def add_alert(tenant_id: UUID, alert_type: str, body: Reason, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    t = await _tenant(ctx, tenant_id)
    if alert_type not in cat.CATALOG:
        raise ApiError(404, "alert_not_found", "There is no alert with that name.")
    if not cat.CATALOG[alert_type]["built"]:
        raise ApiError(409, "alert_not_built", "This alert is not built yet, so it cannot be added.")
    plan, _ = await cat.plan_types(ctx.conn, tenant_id)
    if alert_type in plan:
        raise ApiError(409, "already_in_plan", "This customer already has this alert through their plan.")
    if not await ctx.conn.fetchval("INSERT INTO app.tenant_alert_grants (tenant_id, alert_type, granted_by) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING RETURNING 1", tenant_id, alert_type, ctx.staff.id):
        raise ApiError(409, "already_added", "This alert was already added for this customer.")
    await ctx.audit("tenant.alert_add", tenant_id=tenant_id, target_type="alert", target_id=alert_type, reason=body.reason, after={"tenant": t["name"], "alert": alert_type})
    return {"type": alert_type, "source": "added"}


@router.delete("/tenants/{tenant_id}/alerts/{alert_type}", status_code=204)
async def remove_alert(tenant_id: UUID, alert_type: str, reason: str = Query(..., min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    t = await _tenant(ctx, tenant_id)
    plan, _ = await cat.plan_types(ctx.conn, tenant_id)
    if alert_type in plan:
        raise ApiError(409, "from_plan", "This alert comes from the customer's plan. Change the plan to remove it.")
    if not await ctx.conn.fetchval("DELETE FROM app.tenant_alert_grants WHERE tenant_id=$1 AND alert_type=$2 RETURNING 1", tenant_id, alert_type):
        raise ApiError(404, "alert_not_added", "This alert was not added for this customer.")
    await ctx.audit("tenant.alert_remove", tenant_id=tenant_id, target_type="alert", target_id=alert_type, reason=reason, before={"tenant": t["name"], "alert": alert_type})
