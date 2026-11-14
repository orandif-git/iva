"""Notifications on a console user's phone (Web Push), by role.

How it works: a phone turns notifications on in the Control Center (More, Notifications). The browser gives it a private address at Apple, Google or Mozilla; we keep that
address (platform.push_subscriptions) and send a small encrypted message to it. Nothing here needs Firebase.
Switched on by two settings: GI_VAPID_PUBLIC_KEY and GI_VAPID_PRIVATE_KEY (make a pair with `python -m app.services.staff_push keygen`). Until they are set, the screen says so.

What is sent, and to whom: five kinds (CATEGORIES). Each person can switch each kind on or off; until they do, their role decides (ROLE_DEFAULTS).
A watcher looks every minute (`check`): it announces a problem once when it starts, and again only if it goes away and comes back.
The encryption is RFC 8291 (aes128gcm) and the sender's proof is RFC 8292 (VAPID); both are done here with `cryptography` and PyJWT."""
import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime, time as dtime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings
from ..db import db

log = logging.getLogger("gateinsight.staffpush")
IST = ZoneInfo("Asia/Kolkata")
CHECK_SECONDS = 60
MORNING_SUMMARY_AT, ENDING_AT = dtime(8, 30), dtime(9, 0)
transport: httpx.AsyncBaseTransport | None = None            # tests replace this with a stand-in for the push services

CATEGORIES = [
    ("server", "Server problems", "Analysis stopped, disk nearly full, server too busy"),
    ("customers", "Customer problems", "A recorder goes offline or comes back"),
    ("ending", "Ending soon", "A customer's service ends within 7 days"),
    ("held", "Messages not sent", "A message could not be sent"),
    ("daily", "Daily summary", "One short note each morning"),
]
KEYS = [c[0] for c in CATEGORIES]
ROLE_DEFAULTS = {"owner": set(KEYS), "admin": set(KEYS), "support": {"customers"}, "onboarding": {"ending"}}


def chosen(role: str, prefs: dict | None) -> set[str]:
    """What this person gets: their own switches where they have set them, the role's defaults for the rest."""
    base = set(ROLE_DEFAULTS.get(role, set()))
    for k, v in (prefs or {}).items():
        if k in KEYS:
            (base.add if v else base.discard)(k)
    return base


# ------------------------------------------------------------------------------------------------ keys
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keygen() -> tuple[str, str]:
    """A new pair: (public key, private key), both as the short text the browser and the settings use."""
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(pub), _b64(k.private_numbers().private_value.to_bytes(32, "big"))


def enabled() -> bool:
    s = get_settings()
    return bool(s.vapid_public_key and s.vapid_private_key)


def _private_key() -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(_unb64(get_settings().vapid_private_key), "big"), ec.SECP256R1())


# ------------------------------------------------------------------------------------------------ the message
def encrypt(plain: bytes, p256dh: str, auth: str, *, sender: ec.EllipticCurvePrivateKey | None = None, salt: bytes | None = None) -> bytes:
    """RFC 8291: the body for one phone. Only that phone can read it."""
    ua_pub = _unb64(p256dh)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_pub = sender.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = HKDF(hashes.SHA256(), 32, _unb64(auth), b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    record = AESGCM(cek).encrypt(nonce, plain + b"\x02", None)                       # one record; 0x02 marks the last
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + record


def vapid_header(endpoint: str) -> str:
    s = get_settings()
    u = urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(datetime.now().timestamp()) + 12 * 3600, "sub": s.vapid_subject or s.public_base_url}
    token = jwt.encode(claims, _private_key(), algorithm="ES256")
    return f"vapid t={token}, k={s.vapid_public_key}"


async def send_one(sub: dict, message: dict) -> str:
    """'ok', 'gone' (the phone no longer wants these, so forget it) or 'error'."""
    try:
        body = encrypt(json.dumps(message).encode(), sub["p256dh"], sub["auth"])
        async with httpx.AsyncClient(timeout=10, transport=transport) as c:
            r = await c.post(sub["endpoint"], content=body, headers={"Authorization": vapid_header(sub["endpoint"]), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                                                                       "TTL": "86400", "Urgency": "high"})
    except Exception as e:                                       # never let a notification problem touch anything else
        log.warning("push failed: %s", type(e).__name__)
        return "error"
    if r.status_code in (200, 201, 202):
        return "ok"
    if r.status_code in (404, 410):
        return "gone"
    log.warning("push refused: %s", r.status_code)
    return "error"


async def notify(conn, category: str, title: str, body: str, url: str = "/console/", tag: str | None = None, only_staff=None) -> dict:
    """Tell everyone who chose this kind of notification (or whose role does) on every phone they turned it on for."""
    out = {"ok": 0, "gone": 0, "error": 0}
    if not enabled():
        return out
    rows = await conn.fetch(
        """SELECT p.id, p.endpoint, p.p256dh, p.auth, u.id AS staff_id, u.role::text AS role, u.push_prefs
           FROM platform.push_subscriptions p JOIN platform.staff_users u ON u.id = p.staff_id WHERE u.status='active' AND ($1::uuid IS NULL OR u.id = $1)""", only_staff)
    msg = {"title": title[:80], "body": body[:200], "url": url, "tag": tag or category}
    for r in rows:
        prefs = json.loads(r["push_prefs"]) if isinstance(r["push_prefs"], str) else r["push_prefs"]
        if only_staff is None and category not in chosen(r["role"], prefs):
            continue
        res = await send_one(dict(r), msg)
        out[res] += 1
        if res == "gone":
            await conn.execute("DELETE FROM platform.push_subscriptions WHERE id=$1", r["id"])
        elif res == "ok":
            await conn.execute("UPDATE platform.push_subscriptions SET last_ok_at=now(), failures=0 WHERE id=$1", r["id"])
        else:
            await conn.execute("UPDATE platform.push_subscriptions SET failures = failures + 1 WHERE id=$1", r["id"])
            await conn.execute("DELETE FROM platform.push_subscriptions WHERE id=$1 AND failures >= 20", r["id"])
    return out


# ------------------------------------------------------------------------------------------------ the watcher
async def _state(conn, key: str) -> str | None:
    return await conn.fetchval("SELECT value FROM platform.push_state WHERE key=$1", key)


async def _set(conn, key: str, value: str) -> None:
    await conn.execute("INSERT INTO platform.push_state (key, value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, at=now()", key, value)


async def server_problems(conn) -> list[tuple[str, str]]:
    from . import system_health
    d = await system_health.status(conn)
    out = []
    if d["worker"]["level"] == "crit":
        out.append(("worker", "The analysis worker is not reporting. Restart it on the server."))
    if d["disk"]["level"] == "crit":
        left = d["disk"]["days_left"]
        out.append(("disk", f"The server disk is {round(d['disk']['pct'] or 0)}% full" + (f", about {round(left)} days left." if left is not None else ".")))
    if d["cpu"]["level"] == "crit":
        out.append(("cpu", "The server is too busy."))
    if d["cameras"]["silent"] and d["cameras"]["level"] == "crit" and d["worker"]["level"] != "crit":
        out.append(("cameras", f"{d['cameras']['silent']} camera{'s' if d['cameras']['silent'] > 1 else ''} sent nothing for 10 minutes."))
    if d["messages"]["level"] == "crit":
        out.append(("messages", f"Messages have been waiting for {d['messages']['oldest_minutes']} minutes."))
    return out


async def check_server(conn) -> None:
    now = {k: t for k, t in await server_problems(conn)}
    before = set(filter(None, (await _state(conn, "server") or "").split(",")))
    for k in sorted(set(now) - before):
        await notify(conn, "server", "Server problem", now[k], "/console/#setup/sys", tag=f"server-{k}")
    if before and not now:
        await notify(conn, "server", "Server is back to normal", "Everything is running again.", "/console/#setup/sys", tag="server-ok")
    if set(now) != before:
        await _set(conn, "server", ",".join(sorted(now)))


async def check_recorders(conn) -> None:
    rows = await conn.fetch("SELECT r.id, r.status::text AS status, coalesce(r.name, r.device_id) AS name, t.id AS tenant_id, t.name AS customer FROM app.recorders r JOIN app.tenants t ON t.id=r.tenant_id WHERE t.status NOT IN ('suspended','cancelled')")
    known = {r["key"]: r["value"] for r in await conn.fetch("SELECT key, value FROM platform.push_state WHERE key LIKE 'rec:%'")}
    for r in rows:
        key, now = f"rec:{r['id']}", "offline" if r["status"] == "offline" else "online"
        was = known.get(key)
        if was is None:                                        # the first time we see it: remember it quietly
            await _set(conn, key, now)
            continue
        if was != now:
            await _set(conn, key, now)
            if now == "offline":
                await notify(conn, "customers", f"{r['customer']}: recorder offline", f"{r['name']} is offline.", f"/console/#customer/{r['tenant_id']}/health", tag=key)
            else:
                await notify(conn, "customers", f"{r['customer']}: recorder back", f"{r['name']} is online again.", f"/console/#customer/{r['tenant_id']}/health", tag=key)


async def check_failed(conn) -> None:
    n = await conn.fetchval("SELECT count(*)::int FROM app.notifications WHERE status='failed' AND created_at > now() - interval '24 hours'")
    last = await _state(conn, "failed_n")
    if last is None:
        return await _set(conn, "failed_n", str(n))
    if n > int(last):
        recent = await _state(conn, "failed_at")
        if not recent or (datetime.now().timestamp() - float(recent)) > 1800:                # at most one note every half hour
            await notify(conn, "held", "Messages not sent", f"{n - int(last)} more message{'s' if n - int(last) > 1 else ''} could not be sent. {n} in the last 24 hours.", "/console/#messages", tag="held")
            await _set(conn, "failed_at", str(datetime.now().timestamp()))
        else:
            return
    await _set(conn, "failed_n", str(n))


async def check_daily(conn, now: datetime | None = None) -> None:
    """Once a day: the customers whose service ends soon (from 9:00) and the morning summary (from 8:30), India time."""
    now = now or datetime.now(IST)
    today = now.date().isoformat()
    if now.time() >= ENDING_AT and await _state(conn, "ending_day") != today:
        await _set(conn, "ending_day", today)
        rows = await conn.fetch("SELECT name, (ends_on - $1::date) AS d FROM app.tenants WHERE status NOT IN ('suspended','cancelled') AND ends_on IS NOT NULL AND ends_on >= $1::date AND ends_on <= $1::date + 7 ORDER BY ends_on", now.date())
        if rows:
            top = ", ".join(f"{r['name']} ({r['d']} day{'s' if r['d'] != 1 else ''})" for r in rows[:3]) + (f" and {len(rows) - 3} more" if len(rows) > 3 else "")
            await notify(conn, "ending", f"{len(rows)} customer{'s' if len(rows) > 1 else ''} ending soon", top, "/console/#customers", tag="ending")
    if now.time() >= MORNING_SUMMARY_AT and await _state(conn, "daily_day") != today:
        await _set(conn, "daily_day", today)
        c = {r["s"]: r["n"] for r in await conn.fetch("SELECT status::text AS s, count(*)::int AS n FROM app.notifications WHERE created_at > now() - interval '24 hours' GROUP BY 1")}
        sent, failed = sum(c.get(k, 0) for k in ("sent", "delivered", "read")), c.get("failed", 0)
        look = await conn.fetchval("SELECT count(DISTINCT tenant_id)::int FROM app.recorders WHERE status='offline'")
        await notify(conn, "daily", "Good morning", f"Last 24 hours: {sent} message{'s' if sent != 1 else ''} sent, {failed} failed. {look} customer{'s' if look != 1 else ''} with an offline recorder.", "/console/#overview", tag="daily")


async def check() -> None:
    if not enabled():
        return
    async with db.admin_tx() as conn:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM platform.push_subscriptions)"):
            return                                              # nobody to tell
    for step in (check_server, check_recorders, check_failed, check_daily):
        try:
            async with db.admin_tx() as conn:                   # one step at a time, so one that fails does not stop the others
                await step(conn)
        except Exception:
            log.exception("push check %s failed", step.__name__)


async def run_forever() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await check()
        except Exception:
            log.exception("push check failed")
        await asyncio.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    if sys.argv[1:] == ["keygen"]:
        pub, priv = keygen()
        print(f"GI_VAPID_PUBLIC_KEY={pub}\nGI_VAPID_PRIVATE_KEY={priv}")
    else:
        print("usage: python -m app.services.staff_push keygen")
