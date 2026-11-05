from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..pagination import decode_cursor, encode_cursor
from ..schemas import FootfallPoint, ListEntryIn, ListEntryOut, PlateDetail, PlateOut, PlateReview
from ..services.plate_events import alert_if_blocked, find_entry
from ..services.plates import normalize, strict

router = APIRouter(prefix="/v1", tags=["customer: footfall and plates"])
DEFAULT_TZ = "Asia/Kolkata"


def _aware(v: datetime | None, name: str) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ApiError(422, "timezone_required", f"'{name}' must include a timezone, for example 2026-11-04T00:00:00+05:30.")
    return v


async def _tz(ctx: CustomerCtx, tz: str) -> str:
    if not await ctx.conn.fetchval("SELECT 1 FROM pg_timezone_names WHERE name = $1", tz):
        raise ApiError(422, "unknown_timezone", "Use a name like Asia/Kolkata.")
    return tz


# ------------------------------------------------------------------------ footfall
@router.get("/footfall")
async def footfall(
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
    interval: Literal["hour", "day"] = "hour", camera_id: UUID | None = None, tz: str = DEFAULT_TZ,
    ctx: CustomerCtx = Depends(customer_ctx()),
):
    """Entries and exits per hour or day, with empty buckets filled in as zero so charts have no gaps.
    Days are cut at midnight in `tz`, not UTC."""
    f, t = _aware(from_, "from"), _aware(to, "to")
    if t <= f:
        raise ApiError(422, "bad_range", "'to' must be after 'from'.")
    if t - f > (timedelta(days=31) if interval == "hour" else timedelta(days=366)):
        raise ApiError(422, "range_too_large", "Use at most 31 days for hourly figures and 366 days for daily ones.")
    await _tz(ctx, tz)
    rows = await ctx.conn.fetch(
        """WITH s AS (
             SELECT date_trunc($1::text, bucket_start AT TIME ZONE $2) AS t, sum(people_in)::int AS i, sum(people_out)::int AS o
             FROM app.footfall_counts
             WHERE bucket_start >= $3 AND bucket_start < $4 AND ($5::uuid IS NULL OR camera_id = $5)
             GROUP BY 1)
           SELECT g.t AT TIME ZONE $2 AS bucket, coalesce(s.i, 0) AS people_in, coalesce(s.o, 0) AS people_out
           FROM generate_series(date_trunc($1::text, $3::timestamptz AT TIME ZONE $2),
                                date_trunc($1::text, ($4::timestamptz - interval '1 microsecond') AT TIME ZONE $2),
                                $6::interval) AS g(t)
           LEFT JOIN s ON s.t = g.t ORDER BY g.t""",
        interval, tz, f, t, camera_id, timedelta(hours=1) if interval == "hour" else timedelta(days=1))
    return {"interval": interval, "tz": tz, "items": [FootfallPoint(**dict(r)) for r in rows]}


@router.get("/footfall/summary")
async def footfall_summary(
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
    camera_id: UUID | None = None, tz: str = DEFAULT_TZ, ctx: CustomerCtx = Depends(customer_ctx()),
):
    f, t = _aware(from_, "from"), _aware(to, "to")
    if t <= f or t - f > timedelta(days=366):
        raise ApiError(422, "bad_range", "Use a range of up to 366 days, with 'to' after 'from'.")
    await _tz(ctx, tz)
    where, args = "bucket_start >= $1 AND bucket_start < $2 AND ($3::uuid IS NULL OR camera_id = $3)", (f, t, camera_id)
    tot = await ctx.conn.fetchrow(f"SELECT coalesce(sum(people_in),0)::int AS i, coalesce(sum(people_out),0)::int AS o FROM app.footfall_counts WHERE {where}", *args)
    peak = await ctx.conn.fetchrow(
        f"""SELECT date_trunc('hour', bucket_start AT TIME ZONE $4) AT TIME ZONE $4 AS bucket, sum(people_in)::int AS people_in
            FROM app.footfall_counts WHERE {where} GROUP BY 1 HAVING sum(people_in) > 0 ORDER BY 2 DESC, 1 LIMIT 1""", *args, tz)
    per = await ctx.conn.fetch(
        f"""SELECT c.id AS camera_id, c.name, sum(f.people_in)::int AS people_in, sum(f.people_out)::int AS people_out
            FROM app.footfall_counts f JOIN app.cameras c ON c.id = f.camera_id
            WHERE f.bucket_start >= $1 AND f.bucket_start < $2 AND ($3::uuid IS NULL OR f.camera_id = $3) GROUP BY c.id, c.name ORDER BY c.name""", *args)
    return {"people_in": tot["i"], "people_out": tot["o"], "net": tot["i"] - tot["o"],
            "peak_hour": dict(peak) if peak else None, "cameras": [dict(r) for r in per]}


# -------------------------------------------------------------------------- plates
class PlatesPage(BaseModel):
    items: list[PlateOut]
    next_cursor: str | None


_PLATE_COLS = """p.id, p.camera_id, p.seen_at, p.plate_text, p.confidence::int AS confidence, p.vehicle_type, p.direction::text AS direction,
  p.review_status::text AS review_status, p.dwell_seconds, p.entry_event_id, (p.snapshot_key IS NOT NULL) AS has_snapshot,
  EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.plate_text = p.plate_text AND v.list_type = 'blocked') AS blocked"""


async def _search(ctx, from_, to, q, camera_id, direction, review_status, blocked, limit, cursor, default_days) -> PlatesPage:
    to = _aware(to, "to") or datetime.now(timezone.utc)
    f = _aware(from_, "from") or to - timedelta(days=default_days)
    if to <= f or to - f > timedelta(days=92):
        raise ApiError(422, "bad_range", "Use a range of up to 92 days, with 'to' after 'from'.")
    where, args = ["p.seen_at >= $1", "p.seen_at < $2"], [f, to]

    def add(sql, v):
        args.append(v)
        where.append(sql.replace("?", f"${len(args)}"))
    if q and normalize(q):
        add("p.plate_text LIKE ?", f"%{normalize(q)}%")
    if camera_id:
        add("p.camera_id = ?", camera_id)
    if direction:
        add("p.direction = ?::app.direction", direction)
    if review_status:
        add("p.review_status = ?::app.review_status", review_status)
    if blocked is not None:
        where.append(("" if blocked else "NOT ") + "EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.plate_text = p.plate_text AND v.list_type = 'blocked')")
    c = decode_cursor(cursor)
    if c:
        args.extend([datetime.fromisoformat(c["seen_at"]), int(c["id"])])
        where.append(f"(p.seen_at, p.id) < (${len(args) - 1}, ${len(args)})")
    args.append(limit + 1)
    rows = [dict(r) for r in await ctx.conn.fetch(
        f"SELECT {_PLATE_COLS} FROM app.plate_events p WHERE {' AND '.join(where)} ORDER BY p.seen_at DESC, p.id DESC LIMIT ${len(args)}", *args)]
    more = len(rows) > limit
    rows = rows[:limit]
    return PlatesPage(items=[PlateOut(**r) for r in rows],
                      next_cursor=encode_cursor({"seen_at": rows[-1]["seen_at"].isoformat(), "id": rows[-1]["id"]}) if more else None)


@router.get("/plates", response_model=PlatesPage)
async def list_plates(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None, q: str | None = Query(None, max_length=20),
    camera_id: UUID | None = None, direction: Literal["in", "out"] | None = None,
    review_status: Literal["auto", "needs_review", "confirmed", "corrected"] | None = None, blocked: bool | None = None,
    limit: int = Query(50, ge=1, le=200), cursor: str | None = None, ctx: CustomerCtx = Depends(customer_ctx()),
):
    return await _search(ctx, from_, to, q, camera_id, direction, review_status, blocked, limit, cursor, 1)


@router.get("/plates/review-queue", response_model=PlatesPage)
async def review_queue(
    from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
    limit: int = Query(50, ge=1, le=200), cursor: str | None = None, ctx: CustomerCtx = Depends(customer_ctx("admin")),
):
    return await _search(ctx, from_, to, None, None, None, "needs_review", None, limit, cursor, 30)


async def _plate(ctx: CustomerCtx, plate_id: int):
    row = await ctx.conn.fetchrow(f"SELECT {_PLATE_COLS}, p.plate_raw FROM app.plate_events p WHERE p.id = $1", plate_id)
    if not row:
        raise ApiError(404, "plate_not_found", "Plate read not found.")
    return dict(row)


@router.get("/plates/{plate_id}", response_model=PlateDetail)
async def plate_detail(plate_id: int, ctx: CustomerCtx = Depends(customer_ctx())):
    row = await _plate(ctx, plate_id)
    entry = exit_ = None
    if row["entry_event_id"]:
        entry = await ctx.conn.fetchrow(f"SELECT {_PLATE_COLS} FROM app.plate_events p WHERE p.id = $1", row["entry_event_id"])
    else:
        exit_ = await ctx.conn.fetchrow(f"SELECT {_PLATE_COLS} FROM app.plate_events p WHERE p.entry_event_id = $1", plate_id)
    return PlateDetail(**row, entry=PlateOut(**entry) if entry else None, exit=PlateOut(**exit_) if exit_ else None)


@router.patch("/plates/{plate_id}", response_model=PlateOut)
async def review_plate(plate_id: int, body: PlateReview, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    """Confirm a read as right, or correct it. A correction must be a complete, valid plate: nothing is guessed here."""
    row = await _plate(ctx, plate_id)
    old = row["plate_text"]
    if body.confirm:
        text, status = strict(old or ""), "confirmed"
        if not text:
            raise ApiError(422, "invalid_plate", "This read is not a valid plate, so it cannot be confirmed. Send the correct plate_text.")
    else:
        text, status = strict(body.plate_text), "corrected"
        if not text:
            raise ApiError(422, "invalid_plate", "That is not a valid Indian plate, for example GJ14AW1013.")
    entry_id, dwell = row["entry_event_id"], row["dwell_seconds"]
    if row["direction"] == "out" and entry_id is None:                     # a corrected exit may now match its entry
        entry = await find_entry(ctx.conn, ctx.tenant_id, text, row["seen_at"])
        if entry:
            entry_id, dwell = entry["id"], int((row["seen_at"] - entry["seen_at"]).total_seconds())
    await ctx.conn.execute(
        """UPDATE app.plate_events SET plate_text=$3, review_status=$4::app.review_status, reviewed_by=$5, reviewed_at=now(),
                  entry_event_id=$6, dwell_seconds=$7 WHERE id=$1 AND seen_at=$2""",
        plate_id, row["seen_at"], text, status, ctx.user_id, entry_id, dwell)
    cam = await ctx.conn.fetchrow("SELECT name FROM app.cameras WHERE id=$1", row["camera_id"])
    await alert_if_blocked(ctx.conn, ctx.tenant_id, text, row["camera_id"], cam["name"] if cam else "camera", row["direction"], None)
    await ctx.audit("plate.review", "plate_event", plate_id, {"from": old, "to": text, "status": status})
    return PlateOut(**await _plate(ctx, plate_id))


# ------------------------------------------------------------------ blocked / allowed
@router.get("/vehicle-lists", response_model=list[ListEntryOut])
async def get_lists(list_type: Literal["blocked", "allowed"] | None = None, ctx: CustomerCtx = Depends(customer_ctx())):
    rows = await ctx.conn.fetch("SELECT id, plate_text, list_type::text AS list_type, note, created_at FROM app.vehicle_lists "
                                "WHERE ($1::text IS NULL OR list_type::text = $1) ORDER BY created_at DESC", list_type)
    return [dict(r) for r in rows]


@router.post("/vehicle-lists", response_model=ListEntryOut, status_code=201)
async def add_to_list(body: ListEntryIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    plate = strict(body.plate_text)
    if not plate:
        raise ApiError(422, "invalid_plate", "That is not a valid Indian plate, for example GJ14AW1013.")
    try:
        async with ctx.conn.transaction():
            row = await ctx.conn.fetchrow(
                """INSERT INTO app.vehicle_lists (tenant_id, plate_text, list_type, note, created_by) VALUES ($1,$2,$3::app.list_type,$4,$5)
                   RETURNING id, plate_text, list_type::text AS list_type, note, created_at""", ctx.tenant_id, plate, body.list_type, body.note, ctx.user_id)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "already_listed", "This plate is already on that list.")
    await ctx.audit("vehicle_list.add", "vehicle_list", row["id"], {"plate": plate, "list": body.list_type})
    return dict(row)


@router.delete("/vehicle-lists/{entry_id}", status_code=204)
async def remove_from_list(entry_id: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    row = await ctx.conn.fetchrow("DELETE FROM app.vehicle_lists WHERE id=$1 RETURNING plate_text, list_type::text AS list_type", entry_id)
    if not row:
        raise ApiError(404, "entry_not_found", "Entry not found.")
    await ctx.audit("vehicle_list.remove", "vehicle_list", entry_id, {"plate": row["plate_text"], "list": row["list_type"]})
