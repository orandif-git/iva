"""Staff set up and look after the Hikvision alert links of a customer (see services/hik_alerts.py)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field

from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..schemas import Strict
from ..services import hik_alerts

router = APIRouter(prefix="/admin/v1", tags=["admin: hikvision alerts"])
ROLES = ("owner", "onboarding")


class LinkIn(Strict):
    name: str = Field(min_length=1, max_length=80)


def _out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "last_message_at": r["last_message_at"], "created_at": r["created_at"], "messages_today": r["messages_today"]}


def _secret(r, code: str) -> dict:
    return {**_out(r), "code": code, "address": hik_alerts.address(code), "command": hik_alerts.setup_command(code)}


COLS = """l.id, l.name, l.last_message_at, l.created_at,
          (SELECT count(*) FROM platform.alert_link_messages m WHERE m.link_id = l.id AND m.received_at > now() - interval '1 day')::int AS messages_today"""


@router.get("/tenants/{tenant_id}/alert-links")
async def list_links(tenant_id: UUID, ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch(f"SELECT {COLS} FROM platform.alert_links l WHERE l.tenant_id=$1 ORDER BY l.created_at", tenant_id)
    return [_out(r) for r in rows]


@router.post("/tenants/{tenant_id}/alert-links", status_code=201)
async def add_link(tenant_id: UUID, body: LinkIn, response: Response, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """The answer carries the secret code and the command that uses it. It is shown once: only a fingerprint of the code is kept."""
    if not await ctx.conn.fetchval("SELECT 1 FROM app.tenants WHERE id=$1", tenant_id):
        raise ApiError(404, "not_found", "No such customer.")
    code = hik_alerts.new_code()
    link_id = await ctx.conn.fetchval("INSERT INTO platform.alert_links (tenant_id, name, code_hash, created_by) VALUES ($1,$2,$3,$4) RETURNING id", tenant_id, body.name.strip(),
                                      hik_alerts.fingerprint(code), ctx.staff.id)
    await ctx.audit("alert_link.create", tenant_id=tenant_id, target_type="alert_link", target_id=link_id, after={"name": body.name.strip()})
    response.headers["Cache-Control"] = "no-store"
    return _secret(await ctx.conn.fetchrow(f"SELECT {COLS} FROM platform.alert_links l WHERE l.id=$1", link_id), code)


@router.post("/alert-links/{link_id}/new-code")
async def new_code(link_id: UUID, response: Response, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    """Replaces the secret code (for example if it leaked). The recorder must be given the new command; the old address stops working at once."""
    code = hik_alerts.new_code()
    tenant_id = await ctx.conn.fetchval("UPDATE platform.alert_links SET code_hash=$2 WHERE id=$1 RETURNING tenant_id", link_id, hik_alerts.fingerprint(code))
    if tenant_id is None:
        raise ApiError(404, "not_found", "No such link.")
    await ctx.audit("alert_link.new_code", tenant_id=tenant_id, target_type="alert_link", target_id=link_id)
    response.headers["Cache-Control"] = "no-store"
    return _secret(await ctx.conn.fetchrow(f"SELECT {COLS} FROM platform.alert_links l WHERE l.id=$1", link_id), code)


@router.delete("/alert-links/{link_id}", status_code=204)
async def delete_link(link_id: UUID, ctx: AdminCtx = Depends(admin_ctx(*ROLES))):
    row = await ctx.conn.fetchrow("DELETE FROM platform.alert_links WHERE id=$1 RETURNING tenant_id, name", link_id)
    if not row:
        raise ApiError(404, "not_found", "No such link.")
    await ctx.audit("alert_link.delete", tenant_id=row["tenant_id"], target_type="alert_link", target_id=link_id, after={"name": row["name"]})


@router.get("/tenants/{tenant_id}/alert-links/messages")
async def messages(tenant_id: UUID, limit: int = Query(100, ge=1, le=500), ctx: AdminCtx = Depends(admin_ctx())):
    rows = await ctx.conn.fetch(
        """SELECT m.id, l.name AS link, m.received_at, m.event_type, m.state, m.channel, m.event_time, m.result
           FROM platform.alert_link_messages m JOIN platform.alert_links l ON l.id = m.link_id WHERE l.tenant_id=$1 ORDER BY m.received_at DESC, m.id DESC LIMIT $2""", tenant_id, limit)
    return [dict(r) for r in rows]
