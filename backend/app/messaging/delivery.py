"""The delivery service: takes queued messages from the outbox, sends them through the active provider, and records what happened.

At-least-once: a message is claimed (locked for a few minutes), sent, then marked sent. If the process dies in between, the lock lapses and
another try is made, so in rare cases a person could get one message twice, never zero. Our notification id is passed to the provider as a
reference so a provider that supports idempotency can drop the duplicate."""
import asyncio
import html
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from ..config import get_settings
from ..crypto import vault
from ..db import db
from ..services.alerts import clean_text
from . import evidence, http_provider, smtp_provider

log = logging.getLogger("gateinsight.delivery")

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [30, 120, 600, 1800, 7200]
LOCK_SECONDS = 180
_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}

_ssl_context = None          # tests inject a context that trusts their local server


def set_ssl_context(ctx) -> None:
    global _ssl_context
    _ssl_context = ctx


def ssl_context():
    return _ssl_context


async def load_provider(conn, channel: str):
    row = await conn.fetchrow("SELECT * FROM platform.messaging_providers WHERE channel=$1::app.channel_kind AND active", channel)
    if not row:
        return None
    secrets = json.loads(vault().decrypt(bytes(row["secrets_ciphertext"]), str(row["id"]))) if row["secrets_ciphertext"] else {}
    return {"id": row["id"], "kind": row["kind"], "config": row["config"], "secrets": secrets, "rate": float(row["rate_per_second"])}


async def pick_template(conn, has_image: bool, has_clip: bool = False):
    """The best approved WhatsApp template for this alert. Falls back from clip to picture to link to plain text."""
    order = ["alert_image", "alert_link", "alert_text"] if has_image else ["alert_link", "alert_text"] if has_clip else ["alert_text"]
    if has_clip:
        order = ["alert_clip"] + order
    rows = {r["key"]: r for r in await conn.fetch("SELECT * FROM platform.message_templates WHERE channel='whatsapp' AND key = ANY($1) AND status='approved'", order)}
    for k in order:
        if k in rows:
            return rows[k]
    return None


async def claim(n: int):
    async with db.service_tx() as conn:
        return await conn.fetch(
            """UPDATE app.notifications SET locked_until = now() + make_interval(secs => $2), attempts = attempts + 1
               WHERE id IN (SELECT id FROM app.notifications WHERE status='queued' AND next_attempt_at <= now()
                              AND (locked_until IS NULL OR locked_until < now()) ORDER BY next_attempt_at LIMIT $1 FOR UPDATE SKIP LOCKED)
               RETURNING *""", n, float(LOCK_SECONDS))


async def _context(conn, n):
    return await conn.fetchrow(
        """SELECT a.id AS alert_id, a.title, a.body, a.details, a.evidence_id, a.clip_id, a.camera_id, ev.kind AS ev_kind, r.name AS r_name, r.active AS r_active,
                  r.phone AS r_phone, r.email AS r_email, t.name AS tenant_name
           FROM app.notifications n JOIN app.tenants t ON t.id = n.tenant_id
                LEFT JOIN app.alerts a ON a.id = n.alert_id LEFT JOIN app.evidence ev ON ev.id = a.evidence_id
                LEFT JOIN app.recipients r ON r.id = n.recipient_id
           WHERE n.id = $1""", n["id"])


async def build_whatsapp(conn, n, c):
    """Returns (message dict, template key) or an error string."""
    has_image = bool(c["evidence_id"]) and c["ev_kind"] == "image"
    tpl = await pick_template(conn, has_image, bool(c["clip_id"]))
    if not tpl:
        return "No approved WhatsApp template yet. Submit the templates for approval in the admin console."
    d = n["payload"] or {}
    vars_ = [clean_text(d.get(k, "")) for k in tpl["variables"]]
    token = link = clip_token = None
    primary = c["evidence_id"] or c["clip_id"]                  # the page behind the link shows the picture and the clip together
    if primary and (tpl["link_button"] or tpl["media_type"] != "none"):
        token, link = evidence.link_for(primary)
    if tpl["media_type"] == "video" and c["clip_id"]:
        clip_token = evidence.link_for(c["clip_id"])[0]
    msg = {"recipient": {"phone": n["recipient"], "name": c["r_name"] or ""}, "template": {"name": tpl["provider_template"], "language": tpl["language"]},
           "vars": vars_, "media_type": tpl["media_type"], "media_url": evidence.image_url(token) if token and tpl["media_type"] == "image" else evidence.clip_url(clip_token) if clip_token else None,
           "link_url": link, "link_token": token if tpl["link_button"] else None, "message_id": str(n["id"]),
           "alert": {"title": c["title"], "body": c["body"] or "", "camera": d.get("location", ""), "time": d.get("time", "")}}
    return msg, tpl["key"]


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
            "attachments": [] if big else [(fname, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]}, "report"


def build_email(n, c):
    d = n["payload"] or {}
    link = None
    if c["evidence_id"] or c["clip_id"]:
        link = evidence.link_for(c["evidence_id"] or c["clip_id"])[1]
    lines = [f"Event: {d.get('event', c['title'])}", f"Location: {d.get('location', '-')}", f"Time: {d.get('time', '-')}", f"Details: {d.get('details', '-')}"]
    text = f"Hello {c['r_name'] or ''},\n\n" + "\n".join(lines) + (f"\n\nSee the evidence: {link}\n(the link expires in {get_settings().evidence_link_ttl_hours} hours)" if link else "") + "\n\nJas iVision"
    rows = "".join(f"<p><b>{html.escape(l.split(': ')[0])}:</b> {html.escape(l.split(': ', 1)[1])}</p>" for l in lines)
    htm = f"<div style='font-family:sans-serif'><p>Hello {html.escape(c['r_name'] or '')},</p>{rows}" + \
          (f"<p><a href='{html.escape(link)}'>See the evidence</a> (link expires in {get_settings().evidence_link_ttl_hours} hours)</p>" if link else "") + "<p>Jas iVision</p></div>"
    return {"to": n["recipient"], "name": c["r_name"], "subject": f"[Jas iVision] {d.get('event', c['title'])}", "text": text, "html": htm}


def backoff(attempts: int) -> int:
    return BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]


async def process(n) -> str:
    """Sends one claimed notification. Returns the status it ended in."""
    s = get_settings()
    async with db.service_tx() as conn:
        c = await _context(conn, n)
        if c is None or n["recipient_id"] is None or not c["r_active"]:       # no recipient_id means the person was deleted after this was queued
            await conn.execute("UPDATE app.notifications SET status='cancelled', error='The recipient was removed or switched off.', locked_until=NULL WHERE id=$1", n["id"])
            return "cancelled"
        provider = await load_provider(conn, n["channel"])
        built = None
        if provider:
            if n["report_id"]:
                built = await build_report_email(conn, n, c)
            else:
                built = await build_whatsapp(conn, n, c) if n["channel"] == "whatsapp" else (build_email(n, c), "email")
    if not provider:
        return await _finish(n, http_provider.Result(False, permanent=False, error=f"No {n['channel']} provider is active."))
    if isinstance(built, str):
        return await _finish(n, http_provider.Result(False, permanent=False, error=built))
    msg, tpl_key = built
    cfg, secrets = provider["config"], provider["secrets"]
    if provider["kind"] == "http":
        res = await http_provider.send(cfg, secrets, msg, allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
    else:
        res = await smtp_provider.send(cfg, secrets, msg, allow_private=s.messaging_allow_private_hosts, ssl_context=_ssl_context)
    return await _finish(n, res, provider, tpl_key)


async def _finish(n, res, provider=None, template=None) -> str:
    async with db.service_tx() as conn:
        if res.ok:
            cost = int((provider or {}).get("config", {}).get("cost_paise", 0)) if provider else 0
            await conn.execute(
                """UPDATE app.notifications SET status='sent', sent_at=now(), provider_id=$2, provider_message_id=$3, template=$4, error=NULL, locked_until=NULL,
                          cost_paise=$5 WHERE id=$1""", n["id"], provider["id"] if provider else None, res.message_id, template, cost)
            return "sent"
        final = res.permanent or n["attempts"] >= MAX_ATTEMPTS
        err = (res.error or "Failed")[:300]
        if not final:
            await conn.execute("UPDATE app.notifications SET next_attempt_at = now() + make_interval(secs => $2), error=$3, locked_until=NULL, template=coalesce($4, template) WHERE id=$1",
                               n["id"], float(backoff(n["attempts"])), err, template)
            return "queued"
        await conn.execute("UPDATE app.notifications SET status='failed', error=$2, locked_until=NULL, template=coalesce($3, template) WHERE id=$1", n["id"], err, template)
        await email_fallback(conn, n["id"])
        return "failed"


async def email_fallback(conn, notification_id) -> bool:
    """A WhatsApp message that failed for good is repeated as an email, when the person has an address and email is on for the account."""
    n = await conn.fetchrow("SELECT * FROM app.notifications WHERE id=$1", notification_id)
    if not n or n["channel"] != "whatsapp" or not n["recipient_id"] or not n["alert_id"]:
        return False
    row = await conn.fetchval(
        """INSERT INTO app.notifications (tenant_id, alert_id, recipient_id, channel, recipient, payload, fallback_of)
           SELECT n.tenant_id, n.alert_id, r.id, 'email', r.email, n.payload, n.id
           FROM app.notifications n JOIN app.recipients r ON r.id = n.recipient_id JOIN app.tenants t ON t.id = n.tenant_id
           WHERE n.id = $1 AND r.active AND r.email IS NOT NULL AND t.msg_email
           ON CONFLICT DO NOTHING RETURNING id""", notification_id)
    return row is not None


async def apply_status(conn, provider_id: UUID, message_id: str, status: str, error: str | None) -> bool:
    """A delivery report from the provider. Never moves a message backwards (a late 'sent' cannot undo 'delivered')."""
    n = await conn.fetchrow("SELECT id, status::text AS status FROM app.notifications WHERE provider_id=$1 AND provider_message_id=$2 FOR UPDATE", provider_id, message_id)
    if not n:
        return False
    cur = n["status"]
    if status == "failed":
        if cur in ("delivered", "read", "failed"):
            return False
        await conn.execute("UPDATE app.notifications SET status='failed', error=$2 WHERE id=$1", n["id"], (error or "The provider reported a failure.")[:300])
        await email_fallback(conn, n["id"])
        return True
    if _RANK.get(status, -1) <= _RANK.get(cur, 99):
        return False
    await conn.execute("UPDATE app.notifications SET status=$2::app.delivery_status, delivered_at = CASE WHEN $2 IN ('delivered','read') THEN coalesce(delivered_at, now()) ELSE delivered_at END, "
                       "read_at = CASE WHEN $2='read' THEN now() ELSE read_at END WHERE id=$1", n["id"], status)
    return True


async def run_once(batch: int | None = None) -> int:
    """One pass: send what is due, pacing by each provider's rate. Returns how many were handled."""
    rows = await claim(batch or get_settings().delivery_batch)
    last: dict[str, float] = {}
    for n in rows:
        try:
            ch = n["channel"]
            async with db.service_tx() as conn:
                p = await conn.fetchval("SELECT rate_per_second FROM platform.messaging_providers WHERE channel=$1::app.channel_kind AND active", ch)
            gap = 1.0 / float(p or 5)
            wait = last.get(ch, 0) + gap - asyncio.get_running_loop().time()
            if wait > 0:
                await asyncio.sleep(wait)
            await process(n)
            last[ch] = asyncio.get_running_loop().time()
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
