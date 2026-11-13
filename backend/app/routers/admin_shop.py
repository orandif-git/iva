"""What the Control Center sets up for one customer: opening hours, the people who get messages, the events (alerts) and the marked areas.

Each of these already exists for the customer's own app. These endpoints let Jas iVision staff do the same for any customer, by running the very same code
with the customer's data in view and nothing else (row-level security is set to that customer, just as it is for the customer's own sign-in).
Every change is recorded in the audit log with the staff member's name. Roles: Owner, Admin and Onboarding may change things; Support may change
people and hours (to help a shop) but not events or areas."""
from datetime import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field

from ..db import db
from ..deps import CustomerCtx, _bearer, _uuid, client_ip
from ..errors import ApiError
from ..schemas import AreasPut, CameraCreate, CameraPatch, ChannelOut, Strict, ZonesPut
from ..security import verify_token
from . import customer_alerts, customer_areas, customer_cameras, customer_recorders
from ..services import alert_catalog
from ..services.alerts import DEFAULT_ENABLED
from ..config import get_settings

router = APIRouter(prefix="/admin/v1/tenants/{tenant_id}/shop", tags=["admin: shop setup"])
EDITORS = ("owner", "admin", "onboarding")
HELPERS = ("owner", "admin", "onboarding", "support")


def shop_ctx(*roles: str):
    allowed = set(roles)

    async def dep(request: Request, tenant_id: UUID):
        claims = verify_token("platform", _bearer(request))
        if get_settings().staff_require_mfa and claims.get("mfa") is not True:
            raise ApiError(401, "mfa_required", "Complete multi-factor sign-in.")
        sid = _uuid(claims["sub"])
        async with db.admin_tx() as c:
            st = await c.fetchrow("SELECT id, name, role::text AS role, status FROM platform.staff_users WHERE id=$1", sid)
            if not st or st["status"] != "active":
                raise ApiError(401, "invalid_token", "Sign in again.")
            if st["role"] not in allowed:
                raise ApiError(403, "forbidden", "Your role cannot do this.")
            if not await c.fetchval("SELECT 1 FROM app.tenants WHERE id=$1", tenant_id):
                raise ApiError(404, "tenant_not_found", "Customer not found.")
        settings = {"app.actor_id": str(sid), "app.actor_name": st["name"], "app.actor_role": f"staff:{st['role']}", "app.request_id": request.state.request_id}
        async with db.tenant_tx(tenant_id, settings=settings) as conn:
            yield CustomerCtx(conn, tenant_id, sid, st["name"], "admin", request.state.request_id, all_kinds=True)

    return dep


# ---------------------------------------------------------------------------------------------------------- opening hours, a different time on each day
class DayIn(Strict):
    open: bool
    from_: time | None = Field(None, alias="from")
    to: time | None = None


class HoursPut(Strict):
    always_open: bool = False
    days: list[DayIn] = Field(default_factory=list, max_length=7)        # Monday first


def _hours_out(r) -> dict:
    import json
    raw = r["day_hours"]
    raw = json.loads(raw) if isinstance(raw, str) else raw
    if raw:
        days = [{"open": str(i) in raw, "from": raw[str(i)][0] if str(i) in raw else "09:00", "to": raw[str(i)][1] if str(i) in raw else "21:00"} for i in range(7)]
    elif r["open_time"] and r["close_time"]:
        o, c = r["open_time"].strftime("%H:%M"), r["close_time"].strftime("%H:%M")
        days = [{"open": i not in (r["closed_days"] or []), "from": o, "to": c} for i in range(7)]
    else:
        days = [{"open": i < 6, "from": "09:00", "to": "21:00"} for i in range(7)]
    return {"set": bool(raw or (r["open_time"] and r["close_time"])), "days": days}


@router.get("/hours")
async def get_hours(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    return _hours_out(await ctx.conn.fetchrow("SELECT open_time, close_time, closed_days, day_hours FROM app.tenants WHERE id=$1", ctx.tenant_id))


@router.put("/hours")
async def put_hours(body: HoursPut, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """`always_open` clears the hours (no after-hours or counting-stopped alerts). Otherwise seven days, each open with from and to, or closed."""
    import json
    if body.always_open:
        await ctx.conn.execute("UPDATE app.tenants SET open_time=NULL, close_time=NULL, closed_days='{}', day_hours=NULL WHERE id=$1", ctx.tenant_id)
        await ctx.audit("opening_hours.update", "tenant", ctx.tenant_id, {"always_open": True})
        return _hours_out(await ctx.conn.fetchrow("SELECT open_time, close_time, closed_days, day_hours FROM app.tenants WHERE id=$1", ctx.tenant_id))
    if len(body.days) != 7:
        raise ApiError(422, "invalid_hours", "Send all seven days, Monday first.")
    per, closed, first = {}, [], None
    for i, d in enumerate(body.days):
        if not d.open:
            closed.append(i)
            continue
        if d.from_ is None or d.to is None or d.from_ == d.to:
            raise ApiError(422, "invalid_hours", "Give each open day an opening and a closing time that differ.")
        per[str(i)] = [d.from_.strftime("%H:%M"), d.to.strftime("%H:%M")]
        first = first or d
    if not per:
        raise ApiError(422, "invalid_hours", "A shop closed every day has no hours. Choose Always open instead.")
    # open_time and close_time keep the first open day's times: the checks that look for "hours are set" still work, and so does the customer's own app.
    await ctx.conn.execute("UPDATE app.tenants SET open_time=$2, close_time=$3, closed_days=$4, day_hours=$5::jsonb WHERE id=$1", ctx.tenant_id, first.from_, first.to, closed, json.dumps(per))
    await ctx.audit("opening_hours.update", "tenant", ctx.tenant_id, {"days": per})
    return _hours_out(await ctx.conn.fetchrow("SELECT open_time, close_time, closed_days, day_hours FROM app.tenants WHERE id=$1", ctx.tenant_id))


# ---------------------------------------------------------------------------------------------------------- the people who get messages
class PersonIn(Strict):
    name: str = Field(..., min_length=1, max_length=80)
    phone: str = Field(..., min_length=5, max_length=20)


@router.get("/people")
async def people(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    return await customer_alerts.list_recipients(ctx)


@router.post("/people", status_code=201)
async def add_person(body: PersonIn, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """Staff confirm for the shop that this person has agreed to receive WhatsApp messages (the Control Center says so next to the form)."""
    return await customer_alerts.add_recipient(customer_alerts.RecipientIn(name=body.name, phone=body.phone, consent_confirmed=True), ctx)


@router.patch("/people/{rid}")
async def edit_person(rid: UUID, body: dict, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    allowed = {k: v for k, v in body.items() if k in ("name", "phone", "active")}
    return await customer_alerts.patch_recipient(rid, customer_alerts.RecipientPatch(**allowed, consent_confirmed=True), ctx)


@router.delete("/people/{rid}", status_code=204)
async def remove_person(rid: UUID, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    await customer_alerts.delete_recipient(rid, ctx)


# ---------------------------------------------------------------------------------------------------------- events (the alerts a shop has)
class EventIn(Strict):
    label: str | None = Field(None, max_length=80)
    enabled: bool = True
    params: dict = Field(default_factory=dict)
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=50)
    quiet_start: time | None = None
    quiet_end: time | None = None


@router.get("/events")
async def events(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """Every alert this shop can have, with its settings, the people chosen, whether it is on and the name given to it."""
    out = await customer_alerts.alert_types(ctx)
    labels = {r["rule_type"]: r["label"] for r in await ctx.conn.fetch("SELECT rule_type, label FROM app.alert_rules")}
    saved = {r["rule_type"] for r in await ctx.conn.fetch("SELECT rule_type FROM app.alert_rules")}
    return [{**o, "label": labels.get(o["type"]), "saved": o["type"] in saved, "default": o["type"] in DEFAULT_ENABLED} for o in out]


@router.put("/events/{rule_type}")
async def set_event(rule_type: str, body: EventIn, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    out = await customer_alerts.set_alert_type(rule_type, customer_alerts.RuleIn(enabled=body.enabled, params=body.params, recipient_ids=body.recipient_ids,
                                                                                 quiet_start=body.quiet_start, quiet_end=body.quiet_end), ctx)
    await ctx.conn.execute("UPDATE app.alert_rules SET label=$3 WHERE tenant_id=$1 AND rule_type=$2", ctx.tenant_id, rule_type, body.label)
    return {**out, "label": body.label}


@router.delete("/events/{rule_type}", status_code=204)
async def delete_event(rule_type: str, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    """The shop stops getting this event. An alert that is on by default is kept as switched off (otherwise the default would bring it back); any other is removed.
    This changes this one customer only, never the alert itself."""
    if rule_type not in await alert_catalog.visible(ctx.conn, ctx.tenant_id):
        raise ApiError(404, "alert_type_not_found", "That alert type does not exist.")
    if rule_type in DEFAULT_ENABLED:
        await ctx.conn.execute("""INSERT INTO app.alert_rules (tenant_id, rule_type, enabled) VALUES ($1,$2,false) ON CONFLICT (tenant_id, rule_type) DO UPDATE SET enabled=false""", ctx.tenant_id, rule_type)
    else:
        await ctx.conn.execute("DELETE FROM app.alert_rules WHERE rule_type=$1", rule_type)
    await ctx.audit("alert_rule.delete", "alert_rule", None, {"type": rule_type})


@router.post("/events/{rule_type}/test")
async def test_event(rule_type: str, body: customer_alerts.TestIn, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    """Sends one test message of this event to one person of the shop, marked as a test."""
    return await customer_alerts.send_test(rule_type, body, ctx)


# ---------------------------------------------------------------------------------------------------------- marked areas on a camera
@router.get("/area-kinds")
async def area_kinds(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    return await customer_areas.area_kinds(ctx)


@router.get("/cameras/{camera_id}/areas")
async def get_areas(camera_id: UUID, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    return await customer_areas.get_areas(camera_id, ctx)


@router.put("/cameras/{camera_id}/areas")
async def put_areas(camera_id: UUID, body: AreasPut, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    return await customer_areas.put_areas(camera_id, body, ctx)


# ---------------------------------------------------------------------------------------------------------- recorders and cameras
@router.get("/recorders/{recorder_id}/channels", response_model=list[ChannelOut])
async def channels(recorder_id: UUID, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """The channels a recorder has reported, so the cameras can be chosen. Empty until the recorder has connected."""
    return await customer_recorders.channels(recorder_id, ctx)


@router.get("/cameras")
async def cameras(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    return await customer_cameras.list_cameras(ctx)


async def _everything(ctx: CustomerCtx) -> None:
    """Every customer gets everything: there is no plan to choose in the Control Center. When a shop is on a plan without number plate reading, it is moved to the
    cheapest plan that has it (and every alert), so what staff switch on is never refused for a reason the customer cannot see."""
    f = await ctx.conn.fetchval("SELECT p.features FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id WHERE t.id=$1", ctx.tenant_id) or {}
    if f.get("anpr"):
        return
    best = await ctx.conn.fetchval("SELECT id FROM platform.plans WHERE active AND (features->>'anpr')::boolean IS TRUE ORDER BY (alert_types IS NOT NULL), price_per_camera_paise LIMIT 1")
    if best is None:
        raise ApiError(409, "no_full_plan", "There is no plan with number plate reading. Create one in Platform Admin first.")
    await ctx.conn.execute("UPDATE app.tenants SET plan_id=$2, price_override_paise=NULL WHERE id=$1", ctx.tenant_id, best)
    await ctx.audit("plan.change", "tenant", ctx.tenant_id, {"plan_id": best, "why": "Control Center: every customer gets everything"})


@router.post("/cameras", status_code=201)
async def add_camera(body: CameraCreate, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    if body.use_anpr:
        await _everything(ctx)
    return await customer_cameras.add_camera(body, ctx)


@router.patch("/cameras/{camera_id}")
async def edit_camera(camera_id: UUID, body: CameraPatch, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    if body.use_anpr:
        await _everything(ctx)
    return await customer_cameras.patch_camera(camera_id, body, ctx)


@router.get("/recorders/{recorder_id}/channels/{channel_no}/snapshot")
async def channel_snapshot(recorder_id: UUID, channel_no: int, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))) -> Response:
    """A current picture of one channel of a recorder, so it can be seen before it is added as a camera."""
    import uuid as _uuid
    from ..services import snapshot
    row = await ctx.conn.fetchrow("SELECT NULL::bytea AS source_ciphertext, $2::int AS channel_no, r.device_id, r.protocol::text AS protocol FROM app.recorders r WHERE r.id=$1", recorder_id, channel_no)
    if not row:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    jpeg = await snapshot.get(_uuid.uuid5(recorder_id, str(channel_no)), row, lambda: "")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/zones")
async def get_zones(camera_id: UUID, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """The counting line and the plate area marked on a camera (either may be missing)."""
    return await customer_cameras.get_zones(camera_id, ctx)


@router.put("/cameras/{camera_id}/zones")
async def put_zones(camera_id: UUID, body: ZonesPut, ctx: CustomerCtx = Depends(shop_ctx(*EDITORS))):
    """Replaces the counting line and the plate area of a camera. Send both when both are wanted: what is left out is removed."""
    return await customer_cameras.put_zones(camera_id, body, ctx)


@router.get("/cameras/{camera_id}/snapshot")
async def snapshot(camera_id: UUID, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))) -> Response:
    """A current still picture of the camera, for drawing an area on."""
    return await customer_cameras.camera_snapshot(camera_id, ctx)
