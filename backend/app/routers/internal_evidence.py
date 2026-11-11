"""Workers upload evidence pictures here. Workers never hold storage credentials; this service checks and stores the file."""
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..deps import ServiceCtx, service_ctx
from ..errors import ApiError
from ..messaging import evidence

log = logging.getLogger("gateinsight.evidence")
router = APIRouter(prefix="/internal/v1", tags=["internal: evidence"])


@router.post("/evidence", status_code=201)
async def upload_evidence(request: Request, camera_id: UUID = Query(...), kind: str = Query("image", pattern="^image$"), ctx: ServiceCtx = Depends(service_ctx)):
    s = get_settings()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > s.evidence_max_bytes:
        raise ApiError(413, "too_large", "The picture is too large.")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > s.evidence_max_bytes:
            raise ApiError(413, "too_large", "The picture is too large.")
    if not data.startswith(evidence.JPEG):
        raise ApiError(415, "not_a_jpeg", "Send a JPEG picture.")
    cam = await ctx.conn.fetchrow("SELECT c.tenant_id, t.retention_snapshots_days, t.status::text AS status FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE c.id=$1", camera_id)
    if not cam:
        raise ApiError(404, "camera_not_found", "Unknown camera.")
    if cam["status"] in ("suspended", "cancelled"):
        raise ApiError(403, "tenant_suspended", "This account is suspended.")
    try:
        eid = await evidence.save_image(ctx.conn, cam["tenant_id"], camera_id, data, cam["retention_snapshots_days"])
    except Exception:
        log.exception("could not store evidence")
        raise ApiError(502, "storage_unavailable", "Could not store the picture. Try again.")
    return {"evidence_id": eid}


@router.post("/alerts/{alert_id}/clip", status_code=201)
async def upload_clip(alert_id: UUID, request: Request, ctx: ServiceCtx = Depends(service_ctx)):
    """The worker uploads the short video the backend asked for. It is attached to the alert, and a WhatsApp message that was
    waiting for it is released at once."""
    s = get_settings()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > s.clip_max_bytes:
        raise ApiError(413, "too_large", "The clip is too large.")
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > s.clip_max_bytes:
            raise ApiError(413, "too_large", "The clip is too large.")
    if data[4:8] != b"ftyp":
        raise ApiError(415, "not_an_mp4", "Send an MP4 video.")
    a = await ctx.conn.fetchrow(
        """SELECT a.tenant_id, a.camera_id, a.clip_id, a.clip_wait_until, a.created_at, t.retention_snapshots_days, t.status::text AS status
           FROM app.alerts a JOIN app.tenants t ON t.id = a.tenant_id WHERE a.id=$1 FOR UPDATE OF a""", alert_id)
    if not a:
        raise ApiError(404, "alert_not_found", "No such alert.")
    if a["status"] in ("suspended", "cancelled"):
        raise ApiError(403, "tenant_suspended", "This account is suspended.")
    if a["clip_wait_until"] is None:
        raise ApiError(409, "clip_not_expected", "No clip was asked for on this alert.")
    if a["clip_id"] is not None:
        return JSONResponse({"evidence_id": str(a["clip_id"]), "already_attached": True}, status_code=200)
    if datetime.now(timezone.utc) > a["created_at"] + timedelta(hours=1):
        raise ApiError(409, "too_late", "This alert is too old to take a clip.")
    eid = uuid.uuid4()
    key = evidence.storage_key(a["tenant_id"], eid, "clip")
    try:
        await evidence.store().put(key, data, "video/mp4")
    except Exception:
        log.exception("could not store clip")
        raise ApiError(502, "storage_unavailable", "Could not store the clip. Try again.")
    await ctx.conn.execute("INSERT INTO app.evidence (id, tenant_id, camera_id, kind, storage_key, content_type, bytes, expires_at) VALUES ($1,$2,$3,'clip',$4,'video/mp4',$5,$6)",
                           eid, a["tenant_id"], a["camera_id"], key, len(data), datetime.now(timezone.utc) + timedelta(days=a["retention_snapshots_days"]))
    await ctx.conn.execute("UPDATE app.alerts SET clip_id=$2 WHERE id=$1", alert_id, eid)
    await ctx.conn.execute("UPDATE app.notifications SET next_attempt_at = now() WHERE alert_id=$1 AND status='queued' AND channel='whatsapp' AND next_attempt_at > now()", alert_id)
    return {"evidence_id": str(eid)}
