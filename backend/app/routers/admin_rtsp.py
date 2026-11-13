"""Cameras made from an RTSP address, for recorders that cannot register with us (and for customers with a static IP or a VPN).
The owner sets it up; the address holds the camera's password, so it is stored encrypted, bound to the camera, and never shown again."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator

from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Name, Reason, Strict
from ..services import rtsp
from .admin_framerate import parse_source

router = APIRouter(prefix="/admin/v1", tags=["admin: rtsp cameras"])
ROLES = ("owner", "onboarding")


class RtspCameraIn(Strict):
    name: Name
    url: str = Field(min_length=8, max_length=500)
    use_footfall: bool = False
    use_anpr: bool = False
    stream: str = Field("sub", pattern="^(sub|main)$")
    check_first: bool = True                        # open the stream before saving, so a wrong address is caught now
    reason: Reason

    @model_validator(mode="after")
    def _one_use(self):
        if not (self.use_footfall or self.use_anpr):
            raise ValueError("choose at least one use: footfall or number plates")
        return self


class CheckIn(Strict):
    url: str = Field(min_length=8, max_length=500)


@router.post("/rtsp-check")
async def check(body: CheckIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """Opens the address from the server and reports what it found. Nothing is saved, and the address is not repeated in the answer."""
    host = parse_source(body.url)
    await rtsp.ensure_reachable_target(body.url)
    result = await rtsp.probe(body.url)
    await ctx.audit("camera.rtsp.check", after={"host": host, "ok": result["ok"]})
    return {"host": host, **result}


@router.post("/tenants/{tenant_id}/rtsp-cameras", status_code=201)
async def create(tenant_id: UUID, body: RtspCameraIn, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    t = await ctx.conn.fetchrow("SELECT t.status::text AS status, t.camera_limit, p.features FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id WHERE t.id=$1 FOR UPDATE OF t", tenant_id)
    if not t:
        raise ApiError(404, "tenant_not_found", "Tenant not found.")
    if t["status"] in ("suspended", "cancelled"):
        raise ApiError(409, "tenant_not_active", "This account is suspended or cancelled.")
    host = parse_source(body.url)
    await rtsp.ensure_reachable_target(body.url)
    if body.use_anpr and not (t["features"] or {}).get("anpr"):
        raise ApiError(403, "plan_feature_missing", "Number plate reading is not included in this customer's plan.")
    if await ctx.conn.fetchval("SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled", tenant_id) >= t["camera_limit"]:
        raise ApiError(409, "camera_limit_reached", f"This plan allows {t['camera_limit']} cameras.")
    if body.check_first:
        res = await rtsp.probe(body.url)
        if not res["ok"]:
            raise ApiError(422, "address_unreachable", res["problem"])
    row = await ctx.conn.fetchrow(
        """INSERT INTO app.cameras (tenant_id, connection, name, use_footfall, use_anpr, stream, source_host, source_set_at)
           VALUES ($1,'rtsp',$2,$3,$4,$5,$6, now()) RETURNING id, name, connection, use_footfall, use_anpr, stream, source_host""",
        tenant_id, body.name, body.use_footfall, body.use_anpr, body.stream, host)
    await ctx.conn.execute("UPDATE app.cameras SET source_ciphertext=$2 WHERE id=$1", row["id"], vault().encrypt(body.url, str(row["id"])))
    await ctx.audit("camera.rtsp.create", tenant_id=tenant_id, target_type="camera", target_id=row["id"], reason=body.reason,
                    after={"name": body.name, "host": host, "footfall": body.use_footfall, "anpr": body.use_anpr, "checked": body.check_first})
    return {**dict(row), "has_source": True}
