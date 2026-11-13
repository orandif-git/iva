"""What the background jobs did, and a way to run the data clean-up now. Retention periods themselves are per tenant (PATCH /admin/v1/tenants/{id})."""
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..deps import AdminCtx, admin_ctx
from ..schemas import Strict
from ..services import retention

router = APIRouter(prefix="/admin/v1/maintenance", tags=["admin: maintenance"])


@router.post("/retention/run")
async def run_now(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Runs the clean-up straight away instead of waiting for the six-hourly run. Deletion cannot be undone, so it is audited."""
    result = await retention.run_all()
    await ctx.audit("maintenance.retention.run", after={k: v for k, v in result.items()})
    return result


# ------------------------------------------------------------------------------------------------ how long data is kept (one setting for every customer)
class KeepIn(Strict):
    days: int = Field(ge=7, le=730)


async def _counts(ctx: AdminCtx, days: int) -> dict:
    """What a clean-up with this many days would remove right now."""
    q = lambda sql: ctx.conn.fetchval(sql, days)
    return {
        "alerts": await q("SELECT count(*) FROM app.alerts a WHERE a.created_at < now() - make_interval(days => $1) AND NOT EXISTS (SELECT 1 FROM app.notifications n WHERE n.alert_id=a.id AND n.status='queued')"),
        "messages": await q("SELECT count(*) FROM app.notifications n WHERE n.created_at < now() - make_interval(days => $1) AND n.status <> 'queued'"),
        "people_counts": await q("SELECT count(*) FROM app.footfall_counts WHERE bucket_start < now() - make_interval(days => $1)"),
        "plate_reads": await q("SELECT count(*) FROM app.plate_events WHERE seen_at < now() - make_interval(days => $1)"),
        "pictures_and_clips": await q("SELECT count(*) FROM app.evidence WHERE created_at < now() - make_interval(days => $1)"),
    }


@router.get("/keep")
async def get_keep(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    r = await ctx.conn.fetchrow("SELECT keep_days, updated_at, updated_by FROM platform.data_retention WHERE id")
    last = await ctx.conn.fetchrow("SELECT ran_at, ok, details FROM platform.job_runs WHERE job='retention' ORDER BY ran_at DESC, id DESC LIMIT 1")
    return {"days": r["keep_days"], "updated_at": r["updated_at"], "updated_by": r["updated_by"], "last_run": dict(last) if last else None}


@router.get("/keep/preview")
async def keep_preview(days: int = Query(30, ge=7, le=730), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    return await _counts(ctx, days)


@router.put("/keep")
async def set_keep(body: KeepIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Keep everything for this many days, for every customer: alerts and their messages, people counts, plate reads, pictures and clips.
    The setting is applied to every customer's own retention (what the clean-up already reads) and to the date the stored pictures expire."""
    before = await ctx.conn.fetchval("SELECT keep_days FROM platform.data_retention WHERE id")
    await ctx.conn.execute("""INSERT INTO platform.data_retention (id, keep_days, updated_at, updated_by) VALUES (true,$1,now(),$2)
                              ON CONFLICT (id) DO UPDATE SET keep_days=$1, updated_at=now(), updated_by=$2""", body.days, ctx.staff.name)
    await ctx.conn.execute("UPDATE app.tenants SET retention_events_days=$1, retention_snapshots_days=$1", body.days)
    await ctx.conn.execute("UPDATE app.evidence SET expires_at = created_at + make_interval(days => $1)", body.days)
    await ctx.audit("maintenance.keep.set", before={"days": before}, after={"days": body.days})
    return await get_keep(ctx)
