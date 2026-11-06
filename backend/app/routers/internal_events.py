"""Event ingestion for the AI workers (service-to-service).

The tenant is never taken from the request: it comes from the camera row, so a worker cannot write into the wrong tenant.
A batch is processed event by event. A bad event is reported back by index and the rest are still stored."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from ..deps import ServiceCtx, service_ctx
from ..schemas import CLIP_AFTER, CLIP_BEFORE, ClipRequest, FootfallBatch, IngestResult, PlateBatch, Rejected
from ..services.alerts import raise_alert
from ..services.plate_events import alert_if_blocked, find_entry
from ..services.plates import clean_plate

router = APIRouter(prefix="/internal/v1/events", tags=["internal: events"])

MAX_AGE = timedelta(days=7)          # late data older than this is refused
MAX_FUTURE = timedelta(minutes=5)    # allowance for clock drift on a worker
DUP_WINDOW = timedelta(seconds=120)  # the same plate, camera and direction inside this window is one visit
_ENSURED: set = set()                # months whose partition this process has already ensured

_CAMS = """SELECT c.id, c.tenant_id, c.name, c.enabled, c.use_footfall, c.use_anpr, t.status::text AS tenant_status
           FROM app.cameras c JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id = ANY($1::uuid[])"""


def _why_rejected(cam, ts: datetime, now: datetime, flag: str) -> str | None:
    if cam is None:
        return "unknown_camera"
    if cam["tenant_status"] in ("suspended", "cancelled"):
        return "tenant_suspended"
    if not cam["enabled"] or not cam[flag]:
        return "footfall_not_enabled" if flag == "use_footfall" else "plates_not_enabled"
    if ts > now + MAX_FUTURE:
        return "in_future"
    if ts < now - MAX_AGE:
        return "too_old"
    return None


async def _touch(conn, latest: dict) -> None:
    for cam_id, ts in latest.items():
        await conn.execute("UPDATE app.cameras SET last_frame_at = greatest(coalesce(last_frame_at, '-infinity'), $2) WHERE id = $1", cam_id, ts)


@router.post("/footfall", response_model=IngestResult)
async def ingest_footfall(body: FootfallBatch, ctx: ServiceCtx = Depends(service_ctx)):
    cams = {r["id"]: r for r in await ctx.conn.fetch(_CAMS, list({e.camera_id for e in body.events}))}
    now = datetime.now(timezone.utc)
    rejected, rows, latest = [], [], {}
    for i, e in enumerate(body.events):
        cam = cams.get(e.camera_id)
        reason = _why_rejected(cam, e.bucket_start, now, "use_footfall")
        if reason is None and (e.bucket_start.second or e.bucket_start.microsecond):
            reason = "not_on_the_minute"
        if reason:
            rejected.append(Rejected(index=i, reason=reason))
            continue
        rows.append((cam["tenant_id"], e.camera_id, e.bucket_start, e.people_in, e.people_out))
        latest[e.camera_id] = max(latest.get(e.camera_id, e.bucket_start), e.bucket_start)
    if rows:
        # GREATEST makes a retried batch harmless: sending the same minute twice never double counts. A worker reports the
        # running total for the minute, so the larger figure is the later and more complete one.
        await ctx.conn.executemany(
            """INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (camera_id, bucket_start) DO UPDATE SET
                 people_in = GREATEST(app.footfall_counts.people_in, EXCLUDED.people_in),
                 people_out = GREATEST(app.footfall_counts.people_out, EXCLUDED.people_out)""", rows)
        await _touch(ctx.conn, latest)
    return IngestResult(accepted=len(rows), rejected=rejected)


@router.post("/plates", response_model=IngestResult)
async def ingest_plates(body: PlateBatch, ctx: ServiceCtx = Depends(service_ctx)):
    conn = ctx.conn
    cams = {r["id"]: r for r in await conn.fetch(_CAMS, list({e.camera_id for e in body.events}))}
    now = datetime.now(timezone.utc)
    accepted = dups = 0
    rejected, latest, clips = [], {}, []
    for i, e in enumerate(body.events):
        cam = cams.get(e.camera_id)
        reason = _why_rejected(cam, e.seen_at, now, "use_anpr")
        if reason:
            rejected.append(Rejected(index=i, reason=reason))
            continue
        month = e.seen_at.astimezone(timezone.utc).strftime("%Y-%m")
        if month not in _ENSURED:
            await conn.execute("SELECT app.ensure_plate_partition($1::date)", e.seen_at.astimezone(timezone.utc).date())
            _ENSURED.add(month)

        res = clean_plate(e.plate_raw)
        review = "needs_review" if res.needs_review(e.confidence) else "auto"
        tenant = cam["tenant_id"]
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(e.camera_id))     # one writer per camera at a time
            if e.track_id and await conn.fetchval(
                    "SELECT 1 FROM app.plate_events WHERE camera_id=$1 AND track_id=$2 AND seen_at BETWEEN $3 AND $4",
                    e.camera_id, e.track_id, e.seen_at - timedelta(days=1), e.seen_at + timedelta(days=1)):
                dups += 1
                continue
            if res.valid and await conn.fetchval(
                    """SELECT 1 FROM app.plate_events WHERE camera_id=$1 AND plate_text=$2 AND direction IS NOT DISTINCT FROM $3::app.direction
                       AND seen_at BETWEEN $4 AND $5""",
                    e.camera_id, res.text, e.direction, e.seen_at - DUP_WINDOW, e.seen_at + DUP_WINDOW):
                dups += 1
                continue

            entry_id = dwell = None
            if e.direction == "out" and review == "auto":
                entry = await find_entry(conn, tenant, res.text, e.seen_at)
                if entry:
                    entry_id, dwell = entry["id"], int((e.seen_at - entry["seen_at"]).total_seconds())

            picture = e.evidence_id if e.evidence_id and await conn.fetchval("SELECT 1 FROM app.evidence WHERE id=$1 AND tenant_id=$2", e.evidence_id, tenant) else None
            await conn.execute(
                """INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, track_id, plate_text, plate_raw, confidence, vehicle_type,
                                                 direction, snapshot_key, review_status, entry_event_id, dwell_seconds, evidence_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::app.direction,$10,$11::app.review_status,$12,$13,$14)""",
                tenant, e.camera_id, e.seen_at, e.track_id, res.text, e.plate_raw, e.confidence, e.vehicle_type, e.direction,
                e.snapshot_key, review, entry_id, dwell, picture)

            if review == "auto":
                alert_id = await alert_if_blocked(conn, tenant, res.text, e.camera_id, cam["name"], e.direction, e.snapshot_key, e.evidence_id, expect_clip=True)
                if alert_id and await conn.fetchval("SELECT clip_wait_until IS NOT NULL FROM app.alerts WHERE id=$1", alert_id):
                    clips.append(ClipRequest(index=i, alert_id=alert_id, seconds_before=CLIP_BEFORE, seconds_after=CLIP_AFTER))
            else:
                await raise_alert(conn, tenant, "low_confidence_plate", body=f"Read as {e.plate_raw} ({e.confidence}% sure) at {cam['name']}.", camera_id=e.camera_id,
                                  camera_name=cam["name"], dedupe_key=f"lowconf:{e.camera_id}", cooldown_minutes=15, evidence_id=e.evidence_id, evidence_key=e.snapshot_key)
            accepted += 1
            latest[e.camera_id] = max(latest.get(e.camera_id, e.seen_at), e.seen_at)
    await _touch(conn, latest)
    return IngestResult(accepted=accepted, duplicates=dups, rejected=rejected, clips=clips)
