"""The alert catalogue for the platform owner: which alerts exist, an on/off switch for each, and the alerts of one customer.

A plan decides which alerts its customers have (see admin_plans.py). Here the owner can also add one alert to one customer, and take it away again.
A customer sees an alert only when it is built, switched on here, and part of their plan or added for them (see services/alert_catalog.py)."""
from uuid import UUID

from fastapi import APIRouter, Depends

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import alert_catalog as cat, maintenance, platform_fault
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
    every = {r["alert_type"]: r["every_seconds"] for r in await ctx.conn.fetch("SELECT alert_type, every_seconds FROM platform.alert_checks")}
    out = []
    for k in cat.LISTED:
        has = [{"id": p["id"], "name": p["name"]} for p in plans if k in (p["alert_types"] if p["alert_types"] is not None else cat.BUILT)]
        # `check_seconds`: how often a timed alert is looked for. None for an alert that is raised the moment its event arrives.
        out.append({**_entry(k), "enabled": cat.CATALOG[k]["built"] and k not in off, "customers_using": using.get(k, 0), "plans": has,
                    "check_seconds": every.get(k, maintenance.DEFAULT_CHECK_SECONDS) if k in maintenance.TIMED else None})
    return {"items": out}


class CheckEvery(Strict):
    every_seconds: int = Field(ge=30, le=3600)
    reason: str = Field(min_length=5, max_length=500)


@router.put("/alert-catalog/{alert_type}/check")
async def set_check(alert_type: str, body: CheckEvery, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """How often a timed alert is checked, for every customer. Takes effect within a few seconds."""
    if alert_type not in maintenance.TIMED:
        raise ApiError(409, "not_timed", "This alert is raised the moment it happens, so there is nothing to set.")
    before = await ctx.conn.fetchval("SELECT every_seconds FROM platform.alert_checks WHERE alert_type=$1", alert_type) or maintenance.DEFAULT_CHECK_SECONDS
    await ctx.conn.execute(
        """INSERT INTO platform.alert_checks (alert_type, every_seconds, changed_by) VALUES ($1,$2,$3)
           ON CONFLICT (alert_type) DO UPDATE SET every_seconds=$2, changed_by=$3, changed_at=now()""", alert_type, body.every_seconds, ctx.staff.id)
    await ctx.audit("alert.check_interval", target_type="alert", target_id=alert_type, reason=body.reason, before={"every_seconds": before}, after={"every_seconds": body.every_seconds})
    return {"type": alert_type, "check_seconds": body.every_seconds}


# ------------------------------------------------------------------------------------------------ faults in our own platform
class Person(Strict):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^\+?[0-9 ]{8,18}$")


class FaultNotify(Strict):
    enabled: bool
    people: list[Person] = Field(max_length=10)
    reason: str = Field(min_length=5, max_length=500)


@router.get("/platform-faults")
async def platform_faults(ctx: AdminCtx = Depends(admin_ctx())):
    """Who is told when our own video analysis stops, whether it is running now, and the last faults."""
    cfg = await platform_fault.settings(ctx.conn)
    rows = await ctx.conn.fetch("SELECT id, kind, detail, cameras, started_at, ended_at FROM platform.faults ORDER BY started_at DESC LIMIT 20")
    hook = await ctx.conn.fetchval("SELECT active FROM platform.webhook_endpoints WHERE alert_type='platform_fault'")
    return {"enabled": cfg["enabled"], "people": cfg["people"], "webhook_ready": bool(hook), "open": next((dict(r) for r in rows if r["ended_at"] is None), None), "recent": [dict(r) for r in rows]}


@router.put("/platform-faults")
async def set_platform_faults(body: FaultNotify, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    before = await platform_fault.settings(ctx.conn)
    people = [p.model_dump() for p in body.people]
    await ctx.conn.execute(
        """INSERT INTO platform.fault_notify (id, enabled, people, changed_by) VALUES (true, $1, $2, $3)
           ON CONFLICT (id) DO UPDATE SET enabled=$1, people=$2, changed_by=$3, changed_at=now()""", body.enabled, people, ctx.staff.id)
    await ctx.audit("platform_fault.notify", target_type="platform", target_id="fault_notify", reason=body.reason, before={"enabled": before["enabled"], "people": len(before["people"])},
                    after={"enabled": body.enabled, "people": len(people)})
    return {"enabled": body.enabled, "people": people}


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


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=500)


