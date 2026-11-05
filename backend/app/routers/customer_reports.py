"""Excel reports a customer can generate now or have emailed on a schedule."""
from datetime import date, datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from ..deps import CustomerCtx, customer_ctx
from ..errors import ApiError
from ..messaging import evidence
from ..schemas import Strict
from ..services.reports import TYPES, filename, latest_period, local_period

router = APIRouter(prefix="/v1", tags=["customer: reports"])
ReportType = Literal["gate_register", "footfall_summary", "vehicle_audit"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_WAITING = 5
MAX_PER_DAY = 20


class ReportIn(Strict):
    report_type: ReportType
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")


def _out(r, tz_name: str | None = None) -> dict:
    return {"id": r["id"], "report_type": r["report_type"], "title": TYPES[r["report_type"]][0], "period_start": r["period_start"], "period_end": r["period_end"],
            "status": r["status"], "scheduled": r["schedule_id"] is not None, "bytes": r["bytes"], "rows": r["row_count"], "truncated": r["truncated"],
            "error": r["error"], "created_at": r["created_at"], "generated_at": r["generated_at"], "expires_at": r["expires_at"]}


@router.get("/reports")
async def list_reports(limit: int = Query(50, ge=1, le=200), ctx: CustomerCtx = Depends(customer_ctx())):
    return [_out(r) for r in await ctx.conn.fetch("SELECT * FROM app.reports ORDER BY created_at DESC LIMIT $1", limit)]


@router.post("/reports", status_code=202)
async def generate(body: ReportIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    t = await ctx.conn.fetchrow("SELECT timezone, retention_events_days FROM app.tenants WHERE id=$1", ctx.tenant_id)
    label, longest = TYPES[body.report_type]
    if body.date_to < body.date_from:
        raise ApiError(422, "invalid_period", "The end date is before the start date.")
    if (body.date_to - body.date_from).days + 1 > longest:
        raise ApiError(422, "period_too_long", f"A {label.lower()} can cover at most {longest} days at a time.")
    if body.date_to > date.today():
        raise ApiError(422, "future_period", "Choose dates up to today.")
    if await ctx.conn.fetchval("SELECT count(*) FROM app.reports WHERE status IN ('queued','working')") >= MAX_WAITING:
        raise ApiError(429, "too_many_reports", "Some of your reports are still being prepared. Wait for them to finish.")
    if await ctx.conn.fetchval("SELECT count(*) FROM app.reports WHERE requested_by IS NOT NULL AND created_at > now() - interval '1 day'") >= MAX_PER_DAY:
        raise ApiError(429, "daily_limit", "You have reached today's limit for reports. Try again tomorrow.")
    a, b = local_period(t["timezone"], body.date_from, body.date_to)
    row = await ctx.conn.fetchrow(
        "INSERT INTO app.reports (tenant_id, report_type, period_start, period_end, requested_by, expires_at) VALUES ($1,$2,$3,$4,$5, now() + make_interval(days => $6)) RETURNING *",
        ctx.tenant_id, body.report_type, a, b, ctx.user_id, t["retention_events_days"])
    await ctx.audit("report.request", "report", row["id"], {"type": body.report_type, "from": str(body.date_from), "to": str(body.date_to)})
    return _out(row)


@router.get("/reports/{report_id}")
async def get_report(report_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    r = await ctx.conn.fetchrow("SELECT * FROM app.reports WHERE id=$1", report_id)
    if not r:
        raise ApiError(404, "report_not_found", "Report not found.")
    return _out(r)


@router.get("/reports/{report_id}/download")
async def download(report_id: UUID, ctx: CustomerCtx = Depends(customer_ctx())):
    r = await ctx.conn.fetchrow("SELECT r.*, t.timezone FROM app.reports r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.id=$1", report_id)
    if not r:
        raise ApiError(404, "report_not_found", "Report not found.")
    if r["status"] != "ready":
        raise ApiError(409, "not_ready", "This report is not ready yet." if r["status"] in ("queued", "working") else "This report could not be built.")
    data = await evidence.store().get(r["file_key"])
    if data is None:
        raise ApiError(410, "file_gone", "This report's file is no longer stored. Generate it again.")
    await ctx.audit("report.download", "report", report_id)
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename(r["report_type"], r["period_start"], r["period_end"], r["timezone"])}"',
                                                       "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------------------ schedules
class ScheduleIn(Strict):
    report_type: ReportType
    frequency: Literal["daily", "weekly", "monthly"]
    send_at: time = time(7, 0)
    weekday: int | None = Field(None, ge=0, le=6)
    day_of_month: int | None = Field(None, ge=1, le=28)
    recipient_ids: list[UUID] = Field(default_factory=list, max_length=20)
    enabled: bool = True


def _sch(r) -> dict:
    return {"id": r["id"], "report_type": r["report_type"], "title": TYPES[r["report_type"]][0], "frequency": r["frequency"], "send_at": r["send_at"].strftime("%H:%M"),
            "weekday": r["weekday"], "day_of_month": r["day_of_month"], "recipient_ids": list(r["recipients"]), "enabled": r["enabled"], "last_period_end": r["last_period_end"]}


async def _check_recipients(ctx: CustomerCtx, ids: list[UUID]) -> list[UUID]:
    ids = list(dict.fromkeys(ids))
    rows = await ctx.conn.fetch("SELECT id, name, email FROM app.recipients WHERE id = ANY($1::uuid[]) AND active", ids)
    if len(rows) != len(ids):
        raise ApiError(422, "recipient_not_found", "One of the people you chose was not found.")
    for r in rows:
        if not r["email"]:
            raise ApiError(422, "no_email", f"{r['name']} has no email address. Reports are sent by email.")
    return ids


def _fix(body: ScheduleIn) -> tuple:
    if body.frequency == "weekly" and body.weekday is None:
        raise ApiError(422, "weekday_needed", "Choose the day of the week.")
    if body.frequency == "monthly" and body.day_of_month is None:
        raise ApiError(422, "day_needed", "Choose the day of the month (1 to 28).")
    return (body.weekday if body.frequency == "weekly" else None), (body.day_of_month if body.frequency == "monthly" else None)


async def _start_from_now(ctx: CustomerCtx, row):
    """A new or changed schedule first sends at its next time, not at once for a period that has already passed."""
    tz = await ctx.conn.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", ctx.tenant_id)
    per = latest_period(row, datetime.now(timezone.utc), tz)
    return await ctx.conn.fetchrow("UPDATE app.report_schedules SET last_period_end=$2 WHERE id=$1 RETURNING *", row["id"], per[1])


@router.get("/report-schedules")
async def list_schedules(ctx: CustomerCtx = Depends(customer_ctx())):
    return [_sch(r) for r in await ctx.conn.fetch("SELECT * FROM app.report_schedules ORDER BY report_type, frequency")]


@router.post("/report-schedules", status_code=201)
async def add_schedule(body: ScheduleIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    wd, dom = _fix(body)
    ids = await _check_recipients(ctx, body.recipient_ids)
    if await ctx.conn.fetchval("SELECT 1 FROM app.report_schedules WHERE report_type=$1 AND frequency=$2", body.report_type, body.frequency):
        raise ApiError(409, "schedule_exists", "You already have this report on this schedule. Change that one instead.")
    row = await ctx.conn.fetchrow(
        "INSERT INTO app.report_schedules (tenant_id, report_type, frequency, send_at, weekday, day_of_month, recipients, enabled) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
        ctx.tenant_id, body.report_type, body.frequency, body.send_at, wd, dom, ids, body.enabled)
    row = await _start_from_now(ctx, row)
    await ctx.audit("report_schedule.create", "report_schedule", row["id"], {"type": body.report_type, "frequency": body.frequency, "recipients": len(ids)})
    return _sch(row)


@router.put("/report-schedules/{sid}")
async def change_schedule(sid: UUID, body: ScheduleIn, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    cur = await ctx.conn.fetchrow("SELECT * FROM app.report_schedules WHERE id=$1", sid)
    if not cur:
        raise ApiError(404, "schedule_not_found", "Schedule not found.")
    wd, dom = _fix(body)
    ids = await _check_recipients(ctx, body.recipient_ids)
    if (body.report_type, body.frequency) != (cur["report_type"], cur["frequency"]) and await ctx.conn.fetchval(
            "SELECT 1 FROM app.report_schedules WHERE report_type=$1 AND frequency=$2 AND id<>$3", body.report_type, body.frequency, sid):
        raise ApiError(409, "schedule_exists", "You already have this report on this schedule.")
    row = await ctx.conn.fetchrow(
        "UPDATE app.report_schedules SET report_type=$2, frequency=$3, send_at=$4, weekday=$5, day_of_month=$6, recipients=$7, enabled=$8 WHERE id=$1 RETURNING *",
        sid, body.report_type, body.frequency, body.send_at, wd, dom, ids, body.enabled)
    row = await _start_from_now(ctx, row)
    await ctx.audit("report_schedule.update", "report_schedule", sid, {"enabled": body.enabled, "recipients": len(ids)})
    return _sch(row)


@router.delete("/report-schedules/{sid}", status_code=204)
async def delete_schedule(sid: UUID, ctx: CustomerCtx = Depends(customer_ctx("admin"))):
    if not await ctx.conn.fetchval("DELETE FROM app.report_schedules WHERE id=$1 RETURNING id", sid):
        raise ApiError(404, "schedule_not_found", "Schedule not found.")
    await ctx.audit("report_schedule.delete", "report_schedule", sid)
