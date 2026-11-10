"""The delivery service: takes queued messages from the outbox, sends them, and records what happened.

An alert reaches a person as a webhook event (see webhook.py); the platform owner's system turns the event into the WhatsApp message.
A scheduled report is the one thing still sent as an email, through the platform's SMTP server.

At-least-once: a message is claimed (locked for a few minutes), sent, then marked sent. If the process dies in between, the lock lapses and
another try is made, so in rare cases an event could be sent twice, never zero. The notification id travels as X-Delivery-Id, the same on every
try, so the receiver can drop a duplicate. A failed event is tried again for up to about two hours."""
import asyncio
import json
import logging

from ..config import get_settings
from ..crypto import vault
from ..db import db
from . import evidence, smtp_provider, webhook
from ..services import alert_catalog as cat

log = logging.getLogger("gateinsight.delivery")

MAX_ATTEMPTS = 6
BACKOFF_SECONDS = [30, 120, 600, 1800, 3600]          # between tries: about an hour and three quarters in all, then the message is marked failed
LOCK_SECONDS = 180
EVENTS_PER_SECOND = 10

_ssl_context = None          # tests inject a context that trusts their local server


def set_ssl_context(ctx) -> None:
    global _ssl_context
    _ssl_context = ctx


def ssl_context():
    return _ssl_context


async def load_provider(conn, channel: str):
    """The active SMTP server, for scheduled reports."""
    row = await conn.fetchrow("SELECT * FROM platform.messaging_providers WHERE channel=$1::app.channel_kind AND active", channel)
    if not row:
        return None
    secrets = json.loads(vault().decrypt(bytes(row["secrets_ciphertext"]), str(row["id"]))) if row["secrets_ciphertext"] else {}
    return {"id": row["id"], "kind": row["kind"], "config": row["config"], "secrets": secrets, "rate": float(row["rate_per_second"])}


async def webhook_settings(conn) -> dict:
    r = await conn.fetchrow("SELECT signing_secret_ciphertext, link_hours FROM platform.webhook_settings WHERE id")
    secret = vault().decrypt(bytes(r["signing_secret_ciphertext"]), "webhook-signing") if r and r["signing_secret_ciphertext"] else None
    return {"secret": secret, "link_hours": min(4, r["link_hours"]) if r else 4}


def endpoint_headers(ep) -> dict[str, str]:
    return json.loads(vault().decrypt(bytes(ep["headers_ciphertext"]), str(ep["id"]))) if ep["headers_ciphertext"] else {}


async def claim(n: int):
    async with db.service_tx() as conn:
        return await conn.fetch(
            """UPDATE app.notifications SET locked_until = now() + make_interval(secs => $2), attempts = attempts + 1
               WHERE id IN (SELECT id FROM app.notifications WHERE status='queued' AND next_attempt_at <= now()
                              AND (locked_until IS NULL OR locked_until < now()) ORDER BY next_attempt_at LIMIT $1 FOR UPDATE SKIP LOCKED)
               RETURNING *""", n, float(LOCK_SECONDS))


async def _context(conn, n):
    return await conn.fetchrow(
        """SELECT a.id AS alert_id, a.rule_type, a.title, a.body, a.details, a.evidence_id, a.clip_id, ev.kind AS ev_kind, r.name AS r_name, r.active AS r_active,
                  r.phone AS r_phone, r.email AS r_email, t.name AS tenant_name
           FROM app.notifications n JOIN app.tenants t ON t.id = n.tenant_id
                LEFT JOIN app.alerts a ON a.id = n.alert_id LEFT JOIN app.evidence ev ON ev.id = a.evidence_id
                LEFT JOIN app.recipients r ON r.id = n.recipient_id
           WHERE n.id = $1""", n["id"])


async def build_report_email(conn, n, c):
    """A scheduled report by email, with the Excel file attached (or only mentioned when it is too big to attach)."""
    from ..services.reports import TYPES, filename
    r = await conn.fetchrow("SELECT r.*, t.timezone FROM app.reports r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.id=$1", n["report_id"])
    if not r or r["status"] != "ready":
        return "The report is not ready yet."
    data = await evidence.store().get(r["file_key"])
    if data is None:
        return "The report file is no longer stored."
    fname = filename(r["report_type"], r["period_start"], r["period_end"], r["timezone"])
    label = TYPES[r["report_type"]][0]
    big = len(data) > 8_000_000
    text = (f"Hello {c['r_name'] or ''},\n\nYour {label.lower()} is " + ("ready. It is too large to attach, so please open it in the Jas iVision app under Reports." if big else "attached.") +
            f"\n\nFile: {fname}\n\nJas iVision")
    return {"to": n["recipient"], "name": c["r_name"], "subject": f"[Jas iVision] {label}: {fname[:-5]}", "text": text, "html": None,
            "attachments": [] if big else [(fname, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]}


def backoff(attempts: int) -> int:
    return BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]


def event_body(ep, c, n, link_hours: int, *, test: bool = False) -> tuple[bytes, str]:
    """The payload for one person: the endpoint's template with every shortcode filled in. Picture and clip links are made now, so a retry gets fresh ones."""
    image = clip = ""
    if c["evidence_id"] and c["ev_kind"] == "image":
        image = evidence.image_url(evidence.link_for(c["evidence_id"], ttl_hours=link_hours)[0])
    if c["clip_id"]:
        clip = evidence.clip_url(evidence.link_for(c["clip_id"], ttl_hours=link_hours)[0])
    details = c["details"] if isinstance(c["details"], dict) else json.loads(c["details"] or "{}")
    event = cat.event_name(c["rule_type"])
    values = webhook.values_for(tenant_name=c["tenant_name"], alert_id=c["alert_id"], title=c["title"], event=event, details=details, person_name=c["r_name"], phone=n["recipient"],
                                image_url=image, clip_url=clip)
    return webhook.render(ep["payload_template"], values).encode("utf-8"), event


async def process(n) -> str:
    """Sends one claimed notification. Returns the status it ended in."""
    s = get_settings()
    async with db.service_tx() as conn:
        c = await _context(conn, n)
        if c is None or n["recipient_id"] is None or not c["r_active"]:       # no recipient_id means the person was deleted after this was queued
            await conn.execute("UPDATE app.notifications SET status='cancelled', error='The recipient was removed or switched off.', locked_until=NULL WHERE id=$1", n["id"])
            return "cancelled"
        if n["report_id"]:
            provider = await load_provider(conn, "email")
            msg = await build_report_email(conn, n, c) if provider else None
        elif n["channel"] != "whatsapp" or c["rule_type"] not in cat.CATALOG:
            await conn.execute("UPDATE app.notifications SET status='cancelled', error='Alerts are sent by WhatsApp only.', locked_until=NULL WHERE id=$1", n["id"])
            return "cancelled"
        else:
            ep = await conn.fetchrow("SELECT * FROM platform.webhook_endpoints WHERE alert_type=$1", c["rule_type"])
            if not ep or not ep["active"]:
                await conn.execute("UPDATE app.notifications SET status='cancelled', error='No active webhook for this alert.', locked_until=NULL WHERE id=$1", n["id"])
                return "cancelled"
            cfg = await webhook_settings(conn)
            headers = endpoint_headers(ep)
    if n["report_id"]:
        if not provider:
            return await _finish(n, webhook.Result(False, permanent=False, error="No email provider is active."))
        if isinstance(msg, str):
            return await _finish(n, webhook.Result(False, permanent=False, error=msg))
        res = await smtp_provider.send(provider["config"], provider["secrets"], msg, allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
        return await _finish(n, res)
    body, event = event_body(ep, c, n, cfg["link_hours"])
    try:
        json.loads(body)
    except ValueError:
        return await _finish(n, webhook.Result(False, permanent=True, error="The payload is not valid JSON. Fix the template of this webhook."), event=event, endpoint=ep["id"])
    res = await webhook.send(ep["url"], headers, body, event=event, delivery_id=str(n["id"]), secret=cfg["secret"], allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
    return await _finish(n, res, event=event, endpoint=ep["id"])


async def _finish(n, res, *, event=None, endpoint=None) -> str:
    async with db.service_tx() as conn:
        if res.ok:
            await conn.execute("UPDATE app.notifications SET status='sent', sent_at=now(), provider_message_id=$2, event=$3, endpoint_id=$4, response_status=$5, error=NULL, locked_until=NULL WHERE id=$1",
                               n["id"], res.message_id, event, endpoint, res.status)
            return "sent"
        final = res.permanent or n["attempts"] >= MAX_ATTEMPTS
        err = (res.error or "Failed")[:300]
        if not final:
            await conn.execute("UPDATE app.notifications SET next_attempt_at = now() + make_interval(secs => $2), error=$3, event=coalesce($4, event), endpoint_id=coalesce($5, endpoint_id), "
                               "response_status=$6, locked_until=NULL WHERE id=$1", n["id"], float(backoff(n["attempts"])), err, event, endpoint, res.status)
            return "queued"
        await conn.execute("UPDATE app.notifications SET status='failed', error=$2, event=coalesce($3, event), endpoint_id=coalesce($4, endpoint_id), response_status=$5, locked_until=NULL WHERE id=$1",
                           n["id"], err, event, endpoint, res.status)
        return "failed"


async def run_once(batch: int | None = None) -> int:
    """One pass: send what is due, a few events a second. Returns how many were handled."""
    rows = await claim(batch or get_settings().delivery_batch)
    gap, last = 1.0 / EVENTS_PER_SECOND, 0.0
    for n in rows:
        try:
            wait = last + gap - asyncio.get_running_loop().time()
            if wait > 0:
                await asyncio.sleep(wait)
            await process(n)
            last = asyncio.get_running_loop().time()
        except Exception:
            log.exception("delivery of %s failed unexpectedly", n["id"])
            async with db.service_tx() as conn:       # never leave it locked and never lose it silently
                await conn.execute("UPDATE app.notifications SET next_attempt_at = now() + interval '5 minutes', locked_until=NULL, error='Internal error while sending.' WHERE id=$1 AND status='queued'", n["id"])
    return len(rows)


async def run_forever() -> None:
    s = get_settings()
    while True:
        try:
            if await run_once() == 0:
                await asyncio.sleep(s.delivery_poll_seconds)
        except Exception:
            log.exception("delivery loop failed")
            await asyncio.sleep(5)
