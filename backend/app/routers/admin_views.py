"""People count and Vehicles, for staff, one customer at a time (Control Center). Read only.

What is shown is what the customer's cameras saved: counts per minute at the counting line, and plate reads with their picture. The pages follow the days set under
Setup, Data kept, because older data is removed. Excel files use the same builders as the scheduled reports."""
import asyncio
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response

from ..deps import CustomerCtx
from ..errors import ApiError
from ..messaging import evidence
from ..services import report_builders as rb
from ..services.detectors import people_inside
from ..services.plates import normalize
from ..services.reports import local_period
from .admin_shop import HELPERS, shop_ctx

router = APIRouter(prefix="/admin/v1/tenants/{tenant_id}/shop", tags=["admin: people and vehicles"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_DAYS = 92


async def _tz(ctx: CustomerCtx) -> str:
    return await ctx.conn.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", ctx.tenant_id)


def _range(tz: str, d_from: date, d_to: date) -> tuple[datetime, datetime]:
    if d_to < d_from or (d_to - d_from).days >= MAX_DAYS:
        raise ApiError(422, "bad_range", f"Choose a range of up to {MAX_DAYS} days, with the end after the start.")
    return local_period(tz, d_from, d_to)


@router.get("/views")
async def views(ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """Which of the two pages this customer has: the matching event is switched on, or there is saved history to look at."""
    r = await ctx.conn.fetchrow(
        """SELECT EXISTS (SELECT 1 FROM app.cameras WHERE use_footfall) AS people_on, EXISTS (SELECT 1 FROM app.footfall_counts) AS people_history,
                  EXISTS (SELECT 1 FROM app.cameras WHERE use_anpr) AS plates_on, EXISTS (SELECT 1 FROM app.plate_events) AS plates_history""")
    return {"people": {"on": r["people_on"], "history": r["people_history"], "show": r["people_on"] or r["people_history"]},
            "vehicles": {"on": r["plates_on"], "history": r["plates_history"], "show": r["plates_on"] or r["plates_history"]}}


# --------------------------------------------------------------------------------------------------------------------- people count
@router.get("/people-count")
async def people_count(d_from: date = Query(..., alias="from"), d_to: date = Query(..., alias="to"), camera_id: UUID | None = None, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """Totals, the busiest hour or day, people per hour (one day) or per day (more), and the numbers for each camera. Days are the shop's own days."""
    tz = await _tz(ctx)
    a, b = _range(tz, d_from, d_to)
    hourly = d_from == d_to
    rows = await ctx.conn.fetch(
        """SELECT date_trunc($4::text, bucket_start AT TIME ZONE $3::text) AS slot, sum(people_in)::int AS i, sum(people_out)::int AS o
           FROM app.footfall_counts WHERE bucket_start >= $1 AND bucket_start < $2 AND ($5::uuid IS NULL OR camera_id = $5) GROUP BY 1""", a, b, tz, "hour" if hourly else "day", camera_id)
    got = {r["slot"]: (r["i"], r["o"]) for r in rows}
    series = []
    if hourly:
        base = datetime.combine(d_from, datetime.min.time())
        for h in range(24):
            s = base + timedelta(hours=h)
            series.append({"label": h, "in": got.get(s, (0, 0))[0], "out": got.get(s, (0, 0))[1]})
    else:
        d = d_from
        while d <= d_to:
            s = datetime.combine(d, datetime.min.time())
            series.append({"label": d.isoformat(), "in": got.get(s, (0, 0))[0], "out": got.get(s, (0, 0))[1]})
            d += timedelta(days=1)
    tin, tout = sum(x["in"] for x in series), sum(x["out"] for x in series)
    peak = max((x for x in series if x["in"] > 0), key=lambda x: x["in"], default=None)
    cams = await ctx.conn.fetch(
        """SELECT c.id, c.name, coalesce(sum(f.people_in),0)::int AS i, coalesce(sum(f.people_out),0)::int AS o,
                  max(f.bucket_start) FILTER (WHERE f.people_in + f.people_out > 0) AS last_at
           FROM app.cameras c LEFT JOIN app.footfall_counts f ON f.camera_id = c.id AND f.bucket_start >= $1 AND f.bucket_start < $2
           WHERE (c.use_footfall OR f.camera_id IS NOT NULL) AND ($3::uuid IS NULL OR c.id = $3) GROUP BY c.id, c.name ORDER BY c.name""", a, b, camera_id)
    today = datetime.now(ZoneInfo(tz)).date()
    inside = None
    if d_from <= today <= d_to:                  # an estimate from today's counts, so it is only shown when today is in the range
        day_a, day_b = local_period(tz, today, today)
        buckets = await ctx.conn.fetch(
            """SELECT bucket_start, sum(people_in)::int AS people_in, sum(people_out)::int AS people_out FROM app.footfall_counts
               WHERE bucket_start >= $1 AND bucket_start < $2 AND ($3::uuid IS NULL OR camera_id = $3) GROUP BY 1 ORDER BY 1""", day_a, day_b, camera_id)
        inside = people_inside(buckets)
    return {"hourly": hourly, "tz": tz, "people_in": tin, "people_out": tout, "busiest": peak, "inside_now": inside, "series": series,
            "cameras": [{"id": str(c["id"]), "name": c["name"], "in": c["i"], "out": c["o"], "last_at": c["last_at"]} for c in cams]}


@router.get("/people-count/export")
async def people_count_export(d_from: date = Query(..., alias="from"), d_to: date = Query(..., alias="to"), ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    tz = await _tz(ctx)
    a, b = _range(tz, d_from, d_to)
    rows = await ctx.conn.fetch(
        """SELECT (f.bucket_start AT TIME ZONE $3)::date AS day, extract(hour FROM f.bucket_start AT TIME ZONE $3)::int AS hour, coalesce(c.name, 'Removed camera') AS camera,
                  sum(f.people_in)::int AS people_in, sum(f.people_out)::int AS people_out
           FROM app.footfall_counts f LEFT JOIN app.cameras c ON c.id = f.camera_id
           WHERE f.bucket_start >= $1 AND f.bucket_start < $2 GROUP BY 1,2,3""", a, b, tz)
    data = await asyncio.to_thread(rb.footfall_summary, rows, tz, (d_to - d_from).days + 1)
    name = f"people_count_{d_from:%d-%b-%Y}" + (f"_to_{d_to:%d-%b-%Y}" if d_to != d_from else "") + ".xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# --------------------------------------------------------------------------------------------------------------------- vehicles
def _filters(a, b, q, direction, status, camera_id):
    where, args = ["p.seen_at >= $1", "p.seen_at < $2"], [a, b]

    def add(sql, v):
        args.append(v)
        where.append(sql.replace("?", f"${len(args)}"))
    if q and normalize(q):
        add("(p.plate_text LIKE ? OR upper(regexp_replace(coalesce(p.plate_raw, ''), '[^A-Za-z0-9]', '', 'g')) LIKE ?)", f"%{normalize(q)}%")
    if direction:
        add("p.direction = ?::app.direction", direction)
    if camera_id:
        add("p.camera_id = ?", camera_id)
    if status == "blocked":
        where.append("EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.plate_text = p.plate_text AND v.list_type = 'blocked')")
    elif status == "review":
        where.append("p.review_status = 'needs_review'")
    return " AND ".join(where), args


@router.get("/vehicles")
async def vehicles(d_from: date = Query(..., alias="from"), d_to: date = Query(..., alias="to"), q: str | None = Query(None, max_length=20),
                   direction: Literal["in", "out"] | None = None, status: Literal["blocked", "review"] | None = None, camera_id: UUID | None = None,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000), ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    """Plate reads, newest first, with the picture and, for a vehicle that came in, how long it stayed once an exit was read at any of the shop's plate cameras."""
    tz = await _tz(ctx)
    a, b = _range(tz, d_from, d_to)
    where, args = _filters(a, b, q, direction, status, camera_id)
    total = await ctx.conn.fetchval(f"SELECT count(*) FROM app.plate_events p WHERE {where}", *args)
    args.extend([limit, offset])
    rows = await ctx.conn.fetch(
        f"""SELECT p.id, p.seen_at, p.plate_text, p.plate_raw, p.confidence::int AS confidence, p.direction::text AS direction, p.review_status::text AS review_status,
                   p.camera_id, c.name AS camera, p.evidence_id, p.dwell_seconds, p.entry_event_id,
                   EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.plate_text = p.plate_text AND v.list_type = 'blocked') AS blocked,
                   (SELECT o.seen_at FROM app.plate_events o WHERE o.entry_event_id = p.id LIMIT 1) AS left_at
            FROM app.plate_events p LEFT JOIN app.cameras c ON c.id = p.camera_id WHERE {where} ORDER BY p.seen_at DESC, p.id DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}""", *args)
    live = {r["evidence_id"] for r in await ctx.conn.fetch("SELECT id AS evidence_id FROM app.evidence WHERE id = ANY($1::uuid[]) AND expires_at > now()", [r["evidence_id"] for r in rows if r["evidence_id"]])} if rows else set()
    items = []
    for r in rows:
        stay = None
        if r["direction"] == "in" and r["left_at"]:
            stay = int((r["left_at"] - r["seen_at"]).total_seconds() // 60)
        items.append({"id": r["id"], "seen_at": r["seen_at"], "plate": r["plate_text"] or r["plate_raw"] or "", "camera": r["camera"] or "Removed camera", "direction": r["direction"],
                      "confidence": r["confidence"], "needs_review": r["review_status"] == "needs_review", "blocked": r["blocked"], "stay_minutes": stay,
                      "inside": r["direction"] == "in" and r["left_at"] is None,
                      "picture": evidence.image_url(evidence.link_for(r["evidence_id"], ttl_hours=1)[0]) if r["evidence_id"] in live else None})
    return {"tz": tz, "total": total, "items": items}


@router.get("/vehicles/export")
async def vehicles_export(d_from: date = Query(..., alias="from"), d_to: date = Query(..., alias="to"), q: str | None = Query(None, max_length=20),
                          direction: Literal["in", "out"] | None = None, status: Literal["blocked", "review"] | None = None, ctx: CustomerCtx = Depends(shop_ctx(*HELPERS))):
    tz = await _tz(ctx)
    a, b = _range(tz, d_from, d_to)
    where, args = _filters(a, b, q, direction, status, None)
    args.append(rb.MAX_ROWS + 1)
    rows = await ctx.conn.fetch(
        f"""SELECT p.seen_at, p.plate_text, p.plate_raw, p.vehicle_type, c.name AS gate, p.direction::text AS direction, p.confidence, p.review_status::text AS review_status, p.dwell_seconds,
                   (SELECT v.list_type::text FROM app.vehicle_lists v WHERE v.tenant_id=p.tenant_id AND v.plate_text=p.plate_text ORDER BY v.list_type = 'blocked' DESC LIMIT 1) AS list_type
            FROM app.plate_events p LEFT JOIN app.cameras c ON c.id = p.camera_id WHERE {where} ORDER BY p.seen_at LIMIT ${len(args)}""", *args)
    cut = len(rows) > rb.MAX_ROWS
    data = await asyncio.to_thread(rb.gate_register, rows[:rb.MAX_ROWS], tz, cut)
    name = f"vehicles_{d_from:%d-%b-%Y}" + (f"_to_{d_to:%d-%b-%Y}" if d_to != d_from else "") + ".xlsx"
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
