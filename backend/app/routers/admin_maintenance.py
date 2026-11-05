"""What the background jobs did, and a way to run the data clean-up now. Retention periods themselves are per tenant (PATCH /admin/v1/tenants/{id})."""
from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..services import retention

router = APIRouter(prefix="/admin/v1/maintenance", tags=["admin: maintenance"])


@router.get("/runs")
async def runs(job: str | None = Query(None, max_length=40), limit: int = Query(20, ge=1, le=100), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    rows = await ctx.conn.fetch("SELECT id, job, ran_at, seconds, ok, details FROM platform.job_runs WHERE ($1::text IS NULL OR job=$1) ORDER BY ran_at DESC, id DESC LIMIT $2", job, limit)
    return [dict(r) for r in rows]


@router.get("/retention/preview")
async def preview(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """How much the next clean-up would remove right now, and the shortest and longest periods in use."""
    q = lambda sql: ctx.conn.fetchval(sql)
    return {
        "footfall_rows": await q("SELECT count(*) FROM app.footfall_counts f JOIN app.tenants t ON t.id=f.tenant_id WHERE f.bucket_start < now() - make_interval(days => t.retention_events_days)"),
        "plate_reads": await q("SELECT count(*) FROM app.plate_events p JOIN app.tenants t ON t.id=p.tenant_id WHERE p.seen_at < now() - make_interval(days => t.retention_events_days)"),
        "alerts": await q("SELECT count(*) FROM app.alerts a JOIN app.tenants t ON t.id=a.tenant_id WHERE a.created_at < now() - make_interval(days => t.retention_events_days)"),
        "pictures_and_clips": await q("SELECT count(*) FROM app.evidence WHERE expires_at < now()"),
        "events_days": dict(await ctx.conn.fetchrow("SELECT min(retention_events_days) AS shortest, max(retention_events_days) AS longest FROM app.tenants")),
        "pictures_days": dict(await ctx.conn.fetchrow("SELECT min(retention_snapshots_days) AS shortest, max(retention_snapshots_days) AS longest FROM app.tenants")),
    }


@router.post("/retention/run")
async def run_now(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Runs the clean-up straight away instead of waiting for the six-hourly run. Deletion cannot be undone, so it is audited."""
    result = await retention.run_all()
    await ctx.audit("maintenance.retention.run", after={k: v for k, v in result.items()})
    return result
