from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ..deps import AdminCtx, admin_ctx
from ..pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/admin/v1", tags=["admin"])


@router.get("/me")
async def me(ctx: AdminCtx = Depends(admin_ctx())):
    return {"id": ctx.staff.id, "name": ctx.staff.name, "role": ctx.staff.role}


@router.get("/plans")
async def plans(ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch("SELECT id, name, price_per_camera_paise, stream_mode, features FROM platform.plans WHERE active ORDER BY price_per_camera_paise")
    return {"items": [dict(r) for r in rows]}


@router.get("/overview")
async def overview(ctx: AdminCtx = Depends(admin_ctx())):
    c = ctx.conn
    tenants = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.tenants GROUP BY 1")}
    rec = {r["status"]: r["n"] for r in await c.fetch("SELECT status::text AS status, count(*) AS n FROM app.recorders GROUP BY 1")}
    in_use = await c.fetchval("SELECT count(*) FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE c.enabled AND t.status <> 'suspended'")
    licensed = await c.fetchval("SELECT coalesce(sum(camera_limit),0) FROM app.tenants WHERE status <> 'suspended'")
    out = {"tenants": tenants, "recorders": rec, "cameras_in_use": in_use, "cameras_licensed": licensed, "mrr_paise": None}
    if ctx.staff.role in ("owner", "finance"):          # revenue is visible to owner and finance only
        out["mrr_paise"] = await c.fetchval(
            """SELECT coalesce(sum(p.price_per_camera_paise * x.n),0)::bigint
               FROM app.tenants t JOIN platform.plans p ON p.id=t.plan_id
               CROSS JOIN LATERAL (SELECT count(*) AS n FROM app.cameras c WHERE c.tenant_id=t.id AND c.enabled) x
               WHERE t.status IN ('active','past_due')""")
    return out


@router.get("/audit")
async def audit_log(
    action: str | None = Query(None, max_length=60),
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = None,
    ctx: AdminCtx = Depends(admin_ctx("owner", "support")),
):
    where, args = [], []
    for col, val in (("action", action), ("tenant_id", tenant_id), ("actor_id", actor_id)):
        if val is not None:
            args.append(val); where.append(f"{col} = ${len(args)}")
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
