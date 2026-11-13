"""The delivery service: takes queued messages from the outbox, sends them, and records what happened.

An alert reaches a person as a webhook event (see webhook.py); the platform owner's system turns the event into the WhatsApp message.
A scheduled report is announced the same way, as a "report ready" event with a link to the Excel file (see report_event_body).

At-least-once: a message is claimed (locked for a few minutes), sent, then marked sent. If the process dies in between, the lock lapses and
another try is made, so in rare cases an event could be sent twice, never zero. The notification id travels as X-Delivery-Id, the same on every
try, so the receiver can drop a duplicate. A failed event is tried again for up to about two hours."""
import asyncio
import json
import logging

from ..config import get_settings
from ..crypto import vault
from ..db import db
from . import direct, evidence, webhook
from ..services import alert_catalog as cat, summary_config

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


ALERT_AAD = "alert_connection"


async def shared(conn) -> dict:
    """The shared alert connection: its address and key (the key is decrypted here, for sending only)."""
    r = await conn.fetchrow("SELECT url, key_ciphertext, key_hint FROM platform.alert_connection WHERE id")
    key = vault().decrypt(bytes(r["key_ciphertext"]), ALERT_AAD) if r and r["key_ciphertext"] else None
    return {"url": r["url"] if r else None, "key": key, "hint": r["key_hint"] if r else None}


async def resolve(conn, ep) -> tuple[str, dict[str, str]]:
    """Where an endpoint sends and with which headers. An alert that uses the shared connection takes its address and key from there (its other headers are kept)."""
    headers = endpoint_headers(ep)
    if ep["use_shared"]:
        sh = await shared(conn)
        if sh["url"]:
            if sh["key"]:
                headers = {**headers, "Authorization": "Bearer " + sh["key"]}
            return sh["url"], headers
    return ep["url"], headers


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
    if details.get("test"):                       # a test message: sample values and our own sample picture and video, so nothing in it is empty
        values = webhook.test_values(values)
    return webhook.render(ep["payload_template"], values).encode("utf-8"), event


def report_event_body(ep, rep, n, tenant_name: str, person_name: str, link_hours: int) -> tuple[bytes, str]:
    """The payload for one person for a scheduled report: the endpoint's template with the report, its period and a link to the file."""
    from ..services.reports import TYPES, period_label
    token = evidence.make_token(rep["id"], link_hours * 3600)
    url = f"{get_settings().public_base_url.rstrip('/')}/e/{token}/report.xlsx"
    event = cat.event_name("report_ready")
    values = webhook.values_for(tenant_name=tenant_name, alert_id=rep["id"], title=TYPES[rep["report_type"]][0], event=event, details={"report": TYPES[rep["report_type"]][0],
                                "period": period_label(rep["period_start"], rep["period_end"], rep["timezone"])}, person_name=person_name, phone=n["recipient"], document_url=url)
    return webhook.render(ep["payload_template"], values).encode("utf-8"), event


async def process(n) -> str:
    """Sends one claimed notification. Returns the status it ended in."""
    s = get_settings()
    rep = None
    direct_send = None            # for a summary: (settings, key, text), sent straight to the provider instead of through a webhook
    async with db.service_tx() as conn:
        c = await _context(conn, n)
        if c is None or n["recipient_id"] is None or not c["r_active"]:       # no recipient_id means the person was deleted after this was queued
            await conn.execute("UPDATE app.notifications SET status='cancelled', error='The recipient was removed or switched off.', locked_until=NULL WHERE id=$1", n["id"])
            return "cancelled"
        if n["report_id"]:
            if n["channel"] != "whatsapp":
                await conn.execute("UPDATE app.notifications SET status='cancelled', error='Reports are sent by WhatsApp only.', locked_until=NULL WHERE id=$1", n["id"])
                return "cancelled"
            ep = await conn.fetchrow("SELECT * FROM platform.webhook_endpoints WHERE alert_type='report_ready'")
            if not ep or not ep["active"]:
                await conn.execute("UPDATE app.notifications SET status='cancelled', error='No active webhook for reports.', locked_until=NULL WHERE id=$1", n["id"])
                return "cancelled"
            rep = await conn.fetchrow("SELECT r.*, t.timezone FROM app.reports r JOIN app.tenants t ON t.id = r.tenant_id WHERE r.id=$1", n["report_id"])
            if not rep or rep["status"] != "ready":
                return await _finish(n, webhook.Result(False, permanent=False, error="The report is not ready yet."))
            cfg = await webhook_settings(conn)
            url, headers = await resolve(conn, ep)
        elif n["channel"] != "whatsapp" or c["rule_type"] not in cat.CATALOG:
            await conn.execute("UPDATE app.notifications SET status='cancelled', error='Alerts are sent by WhatsApp only.', locked_until=NULL WHERE id=$1", n["id"])
            return "cancelled"
        elif c["rule_type"] in cat.DIRECT_SEND:
            scfg, skey = await summary_config.load(conn), await summary_config.key(conn)
            if not summary_config.ready(scfg, bool(skey), "daily" if c["rule_type"] == "daily_summary" else "weekly"):
                await conn.execute("UPDATE app.notifications SET status='cancelled', error='WhatsApp summaries are not set up or are switched off.', locked_until=NULL WHERE id=$1", n["id"])
                return "cancelled"
            direct_send = (scfg, skey, c["body"] or "")
        else:
            ep = await conn.fetchrow("SELECT * FROM platform.webhook_endpoints WHERE alert_type=$1", c["rule_type"])
            if not ep or not ep["active"]:
                await conn.execute("UPDATE app.notifications SET status='cancelled', error='No active webhook for this alert.', locked_until=NULL WHERE id=$1", n["id"])
                return "cancelled"
            cfg = await webhook_settings(conn)
            url, headers = await resolve(conn, ep)
    if direct_send is not None:
        scfg, skey, text = direct_send
        res = await direct.send(scfg, skey, n["recipient"], text, allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
        await asyncio.sleep(max(0.0, float(scfg["gap"])))          # spreads out the customers who share a send hour
        return await _finish(n, res, event=cat.event_name(c["rule_type"]), max_attempts=1 + int(scfg["retries"]))
    if rep is not None:
        body, event = report_event_body(ep, rep, n, c["tenant_name"], c["r_name"], cfg["link_hours"])
    else:
        body, event = event_body(ep, c, n, cfg["link_hours"])
    try:
        json.loads(body)
    except ValueError:
        return await _finish(n, webhook.Result(False, permanent=True, error="The payload is not valid JSON. Fix the template of this webhook."), event=event, endpoint=ep["id"])
    res = await webhook.send(url, headers, body, event=event, delivery_id=str(n["id"]), secret=cfg["secret"], allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
    return await _finish(n, res, event=event, endpoint=ep["id"])


async def _finish(n, res, *, event=None, endpoint=None, max_attempts: int = MAX_ATTEMPTS) -> str:
    async with db.service_tx() as conn:
        if res.ok:
            await conn.execute("UPDATE app.notifications SET status='sent', sent_at=now(), provider_message_id=$2, event=$3, endpoint_id=$4, response_status=$5, error=NULL, locked_until=NULL WHERE id=$1",
                               n["id"], res.message_id, event, endpoint, res.status)
            return "sent"
        final = res.permanent or n["attempts"] >= max_attempts
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
