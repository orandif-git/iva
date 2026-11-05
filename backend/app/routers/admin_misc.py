from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/admin/v1", tags=["admin"])


@router.get("/me")
async def me(ctx: AdminCtx = Depends(admin_ctx())):
    return {"id": ctx.staff.id, "name": ctx.staff.name, "role": ctx.staff.role}


@router.get("/overview")
async def overview(ctx: AdminCtx = Depends(admin_ctx())):
    c = ctx.conn
    tenants = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.tenants GROUP BY 1")}
    rec = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.recorders GROUP BY 1")}
    in_use = await c.fetchval("SELECT count(*) FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE c.enabled AND t.status <> 'suspended'")
    licensed = await c.fetchval("SELECT coalesce(sum(camera_limit),0) FROM app.tenants WHERE status <> 'suspended'")
    states = {r["stream_state"]: r["n"] for r in await c.fetch("SELECT stream_state, count(*) AS n FROM app.cameras WHERE enabled GROUP BY 1")}
    out = {"tenants": tenants, "recorders": rec, "cameras_in_use": in_use, "cameras_licensed": licensed, "cameras_by_state": states, "mrr_paise": None}
    if ctx.staff.role == "owner":                       # revenue is visible to the owner only
        out["mrr_paise"] = await c.fetchval(
            """SELECT coalesce(sum(coalesce(t.price_override_paise, p.price_per_camera_paise) * x.n),0)::bigint
               FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id
               CROSS JOIN LATERAL (SELECT count(*) AS n FROM app.cameras c WHERE c.tenant_id=t.id AND c.enabled) x
               WHERE t.status IN ('active','past_due')""")
    return out


@router.get("/audit")
async def audit_log(
    action: str | None = Query(None, max_length=60),
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
    q: str | None = Query(None, max_length=80),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = None,
    ctx: AdminCtx = Depends(admin_ctx("owner", "support")),
):
    where, args = [], []
    for col, val in (("action", action), ("tenant_id", tenant_id), ("actor_id", actor_id)):
        if val is not None:
            args.append(val); where.append(f"{col} = ${len(args)}")
    if q:
        args.append(f"%{q}%"); where.append(f"(actor_name ILIKE ${len(args)} OR action ILIKE ${len(args)} OR reason ILIKE ${len(args)})")
    if since:
        args.append(since); where.append(f"ts >= ${len(args)}")
    if until:
        args.append(until); where.append(f"ts < ${len(args)}")
    c = decode_cursor(cursor)
    if c:
        args.append(int(c["id"])); where.append(f"id < ${len(args)}")
    args.append(limit + 1)
    rows = [dict(r) for r in await ctx.conn.fetch(
        """SELECT id, ts, actor_type::text AS actor_type, actor_id, actor_name, actor_role, tenant_id, action, target_type, target_id, reason, host(ip) AS ip
           FROM platform.audit_log""" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY id DESC LIMIT ${len(args)}", *args)]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "next_cursor": encode_cursor({"id": rows[-1]["id"]}) if more else None}


# ------------------------------------------------------------------------------ the front page: what needs a person
def _since(ts) -> str:
    secs = max(0, int((datetime.now(timezone.utc) - ts).total_seconds())) if ts else None
    if secs is None:
        return "never"
    if secs < 3600:
        return f"{max(1, secs // 60)} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


@router.get("/overview/attention")
async def attention(ctx: AdminCtx = Depends(admin_ctx())):
    """Things a person should look at, most urgent first. Money items are for the owner only."""
    c, items = ctx.conn, []
    for r in await c.fetch(
            """SELECT r.id, r.device_id, r.name, t.name AS tenant, r.last_heartbeat_at FROM app.recorders r JOIN app.tenants t ON t.id=r.tenant_id
               WHERE r.status='offline' AND t.status NOT IN ('suspended','cancelled') ORDER BY r.last_heartbeat_at NULLS LAST LIMIT 20"""):
        items.append({"id": f"rec-off-{r['id']}", "severity": "critical", "kind": "recorder_offline", "title": f"{r['name'] or r['device_id']} offline for {_since(r['last_heartbeat_at'])}",
                      "subtitle": f"{r['tenant']} · {r['device_id']}", "age": _since(r["last_heartbeat_at"]), "target": {"type": "recorder", "id": str(r["id"])}})
    for r in await c.fetch(
            """SELECT r.id, r.device_id, r.name, t.name AS tenant, r.created_at FROM app.recorders r JOIN app.tenants t ON t.id=r.tenant_id
               WHERE r.status='never' AND r.created_at < now() - interval '24 hours' AND t.status NOT IN ('suspended','cancelled') ORDER BY r.created_at LIMIT 20"""):
        items.append({"id": f"rec-never-{r['id']}", "severity": "warning", "kind": "recorder_never_connected", "title": f"{r['name'] or r['device_id']} has never connected",
                      "subtitle": f"{r['tenant']} · created {_since(r['created_at'])} ago", "age": _since(r["created_at"]), "target": {"type": "recorder", "id": str(r["id"])}})
    if ctx.staff.role == "owner":
        for r in await c.fetch(
                """SELECT i.id, i.number, i.total_paise, i.due_at, t.id AS tenant_id, t.name AS tenant FROM app.invoices i JOIN app.tenants t ON t.id=i.tenant_id
                   WHERE i.status='issued' AND i.due_at < now() ORDER BY i.due_at LIMIT 20"""):
            items.append({"id": f"inv-{r['id']}", "severity": "warning", "kind": "invoice_overdue", "title": f"Invoice {_since(r['due_at'])} overdue · ₹{r['total_paise'] / 100:,.0f}",
                          "subtitle": f"{r['tenant']} · {r['number'] or 'draft'}", "age": _since(r["due_at"]), "target": {"type": "invoice", "id": str(r["id"]), "tenant_id": str(r["tenant_id"])}})
    for r in await c.fetch(
            """SELECT t.id, t.name, t.trial_ends_at, (SELECT count(*) FROM app.cameras c WHERE c.tenant_id=t.id) AS cams, t.camera_limit
               FROM app.tenants t WHERE t.status='trial' AND t.trial_ends_at BETWEEN now() AND now() + interval '3 days' ORDER BY t.trial_ends_at LIMIT 20"""):
        left = max(0, (r["trial_ends_at"] - datetime.now(timezone.utc)).days)
        items.append({"id": f"trial-{r['id']}", "severity": "info", "kind": "trial_ending", "title": f"Trial ends in {left} {'day' if left == 1 else 'days'}",
                      "subtitle": f"{r['name']} · {r['cams']} of {r['camera_limit']} cameras set up", "age": f"{left} d", "target": {"type": "customer", "id": str(r["id"])}})
    failed = await c.fetchrow("SELECT count(*) AS n, count(DISTINCT tenant_id) AS tenants FROM app.notifications WHERE status='failed' AND created_at >= date_trunc('day', now())")
    if failed["n"]:
        items.append({"id": "msg-failed-today", "severity": "warning", "kind": "messages_failed", "title": f"{failed['n']} message{'s' if failed['n'] != 1 else ''} failed today",
                      "subtitle": f"Across {failed['tenants']} customer{'s' if failed['tenants'] != 1 else ''}", "age": "today", "target": {"type": "messaging", "id": None}})
    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda x: order[x["severity"]])
    return {"items": items}
