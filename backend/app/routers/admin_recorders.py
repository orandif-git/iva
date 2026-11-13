"""Staff can see every recorder and how to connect it. Customers connect their own Dahua recorders, rename them and remove them;
only staff can add the other types (GB28181 recorders and the "other" brand), here."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import AdminRecorderOut, RecorderConnection, RecorderCreateAdmin, RecorderCredentials
from ..services import recorders as svc

router = APIRouter(prefix="/admin/v1", tags=["admin: recorders"])
ROLES = ("owner", "onboarding")


@router.post("/tenants/{tenant_id}/recorders", response_model=RecorderCredentials, status_code=201)
async def add_recorder(tenant_id: UUID, body: RecorderCreateAdmin, response: Response, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """Adds a recorder for a customer, including the types customers cannot add themselves (GB28181, the "other" brand).
    Hikvision, ISUP and cloud_api are refused. The answer carries the key, shown once and never stored in the audit log."""
    out = await svc.create_recorder(ctx.conn, tenant_id, body.brand, body.site_id, None, body.protocol, body.name, staff=True)
    await ctx.audit("recorder.admin_register", tenant_id=tenant_id, target_type="recorder", target_id=out["recorder_id"], reason=body.reason,
                    after={"device_id": out["device_id"], "brand": body.brand, "protocol": out["protocol"]})
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get("/recorders", response_model=list[AdminRecorderOut])
async def list_recorders(
    status: str | None = Query(None, pattern="^(never|online|offline)$"),
    tenant_id: UUID | None = None,
    q: str | None = Query(None, max_length=80),
    limit: int = Query(200, ge=1, le=500),
    ctx: AdminCtx = Depends(admin_ctx()),
):
    where, args = [], []
    if status:
        args.append(status); where.append(f"r.status = ${len(args)}::app.link_status")
    if tenant_id:
        args.append(tenant_id); where.append(f"r.tenant_id = ${len(args)}")
    if q:
        args.append(f"%{q}%"); where.append(f"(r.device_id ILIKE ${len(args)} OR t.name ILIKE ${len(args)})")
    args.append(limit)
    rows = await ctx.conn.fetch(
        f"SELECT {svc.RECORDER_COLS}, t.name AS tenant_name FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id"
        + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY r.created_at DESC LIMIT ${len(args)}", *args)
    return [dict(r) for r in rows]


@router.get("/recorders/{recorder_id}/connection", response_model=RecorderConnection)
async def recorder_connection(recorder_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    """The details to type into the recorder, without the password (which is shown only when it is made; rotate-key makes a new one)."""
    row = await ctx.conn.fetchrow("SELECT id, device_id, brand::text AS brand, protocol::text AS protocol FROM app.recorders WHERE id=$1", recorder_id)
    if not row:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    return svc.connection_details(row)


# ---------------------------------------------------------------------------------------------- change, new password, delete
import re

from ..crypto import vault
from ..schemas import Strict
from ..services import alert_catalog as cat

EDIT_ROLES = ("owner", "onboarding")            # Owner (and Admin) and Onboarding may change recorders; Support may only look
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,39}$")
FOOTFALL_EVENTS = ("crowding", "after_hours", "counting_stopped")
PLATE_EVENTS = ("blocked_plate", "vehicle_overstay", "low_confidence_plate")


class RecorderEdit(Strict):
    name: str | None = None                      # empty clears it and shows the id again
    device_id: str | None = None
    reason: str | None = None


async def _one(ctx, recorder_id: UUID):
    r = await ctx.conn.fetchrow("SELECT id, tenant_id, device_id, name, protocol::text AS protocol, key_ciphertext FROM app.recorders WHERE id=$1", recorder_id)
    if not r:
        raise ApiError(404, "recorder_not_found", "Recorder not found.")
    return r


async def _supported(conn, tenant_id, skip) -> set:
    """The events of this shop that still have something to watch, if the cameras of recorder `skip` were not there."""
    cams = await conn.fetchrow("SELECT coalesce(bool_or(use_footfall),false) AS foot, coalesce(bool_or(use_anpr),false) AS plates FROM app.cameras "
                               "WHERE tenant_id=$1 AND ($2::uuid IS NULL OR recorder_id IS DISTINCT FROM $2)", tenant_id, skip)
    kinds = {r["kind"] for r in await conn.fetch("SELECT DISTINCT z.kind::text AS kind FROM app.camera_zones z JOIN app.cameras c ON c.id=z.camera_id "
                                                  "WHERE c.tenant_id=$1 AND ($2::uuid IS NULL OR c.recorder_id IS DISTINCT FROM $2)", tenant_id, skip)}
    out = {cat.AREA_ALERTS[k] for k in kinds if k in cat.AREA_ALERTS}
    if cams["foot"]:
        out |= set(FOOTFALL_EVENTS)
    if cams["plates"]:
        out |= set(PLATE_EVENTS)
    return out


async def _orphans(conn, tenant_id, recorder_id) -> list:
    """Events this shop has now that would have nothing left to watch without this recorder."""
    lost = (await _supported(conn, tenant_id, None)) - (await _supported(conn, tenant_id, recorder_id))
    have = {r["rule_type"] for r in await conn.fetch("SELECT rule_type FROM app.alert_rules WHERE tenant_id=$1", tenant_id)}
    return sorted(lost & have)


@router.patch("/recorders/{recorder_id}")
async def edit_recorder(recorder_id: UUID, body: RecorderEdit, ctx: AdminCtx = Depends(admin_ctx(*EDIT_ROLES))):
    """Changes the name, and the id the recorder introduces itself with. The id is also what the stored password is locked to, so it is re-locked here.
    After the id changes, the recorder must be given the new id in its own settings, or it stays offline."""
    r = await _one(ctx, recorder_id)
    sets, args, after = [], [recorder_id], {}
    if body.name is not None:
        name = body.name.strip() or None
        if name and len(name) > 120:
            raise ApiError(422, "invalid_name", "The name can be up to 120 characters.")
        args.append(name); sets.append(f"name=${len(args)}"); after["name"] = name
    if body.device_id is not None and body.device_id.strip() != r["device_id"]:
        new = body.device_id.strip()
        if r["protocol"] == "gb28181":
            if not re.fullmatch(r"\d{20}", new):
                raise ApiError(422, "invalid_id", "This type of recorder needs a 20-digit id.")
        elif not _ID.match(new):
            raise ApiError(422, "invalid_id", "Use 3 to 40 letters, numbers, dashes or underscores.")
        if await ctx.conn.fetchval("SELECT 1 FROM app.recorders WHERE device_id=$1", new):
            raise ApiError(409, "id_taken", "Another recorder already uses this id.")
        if r["key_ciphertext"] is not None:
            key = vault().decrypt(bytes(r["key_ciphertext"]), r["device_id"])
            args.append(vault().encrypt(key, new)); sets.append(f"key_ciphertext=${len(args)}")
        args.append(new); sets.append(f"device_id=${len(args)}")
        sets.append("status='never'::app.link_status"); after["device_id"] = new          # it has to connect again under its new id
    if not sets:
        return {"recorder_id": recorder_id, "device_id": r["device_id"], "name": r["name"], "changed": False}
    await ctx.conn.execute(f"UPDATE app.recorders SET {', '.join(sets)} WHERE id=$1", *args)
    await ctx.audit("recorder.edit", tenant_id=r["tenant_id"], target_type="recorder", target_id=recorder_id, reason=body.reason,
                    before={"device_id": r["device_id"], "name": r["name"]}, after=after)
    row = await _one(ctx, recorder_id)
    return {"recorder_id": recorder_id, "device_id": row["device_id"], "name": row["name"], "changed": True, "id_changed": "device_id" in after}


@router.post("/recorders/{recorder_id}/new-password", response_model=RecorderCredentials)
async def new_password(recorder_id: UUID, response: Response, ctx: AdminCtx = Depends(admin_ctx(*EDIT_ROLES))):
    """A new password for the recorder, shown once. The old one stops working at once."""
    r = await _one(ctx, recorder_id)
    out = await svc.rotate_key(ctx.conn, recorder_id)
    await ctx.audit("recorder.new_password", tenant_id=r["tenant_id"], target_type="recorder", target_id=recorder_id, after={"device_id": r["device_id"]})
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get("/recorders/{recorder_id}/delete-preview")
async def delete_preview(recorder_id: UUID, ctx: AdminCtx = Depends(admin_ctx(*EDIT_ROLES))):
    r = await _one(ctx, recorder_id)
    cams = await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE recorder_id=$1", recorder_id)
    return {"name": r["name"] or r["device_id"], "cameras": cams, "events": [{"type": t, "name": cat.event_name(t), "title": cat.CATALOG[t]["title"]} for t in await _orphans(ctx.conn, r["tenant_id"], recorder_id)]}


@router.delete("/recorders/{recorder_id}", status_code=204)
async def delete_recorder(recorder_id: UUID, ctx: AdminCtx = Depends(admin_ctx(*EDIT_ROLES))):
    """Removes the recorder, its cameras (with their counts, plate reads and marked areas) and the events left with nothing to watch. Alerts and the message log stay
    until the data-kept clean-up removes them. The recorder is refused the next time it tries to connect."""
    r = await _one(ctx, recorder_id)
    gone = await _orphans(ctx.conn, r["tenant_id"], recorder_id)
    cams = await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE recorder_id=$1", recorder_id)
    await ctx.conn.execute("DELETE FROM app.recorders WHERE id=$1", recorder_id)
    if gone:
        await ctx.conn.execute("DELETE FROM app.alert_rules WHERE tenant_id=$1 AND rule_type = ANY($2::text[])", r["tenant_id"], gone)
    await ctx.audit("recorder.delete", tenant_id=r["tenant_id"], target_type="recorder", target_id=recorder_id,
                    before={"device_id": r["device_id"], "name": r["name"]}, after={"cameras_removed": cams, "events_removed": gone})
