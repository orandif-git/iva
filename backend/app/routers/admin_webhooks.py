"""Webhooks for alerts. Owner only: the headers can hold a token and the events carry customers' alerts.

Every alert has at most one endpoint: a name, an address, optional headers, and a JSON payload with {shortcodes}. When the alert happens
the payload is sent once for each person who should be told, and the owner's own system turns it into the WhatsApp message.
The owner can turn an endpoint on or off, send a test, look at the delivery log, and send a failed event again."""
import json
import secrets as _secrets
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..config import get_settings
from ..crypto import vault
from ..deps import AdminCtx, admin_ctx
from ..errors import ApiError
from ..messaging import delivery, webhook
from ..schemas import Strict
from ..services import alert_catalog as cat

router = APIRouter(prefix="/admin/v1/webhooks", tags=["admin: webhooks"])
Reason = Field(None, min_length=5, max_length=500)


class EndpointIn(Strict):
    name: str = Field(min_length=2, max_length=80)
    alert_type: str
    url: str = Field(min_length=10, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    payload_template: str | None = Field(None, max_length=webhook.MAX_TEMPLATE)
    active: bool = True
    reason: str | None = Reason


class EndpointPatch(Strict):
    name: str | None = Field(None, min_length=2, max_length=80)
    url: str | None = Field(None, min_length=10, max_length=2000)
    headers: dict[str, str] | None = None
    payload_template: str | None = Field(None, max_length=webhook.MAX_TEMPLATE)
    active: bool | None = None
    reason: str | None = Reason


def _check(alert_type: str | None, url: str | None, headers: dict | None, template: str | None) -> dict | None:
    try:
        if url is not None:
            webhook.check_url(url, allow_private=get_settings().messaging_allow_private_hosts)
        clean = webhook.validate_headers(headers) if headers is not None else None
        if template is not None and alert_type:
            webhook.validate_template(alert_type, template)
    except ValueError as e:
        raise ApiError(422, "invalid_webhook", str(e))
    return clean


def _seal(ep_id, headers: dict) -> bytes | None:
    return vault().encrypt(json.dumps(headers), str(ep_id)) if headers else None


def _public(r, stats: dict | None = None) -> dict:
    meta = cat.CATALOG[r["alert_type"]]
    st = (stats or {}).get(r["id"], {})
    return {"id": r["id"], "name": r["name"], "alert_type": r["alert_type"], "event": cat.event_name(r["alert_type"]), "alert_title": meta["title"], "ai": meta["family"] == cat.SMART,
            "url": r["url"], "headers": delivery.endpoint_headers(r), "payload_template": r["payload_template"], "preview": webhook.preview(r["payload_template"]),
            "shortcodes": cat.shortcodes_for(r["alert_type"]), "active": r["active"], "updated_at": r["updated_at"],
            "deliveries": st.get("n", 0), "sent": st.get("sent", 0), "failed": st.get("failed", 0)}


async def _stats(ctx: AdminCtx) -> dict:
    rows = await ctx.conn.fetch("SELECT endpoint_id, count(*)::int AS n, count(*) FILTER (WHERE status='sent')::int AS sent, count(*) FILTER (WHERE status='failed')::int AS failed "
                                "FROM app.notifications WHERE endpoint_id IS NOT NULL GROUP BY endpoint_id")
    return {r["endpoint_id"]: dict(r) for r in rows}


async def _get(ctx: AdminCtx, eid: UUID):
    r = await ctx.conn.fetchrow("SELECT * FROM platform.webhook_endpoints WHERE id=$1", eid)
    if not r:
        raise ApiError(404, "webhook_not_found", "Webhook not found.")
    return r


async def _settings(ctx: AdminCtx) -> dict:
    r = await ctx.conn.fetchrow("SELECT signing_secret_ciphertext IS NOT NULL AS has_secret, link_hours FROM platform.webhook_settings WHERE id")
    return {"has_secret": bool(r and r["has_secret"]), "link_hours": r["link_hours"] if r else 4}


@router.get("")
async def list_endpoints(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    stats = await _stats(ctx)
    rows = await ctx.conn.fetch("SELECT * FROM platform.webhook_endpoints ORDER BY created_at")
    return {"items": [_public(r, stats) for r in rows], "settings": await _settings(ctx), "sample": webhook.SAMPLE}


@router.post("", status_code=201)
async def create_endpoint(body: EndpointIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    if body.alert_type not in cat.CATALOG:
        raise ApiError(404, "alert_not_found", "There is no alert with that name.")
    if not cat.CATALOG[body.alert_type]["built"]:
        raise ApiError(409, "alert_not_built", "This alert is not built yet, so it cannot send anything.")
    template = body.payload_template or webhook.default_template(body.alert_type)
    headers = _check(body.alert_type, body.url, body.headers, template)
    eid = await ctx.conn.fetchval("SELECT gen_random_uuid()")
    try:
        row = await ctx.conn.fetchrow(
            "INSERT INTO platform.webhook_endpoints (id, name, alert_type, url, headers_ciphertext, payload_template, active) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
            eid, body.name.strip(), body.alert_type, body.url, _seal(eid, headers), template, body.active)
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "webhook_exists", "This alert already has a webhook. Edit that one instead.")
    await ctx.audit("webhook.create", target_type="webhook", target_id=eid, reason=body.reason, after={"name": body.name, "alert": body.alert_type, "url": body.url, "active": body.active,
                                                                                                     "header_names": sorted(headers)})
    return _public(row)


class Standard(Strict):
    url: str = Field(min_length=10, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    reason: str | None = Reason


@router.post("/standard")
async def create_standard(body: Standard, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """One endpoint for every alert that is built and has none yet, all with the same address and headers and each with its standard payload.
    Alerts that already have an endpoint are left alone, so this can be run again after a new alert is built."""
    headers = _check(None, body.url, body.headers, None)
    have = {r["alert_type"] for r in await ctx.conn.fetch("SELECT alert_type FROM platform.webhook_endpoints")}
    made, skipped = [], []
    for k in cat.BUILT:
        if k in have:
            skipped.append(k)
            continue
        eid = await ctx.conn.fetchval("SELECT gen_random_uuid()")
        await ctx.conn.execute("INSERT INTO platform.webhook_endpoints (id, name, alert_type, url, headers_ciphertext, payload_template, active) VALUES ($1,$2,$3,$4,$5,$6,true)",
                               eid, cat.CATALOG[k]["title"], k, body.url, _seal(eid, headers), webhook.default_template(k))
        made.append(k)
    await ctx.audit("webhook.create_standard", target_type="webhook", reason=body.reason, after={"created": made, "skipped": skipped, "url": body.url, "header_names": sorted(headers)})
    return {"created": made, "skipped": skipped}


@router.get("/settings")
async def get_settings_(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    return await _settings(ctx)


class SettingsIn(Strict):
    link_hours: int = Field(ge=1, le=4)
    reason: str | None = Reason


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    await ctx.conn.execute("INSERT INTO platform.webhook_settings (id, link_hours) VALUES (true,$1) ON CONFLICT (id) DO UPDATE SET link_hours=$1", body.link_hours)
    await ctx.audit("webhook.settings", target_type="webhook", reason=body.reason, after={"link_hours": body.link_hours})
    return await _settings(ctx)


@router.post("/settings/secret")
async def new_secret(ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Makes a new signing secret and shows it once. The old one stops working at once, so copy the new one to the receiving system."""
    value = "whsec_" + _secrets.token_urlsafe(32)
    await ctx.conn.execute("INSERT INTO platform.webhook_settings (id, signing_secret_ciphertext) VALUES (true,$1) ON CONFLICT (id) DO UPDATE SET signing_secret_ciphertext=$1",
                           vault().encrypt(value, "webhook-signing"))
    await ctx.audit("webhook.secret", target_type="webhook")
    return {"secret": value}


# ------------------------------------------------------------------------------------------------ the log
@router.get("/deliveries")
async def deliveries(endpoint_id: UUID | None = None, status: str | None = Query(None, pattern="^(queued|sent|failed|suppressed|cancelled)$"),
                     limit: int = Query(50, ge=1, le=200), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    rows = await ctx.conn.fetch(
        """SELECT n.id, n.endpoint_id, w.name AS endpoint, n.event, n.status::text AS status, n.attempts, n.response_status, n.error, n.created_at, n.sent_at, n.recipient, t.name AS tenant
           FROM app.notifications n JOIN app.tenants t ON t.id = n.tenant_id LEFT JOIN platform.webhook_endpoints w ON w.id = n.endpoint_id
           WHERE n.channel='whatsapp' AND n.alert_id IS NOT NULL AND ($1::uuid IS NULL OR n.endpoint_id=$1) AND ($2::text IS NULL OR n.status::text=$2)
           ORDER BY n.created_at DESC LIMIT $3""", endpoint_id, status, limit)
    out = []
    for r in rows:
        d = dict(r)
        d["recipient"] = (d["recipient"] or "")[:3] + "•" * max(len(d["recipient"] or "") - 7, 0) + (d["recipient"] or "")[-4:]       # the number is never shown in full
        out.append(d)
    return {"items": out}


@router.post("/deliveries/{notification_id}/resend")
async def resend(notification_id: UUID, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """A failed event goes back in the queue with a fresh set of tries. It keeps its delivery id, so the receiver can tell it is the same event."""
    row = await ctx.conn.fetchrow(
        """UPDATE app.notifications SET status='queued', attempts=0, next_attempt_at=now(), locked_until=NULL, error=NULL, response_status=NULL
           WHERE id=$1 AND channel='whatsapp' AND alert_id IS NOT NULL AND status='failed' RETURNING id, tenant_id, event""", notification_id)
    if not row:
        raise ApiError(409, "not_failed", "Only an event that failed can be sent again.")
    await ctx.audit("webhook.resend", tenant_id=row["tenant_id"], target_type="notification", target_id=notification_id, after={"event": row["event"]})
    return {"queued": True}


# ------------------------------------------------------------------------------------------------ one endpoint
@router.put("/{endpoint_id}")
async def update_endpoint(endpoint_id: UUID, body: EndpointPatch, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, endpoint_id)
    headers = _check(cur["alert_type"], body.url, body.headers, body.payload_template)       # only what is being changed is checked
    template = body.payload_template if body.payload_template is not None else cur["payload_template"]
    sealed = _seal(endpoint_id, headers) if headers is not None else cur["headers_ciphertext"]
    active = cur["active"] if body.active is None else body.active
    row = await ctx.conn.fetchrow(
        "UPDATE platform.webhook_endpoints SET name=$2, url=$3, headers_ciphertext=$4, payload_template=$5, active=$6, updated_at=now() WHERE id=$1 RETURNING *",
        endpoint_id, body.name or cur["name"], body.url or cur["url"], sealed, template, active)
    action = "webhook.toggle" if set(body.model_fields_set) <= {"active", "reason"} else "webhook.update"
    await ctx.audit(action, target_type="webhook", target_id=endpoint_id, reason=body.reason, before={"active": cur["active"]},
                    after={"active": active, "changed": sorted(body.model_fields_set - {"reason"})})
    return _public(row)


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(endpoint_id: UUID, reason: str | None = Query(None, min_length=5, max_length=500), ctx: AdminCtx = Depends(admin_ctx("owner"))):
    cur = await _get(ctx, endpoint_id)
    await ctx.conn.execute("DELETE FROM platform.webhook_endpoints WHERE id=$1", endpoint_id)
    await ctx.audit("webhook.delete", target_type="webhook", target_id=endpoint_id, reason=reason, before={"name": cur["name"], "alert": cur["alert_type"]})


class SendNow(Strict):
    to_phone: str | None = Field(None, pattern=r"^\+?[0-9]{8,15}$")        # the number the test event is about; a sample number if left out


@router.post("/{endpoint_id}/send-now")
async def send_now(endpoint_id: UUID, body: SendNow | None = None, ctx: AdminCtx = Depends(admin_ctx("owner"))):
    """Sends one test event with sample values to the endpoint, marked X-Test: true. Nothing is recorded for any customer."""
    ep = await _get(ctx, endpoint_id)
    s = get_settings()
    values = {**webhook.SAMPLE, "event_type": cat.event_name(ep["alert_type"]), "title": cat.CATALOG[ep["alert_type"]]["title"]}
    if body and body.to_phone:
        values["to_phone"] = body.to_phone.lstrip("+")
    payload = webhook.render(ep["payload_template"], values).encode()
    cfg = await delivery.webhook_settings(ctx.conn)
    res = await webhook.send(ep["url"], delivery.endpoint_headers(ep), payload, event=cat.event_name(ep["alert_type"]), delivery_id="test-" + _secrets.token_hex(8), secret=cfg["secret"], test=True,
                             allow_private=s.messaging_allow_private_hosts, ssl_context=delivery.ssl_context())
    await ctx.audit("webhook.test", target_type="webhook", target_id=endpoint_id, after={"ok": res.ok, "status": res.status})
    return {"ok": res.ok, "status": res.status, "error": res.error, "sent": json.loads(payload) if payload else None}
