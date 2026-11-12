"""Service-to-service routes for the registrar. Not exposed publicly: put them on a private listener or behind mTLS as well."""
import logging
import re
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, Query, Request, Response

from ..crypto import vault
from ..deps import ServiceCtx, service_ctx
from ..errors import ApiError
from pydantic import Field

from ..schemas import FrameStatusIn, HeartbeatIn, Strict
from datetime import datetime, timezone

from ..services import alert_catalog, billing, link_history, platform_fault, recorder_health
from ..services.alerts import clips_wanted
from ..services.framerate import PARTS, camera_override, resolve, tenant_policy

router = APIRouter(prefix="/internal/v1", tags=["internal"])
log = logging.getLogger("gateinsight.internal")


async def _recorder(ctx: ServiceCtx, device_id: str):
    row = await ctx.conn.fetchrow(
        """SELECT r.id, r.tenant_id, r.device_id, r.name, r.status::text AS link_status, r.key_ciphertext, r.brand::text AS brand, r.protocol::text AS protocol, t.status::text AS tenant_status
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
    if r["link_status"] != "online":                           # it was not online a moment ago: note it, and say so if the customer was told it was down
        await link_history.record(ctx.conn, r["tenant_id"], r["id"], "online")
        await link_history.recovered(ctx.conn, r["tenant_id"], offline_rule="recorder_offline", entity_id=r["id"], name=r["name"] or r["device_id"])
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


class DiskIn(Strict):
    num: int = Field(ge=0, le=255)
    total_mb: int = Field(ge=0)
    free_mb: int = Field(ge=0)
    status: int = Field(ge=0)
    state: int = Field(ge=0, le=15)           # the low four bits of status: 0 asleep, 1 working, 2 faulty
    life_percent: float | None = Field(None, ge=0, le=100)      # how much of the disk's life is left, when the recorder says
    model: str | None = Field(None, max_length=40)              # the make and serial number of the disk, when the recorder gives them
    serial: str | None = Field(None, max_length=40)


class ClockIn(Strict):
    utc: int | None = None                    # the recorder's time as seconds since 1970, when it gives one
    local: str | None = Field(None, max_length=32)      # its own wall-clock time, "2026-11-12T14:05:09"


class ChannelRecordings(Strict):
    recent: int | None = Field(None, ge=0)    # recordings held for the last two hours (None: could not be asked)
    day: int | None = Field(None, ge=0)       # recordings held for the last day


class HardwareIn(Strict):
    temperature_c: float | None = Field(None, ge=-40, le=150)
    fan_ok: bool | None = None
    poe_fault: bool | None = None


class HealthIn(Strict):
    disks: list[DiskIn] | None = Field(None, max_length=64)        # None: could not be read; []: read, and there are none
    clock: ClockIn | None = None
    recording: dict[str, ChannelRecordings] = Field(default_factory=dict, max_length=256)
    recording_now: dict[str, bool] = Field(default_factory=dict, max_length=256)       # for each channel, is the recorder saving its video this moment
    days_held: int | None = Field(None, ge=0, le=3650)             # about how many days of video the recorder holds (None: could not be found out)
    uptime_seconds: int | None = Field(None, ge=0)                 # how long since the recorder last started
    channel_online: dict[str, bool] = Field(default_factory=dict, max_length=256)     # for each channel, does the recorder see its camera
    hardware: HardwareIn | None = None


class AlarmIn(Strict):
    kind: str = Field(pattern="^(disk_error|disk_missing|storage_low|video_loss|video_tamper|reboot)$")
    channel: int | None = Field(None, ge=1, le=1024)
    disk: int | None = Field(None, ge=0, le=255)
    percent: int | None = Field(None, ge=0, le=100)
    at: datetime | None = None


class AlarmBatch(Strict):
    events: list[AlarmIn] = Field(min_length=1, max_length=100)


@router.post("/recorders/{device_id}/health")
async def recorder_health_report(device_id: str, body: HealthIn, ctx: ServiceCtx = Depends(service_ctx)):
    """The disks, clock and recordings of a recorder, as the Dahua gateway read them. Stored, and turned into alerts where something is wrong."""
    r = await _recorder(ctx, device_id)
    rec = await ctx.conn.fetchrow("SELECT r.id, r.tenant_id, r.device_id, r.name, t.timezone FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.id=$1", r["id"])
    raised = await recorder_health.apply(ctx.conn, rec, body.model_dump())
    return {"raised": raised}


@router.post("/recorders/{device_id}/alarms")
async def recorder_alarms(device_id: str, body: AlarmBatch, ctx: ServiceCtx = Depends(service_ctx)):
    """What the recorder itself announced (a disk failed, the disk is nearly full, a camera was covered or lost). Turned into the same alerts as the readings, within seconds."""
    r = await _recorder(ctx, device_id)
    rec = await ctx.conn.fetchrow("SELECT r.id, r.tenant_id, r.device_id, r.name, t.timezone FROM app.recorders r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.id=$1", r["id"])
    raised = await recorder_health.apply_alarms(ctx.conn, rec, [e.model_dump() for e in body.events])
    return {"raised": raised}


@router.post("/recorders/{device_id}/offline", status_code=204)
async def offline(device_id: str, ctx: ServiceCtx = Depends(service_ctx)):
    r = await _recorder(ctx, device_id)
    if await ctx.conn.fetchval("UPDATE app.recorders SET status='offline' WHERE id=$1 AND status='online' RETURNING 1", r["id"]):
        await link_history.record(ctx.conn, r["tenant_id"], r["id"], "offline")


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
    await platform_fault.worker_polled(ctx.conn, shard, of)          # how we know our own analysis is alive (see services/platform_fault.py)
    rows = await ctx.conn.fetch(
        """SELECT c.id FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id LEFT JOIN app.recorders r ON r.id = c.recorder_id
           WHERE c.enabled AND (c.use_footfall OR c.use_anpr) AND t.status::text NOT IN ('suspended','cancelled')
             AND (c.source_ciphertext IS NOT NULL OR r.protocol::text IN ('gb28181','auto_register'))
             AND abs(hashtext(c.id::text)::bigint) % $2 = $1 ORDER BY c.id""", shard, of)
    return {"cameras": [str(r["id"]) for r in rows]}


@router.get("/cameras/{camera_id}/config")
async def camera_config(camera_id: UUID, ctx: ServiceCtx = Depends(service_ctx)):
    """What an AI worker needs to analyse one camera: what to detect, at what frame rate, and the counting line and plate zone.
    The worker asks again every half minute, so a frame-rate change made in the admin console takes effect without a restart."""
    c = await ctx.conn.fetchrow(
        f"""SELECT c.id, c.tenant_id, c.name, c.enabled, c.use_footfall, c.use_anpr, c.use_areas, c.stream, c.source_set_at, t.status::text AS tenant_status, t.msg_clips, {_POLICY_COLS}
            FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id = $1""", camera_id)
    if not c:
        raise ApiError(404, "unknown_camera", "No such camera.")
    zones = await ctx.conn.fetch("SELECT kind::text AS kind, points, in_direction FROM app.camera_zones WHERE camera_id=$1 AND kind IN ('count_line','anpr_zone') ORDER BY kind", camera_id)
    # Marked areas are sent only for the kinds this customer has an alert for, so a camera analyses nothing it will not be used for.
    ok = set(await alert_catalog.visible(ctx.conn, c["tenant_id"])) if c["use_areas"] else set()
    areas = [dict(a) for a in await ctx.conn.fetch("SELECT id, kind::text AS kind, name, points, params FROM app.camera_zones WHERE camera_id=$1 AND kind::text = ANY($2::text[]) ORDER BY created_at, id",
                                                   camera_id, list(alert_catalog.AREA_ALERTS))] if ok else []
    areas = [a for a in areas if alert_catalog.AREA_ALERTS[a["kind"]] in ok]
    rule = await ctx.conn.fetchrow("SELECT enabled, params FROM app.alert_rules WHERE tenant_id=(SELECT tenant_id FROM app.cameras WHERE id=$1) AND rule_type='blocked_plate'", camera_id)
    return {"clips": bool(c["use_anpr"]) and clips_wanted(c, rule), "camera_id": c["id"], "name": c["name"], "enabled": c["enabled"], "use_footfall": c["use_footfall"], "use_anpr": c["use_anpr"], "use_areas": bool(areas),
            "stream": c["stream"], "tenant_status": c["tenant_status"], "source_set_at": c["source_set_at"],
            "active": c["enabled"] and c["tenant_status"] not in ("suspended", "cancelled"),
            "frame_rate": resolve(tenant_policy(c), camera_override(c)),
            "zones": [dict(z) for z in zones], "areas": areas}


@router.get("/cameras/{camera_id}/source")
async def camera_source(camera_id: UUID, response: Response, ctx: ServiceCtx = Depends(service_ctx)):
    """Where the worker gets this camera's video from. Never log the response.
      {"kind": "rtsp", "url": ...}                              an RTSP address an administrator set (it holds the camera password)
      {"kind": "gb28181", "device_id": ..., "channel_no": ...}   a recorder that registered with us: ask the video relay for it
      {"kind": "auto_register", "device_id": ..., "channel_no": ..., "stream": "main"|"sub"}   a Dahua recorder: ask the Dahua gateway for it
    An address set by an administrator wins, so a technician can point one camera at a VPN address while the rest use the relay."""
    c = await ctx.conn.fetchrow(
        """SELECT c.source_ciphertext, c.enabled, c.channel_no, c.stream, r.device_id, r.protocol::text AS protocol, t.status::text AS tenant_status
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
    if c["protocol"] == "auto_register":                  # a Dahua recorder connected to our Dahua gateway; it can serve the main or the sub stream
        return {"kind": "auto_register", "device_id": c["device_id"], "channel_no": c["channel_no"], "stream": c["stream"]}
    raise ApiError(404, "no_source", "No stream address is set for this camera.")


_URL_USERINFO = re.compile(r"(rtsps?://)[^@\s/]+@", re.I)


@router.post("/cameras/{camera_id}/frame-status")
async def frame_status(camera_id: UUID, body: FrameStatusIn, ctx: ServiceCtx = Depends(service_ctx)):
    """Health heartbeat from the worker, every few seconds. A quiet camera sends no events, so this is how we know it is alive.
    The answer tells the worker whether it should keep going."""
    err = _URL_USERINFO.sub(r"\1***@", body.error) if body.error else None            # belt and braces: no passwords, whatever the worker sent
    prev = await ctx.conn.fetchrow("SELECT c.stream_seen_at, c.stream_state, c.name, c.tenant_id, t.timezone FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id=$1", camera_id)
    row = await ctx.conn.fetchrow(
        """UPDATE app.cameras c SET stream_state=$2, stream_seen_at=now(), analysis_fps=$3, stream_error=$4, frames_dropped=$5,
                  stream_state_at = CASE WHEN c.stream_state IS DISTINCT FROM $2 THEN now() ELSE c.stream_state_at END
           FROM app.tenants t WHERE c.id=$1 AND t.id = c.tenant_id RETURNING c.enabled, t.status::text AS tenant_status""",
        camera_id, body.state, body.analysis_fps, err, body.dropped)
    if not row:
        raise ApiError(404, "unknown_camera", "No such camera.")
    if prev and body.state == "streaming" and prev["stream_state"] == "offline":              # it was down: tell the customer if they were warned
        await link_history.recovered(ctx.conn, prev["tenant_id"], offline_rule="camera_offline", entity_id=camera_id, name=prev["name"], camera_id=camera_id)
    if prev and body.state == "streaming" and body.analysis_fps and prev["stream_seen_at"]:
        # frames analysed since the last report, for metering (a long gap counts as at most a minute: the worker was not running in between)
        secs = min((datetime.now(timezone.utc) - prev["stream_seen_at"]).total_seconds(), 60.0)
        await billing.add_frames(ctx.conn, prev["tenant_id"], prev["timezone"], int(body.analysis_fps * max(secs, 0)))
    return {"active": row["enabled"] and row["tenant_status"] not in ("suspended", "cancelled")}


# ------------------------------------------------------------------------------ "what the AI sees"
@router.get("/cameras/{camera_id}/ai-view")
async def ai_view_wanted(camera_id: UUID, ctx: ServiceCtx = Depends(service_ctx)):
    """Is anyone looking at this camera's annotated picture right now? The worker asks every few seconds and only draws and uploads while the answer is yes."""
    row = await ctx.conn.fetchrow("SELECT coalesce(ai_view_until > now(), false) AS on FROM app.cameras WHERE id=$1", camera_id)
    if not row:
        raise ApiError(404, "unknown_camera", "No such camera.")
    return {"on": row["on"]}


@router.post("/cameras/{camera_id}/ai-frame", status_code=204)
async def ai_view_frame(camera_id: UUID, request: Request, ctx: ServiceCtx = Depends(service_ctx)):
    """The annotated picture. Kept as one row per camera, overwritten each time, and only accepted while someone is looking."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > 600_000:
        raise ApiError(413, "too_large", "The picture is too large.")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > 600_000:
            raise ApiError(413, "too_large", "The picture is too large.")
    if not data.startswith(b"\xff\xd8\xff"):
        raise ApiError(415, "not_a_jpeg", "Send a JPEG picture.")
    cam = await ctx.conn.fetchrow("SELECT tenant_id, coalesce(ai_view_until > now(), false) AS on FROM app.cameras WHERE id=$1", camera_id)
    if not cam:
        raise ApiError(404, "unknown_camera", "No such camera.")
    if not cam["on"]:
        raise ApiError(409, "not_wanted", "Nobody is looking at this camera's picture now.")
    await ctx.conn.execute(
        """INSERT INTO app.ai_view_frames (camera_id, tenant_id, taken_at, jpeg) VALUES ($1,$2, now(), $3)
           ON CONFLICT (camera_id) DO UPDATE SET taken_at=now(), jpeg=$3""", camera_id, cam["tenant_id"], data)
    await ctx.conn.execute("DELETE FROM app.ai_view_frames WHERE taken_at < now() - interval '10 minutes'")        # never kept
