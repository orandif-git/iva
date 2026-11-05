"""Service-to-service routes for the registrar. Not exposed publicly: put them on a private listener or behind mTLS as well."""
import logging
import re
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, Query, Response

from ..crypto import vault
from ..deps import ServiceCtx, service_ctx
from ..errors import ApiError
from ..schemas import FrameStatusIn, HeartbeatIn
from datetime import datetime, timezone

from ..services import billing
from ..services.alerts import clips_wanted
from ..services.framerate import PARTS, camera_override, resolve, tenant_policy

router = APIRouter(prefix="/internal/v1", tags=["internal"])
log = logging.getLogger("gateinsight.internal")


async def _recorder(ctx: ServiceCtx, device_id: str):
    row = await ctx.conn.fetchrow(
        """SELECT r.id, r.tenant_id, r.device_id, r.key_ciphertext, r.brand::text AS brand, r.protocol::text AS protocol, t.status::text AS tenant_status
           FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.device_id = $1""", device_id)
    if not row:
        raise ApiError(404, "unknown_device", "No such recorder.")
    return row


@router.get("/recorders/{device_id}/credentials")
async def credentials(device_id: str, response: Response, ctx: ServiceCtx = Depends(service_ctx)):
    """The registrar fetches the key to verify the recorder's handshake. Never log the response."""
    r = await _recorder(ctx, device_id)
    if r["tenant_status"] in ("suspended", "cancelled"):
        raise ApiError(403, "tenant_suspended", "Tenant is suspended.")
    response.headers["Cache-Control"] = "no-store"
    try:
        key = vault().decrypt(bytes(r["key_ciphertext"]), device_id)
    except InvalidTag:
        # The stored key does not match this device (corruption, a wrong master key, or tampering). Fail closed.
        log.error("recorder key failed integrity check device_id=%s", device_id)
        raise ApiError(500, "key_unreadable", "The stored key failed its integrity check. Rotate the key.")
    return {"device_id": device_id, "key": key, "protocol": r["protocol"], "brand": r["brand"]}


@router.post("/recorders/{device_id}/heartbeat")
async def heartbeat(device_id: str, body: HeartbeatIn, ctx: ServiceCtx = Depends(service_ctx)):
    r = await _recorder(ctx, device_id)
    allowed = r["tenant_status"] not in ("suspended", "cancelled")
    if not allowed:
        # Tell the registrar to drop the session. Do not mark the recorder online.
        return {"allowed": False}
    numbers = [c.channel_no for c in body.channels]
    await ctx.conn.execute(
        """UPDATE app.recorders SET status='online', last_heartbeat_at=now(), last_ip=$2::inet,
               channel_count=$3, firmware=coalesce($4, firmware) WHERE id=$1""",
        r["id"], str(body.ip), len(numbers), body.firmware)
    for c in body.channels:
        await ctx.conn.execute(
            """INSERT INTO app.recorder_channels (recorder_id, tenant_id, channel_no, name, external_id, active, seen_at)
               VALUES ($1,$2,$3,$4,$5,true,now())
               ON CONFLICT (recorder_id, channel_no) DO UPDATE SET name=EXCLUDED.name, external_id=EXCLUDED.external_id, active=true, seen_at=now()""",
            r["id"], r["tenant_id"], c.channel_no, c.name, c.external_id)
    await ctx.conn.execute("UPDATE app.recorder_channels SET active=false WHERE recorder_id=$1 AND NOT (channel_no = ANY($2::int[]))", r["id"], numbers)
    return {"allowed": True}


@router.post("/recorders/{device_id}/offline", status_code=204)
async def offline(device_id: str, ctx: ServiceCtx = Depends(service_ctx)):
    r = await _recorder(ctx, device_id)
    await ctx.conn.execute("UPDATE app.recorders SET status='offline' WHERE id=$1 AND status='online'", r["id"])


@router.post("/recorders/{device_id}/preview-ok", status_code=204)
async def preview_ok(device_id: str, ctx: ServiceCtx = Depends(service_ctx)):
    """Registrar reports the first decoded picture, which ticks the last box on the customer's connect screen."""
    r = await _recorder(ctx, device_id)
    await ctx.conn.execute("UPDATE app.recorders SET preview_ok_at = coalesce(preview_ok_at, now()) WHERE id=$1", r["id"])


_POLICY_COLS = ", ".join([f"c.fps_{p} AS c_{p}" for p in PARTS] + [f"t.fps_{p} AS t_{p}" for p in PARTS])


@router.get("/workers/cameras")
async def worker_cameras(shard: int = Query(0, ge=0), of: int = Query(1, ge=1, le=1000), ctx: ServiceCtx = Depends(service_ctx)):
    """The cameras one worker process should run: every camera that is switched on, has something to analyse and somewhere to get video, whose
    account is active. Cameras are split between `of` workers by a stable hash of the camera id, so each camera has exactly one worker and
    cameras added later are picked up by the next poll."""
    if shard >= of:
        raise ApiError(422, "invalid_shard", "shard must be smaller than of.")
    rows = await ctx.conn.fetch(
        """SELECT c.id FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id LEFT JOIN app.recorders r ON r.id = c.recorder_id
           WHERE c.enabled AND (c.use_footfall OR c.use_anpr) AND t.status::text NOT IN ('suspended','cancelled')
             AND (c.source_ciphertext IS NOT NULL OR r.protocol::text = 'gb28181')
             AND abs(hashtext(c.id::text)::bigint) % $2 = $1 ORDER BY c.id""", shard, of)
    return {"cameras": [str(r["id"]) for r in rows]}


@router.get("/cameras/{camera_id}/config")
async def camera_config(camera_id: UUID, ctx: ServiceCtx = Depends(service_ctx)):
    """What an AI worker needs to analyse one camera: what to detect, at what frame rate, and the counting line and plate zone.
    The worker asks again every half minute, so a frame-rate change made in the admin console takes effect without a restart."""
    c = await ctx.conn.fetchrow(
        f"""SELECT c.id, c.name, c.enabled, c.use_footfall, c.use_anpr, c.stream, c.source_set_at, t.status::text AS tenant_status, t.msg_clips, {_POLICY_COLS}
            FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id = $1""", camera_id)
    if not c:
        raise ApiError(404, "unknown_camera", "No such camera.")
    zones = await ctx.conn.fetch("SELECT kind::text AS kind, points, in_direction FROM app.camera_zones WHERE camera_id=$1 ORDER BY kind", camera_id)
    rule = await ctx.conn.fetchrow("SELECT enabled, params FROM app.alert_rules WHERE tenant_id=(SELECT tenant_id FROM app.cameras WHERE id=$1) AND rule_type='blocked_plate'", camera_id)
    return {"clips": bool(c["use_anpr"]) and clips_wanted(c, rule), "camera_id": c["id"], "name": c["name"], "enabled": c["enabled"], "use_footfall": c["use_footfall"], "use_anpr": c["use_anpr"],
            "stream": c["stream"], "tenant_status": c["tenant_status"], "source_set_at": c["source_set_at"],
            "active": c["enabled"] and c["tenant_status"] not in ("suspended", "cancelled"),
            "frame_rate": resolve(tenant_policy(c), camera_override(c)),
            "zones": [dict(z) for z in zones]}


@router.get("/cameras/{camera_id}/source")
async def camera_source(camera_id: UUID, response: Response, ctx: ServiceCtx = Depends(service_ctx)):
    """Where the worker gets this camera's video from. Never log the response.
      {"kind": "rtsp", "url": ...}                              an RTSP address an administrator set (it holds the camera password)
      {"kind": "gb28181", "device_id": ..., "channel_no": ...}   a recorder that registered with us: ask the video relay for it
    An address set by an administrator wins, so a technician can point one camera at a VPN address while the rest use the relay."""
    c = await ctx.conn.fetchrow(
        """SELECT c.source_ciphertext, c.enabled, c.channel_no, r.device_id, r.protocol::text AS protocol, t.status::text AS tenant_status
           FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id LEFT JOIN app.recorders r ON r.id = c.recorder_id WHERE c.id=$1""", camera_id)
    if not c:
        raise ApiError(404, "unknown_camera", "No such camera.")
    if not c["enabled"] or c["tenant_status"] in ("suspended", "cancelled"):
        raise ApiError(403, "camera_inactive", "This camera may not be analysed.")
    response.headers["Cache-Control"] = "no-store"
    if c["source_ciphertext"]:
        try:
            return {"kind": "rtsp", "url": vault().decrypt(bytes(c["source_ciphertext"]), str(camera_id))}
        except InvalidTag:
            log.error("camera source failed integrity check camera_id=%s", camera_id)
            raise ApiError(500, "source_unreadable", "The stored address failed its integrity check. Set it again.")
    if c["protocol"] == "gb28181":
        return {"kind": "gb28181", "device_id": c["device_id"], "channel_no": c["channel_no"]}
    raise ApiError(404, "no_source", "No stream address is set for this camera.")


_URL_USERINFO = re.compile(r"(rtsps?://)[^@\s/]+@", re.I)


@router.post("/cameras/{camera_id}/frame-status")
async def frame_status(camera_id: UUID, body: FrameStatusIn, ctx: ServiceCtx = Depends(service_ctx)):
    """Health heartbeat from the worker, every few seconds. A quiet camera sends no events, so this is how we know it is alive.
    The answer tells the worker whether it should keep going."""
    err = _URL_USERINFO.sub(r"\1***@", body.error) if body.error else None            # belt and braces: no passwords, whatever the worker sent
    prev = await ctx.conn.fetchrow("SELECT c.stream_seen_at, c.tenant_id, t.timezone FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id=$1", camera_id)
    row = await ctx.conn.fetchrow(
        """UPDATE app.cameras c SET stream_state=$2, stream_seen_at=now(), analysis_fps=$3, stream_error=$4, frames_dropped=$5,
                  stream_state_at = CASE WHEN c.stream_state IS DISTINCT FROM $2 THEN now() ELSE c.stream_state_at END
           FROM app.tenants t WHERE c.id=$1 AND t.id = c.tenant_id RETURNING c.enabled, t.status::text AS tenant_status""",
        camera_id, body.state, body.analysis_fps, err, body.dropped)
    if not row:
        raise ApiError(404, "unknown_camera", "No such camera.")
    if prev and body.state == "streaming" and body.analysis_fps and prev["stream_seen_at"]:
        # frames analysed since the last report, for metering (a long gap counts as at most a minute: the worker was not running in between)
        secs = min((datetime.now(timezone.utc) - prev["stream_seen_at"]).total_seconds(), 60.0)
        await billing.add_frames(ctx.conn, prev["tenant_id"], prev["timezone"], int(body.analysis_fps * max(secs, 0)))
    return {"active": row["enabled"] and row["tenant_status"] not in ("suspended", "cancelled")}
