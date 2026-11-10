"""Push notifications to the customer app (phones and browsers), through Firebase Cloud Messaging.

Push is an addition to WhatsApp, not a replacement: an alert is still sent by WhatsApp, and also pushed to every device signed in to the account
that has agreed to notifications. It is best effort (no retries; a device that has gone is forgotten), and it is off until a Firebase project is set up:
GI_FCM_PROJECT_ID and GI_FCM_SERVICE_ACCOUNT_FILE (the path to the service-account key file, which is never put in the database or in settings text).
Nothing in a notification identifies a person's data beyond the alert's own title: the app opens and fetches the rest after sign-in."""
import asyncio
import json
import logging
import time

import httpx
import jwt

from ..config import get_settings
from ..db import db

log = logging.getLogger("gateinsight.push")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
transport: httpx.AsyncBaseTransport | None = None        # tests replace this with a stand-in for Google
_token: tuple[float, str] | None = None
_tasks: set = set()


def enabled() -> bool:
    s = get_settings()
    return bool(s.fcm_project_id and s.fcm_service_account_file)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10, transport=transport)


async def access_token() -> str:
    """A short-lived Google access token for the service account, kept for most of its hour."""
    global _token
    if _token and _token[0] > time.time() + 120:
        return _token[1]
    with open(get_settings().fcm_service_account_file) as f:
        sa = json.load(f)
    now = int(time.time())
    assertion = jwt.encode({"iss": sa["client_email"], "scope": SCOPE, "aud": sa.get("token_uri", "https://oauth2.googleapis.com/token"), "iat": now, "exp": now + 3000},
                           sa["private_key"], algorithm="RS256")
    async with _client() as c:
        r = await c.post(sa.get("token_uri", "https://oauth2.googleapis.com/token"), data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    r.raise_for_status()
    j = r.json()
    _token = (time.time() + int(j.get("expires_in", 3000)), j["access_token"])
    return _token[1]


async def send(token: str, title: str, body: str, data: dict[str, str]) -> str:
    """'ok', 'gone' (the device no longer exists, so forget it) or 'error'."""
    s = get_settings()
    msg = {"message": {"token": token, "notification": {"title": title[:100], "body": body[:200]}, "data": data,
                       "android": {"priority": "HIGH"}, "apns": {"headers": {"apns-priority": "10"}}}}
    try:
        at = await access_token()
        async with _client() as c:
            r = await c.post(f"https://fcm.googleapis.com/v1/projects/{s.fcm_project_id}/messages:send", json=msg, headers={"Authorization": f"Bearer {at}"})
    except Exception as e:                      # never let a push problem touch an alert
        log.warning("push failed: %s", type(e).__name__)
        return "error"
    if r.status_code == 200:
        return "ok"
    if r.status_code in (404, 400) and any(x in r.text for x in ("UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND")):
        return "gone"
    if r.status_code == 401:
        global _token
        _token = None
    log.warning("push refused: %s", r.status_code)
    return "error"


async def deliver(tokens: list[str], title: str, body: str, data: dict[str, str]) -> dict[str, int]:
    out = {"ok": 0, "gone": 0, "error": 0}
    gone = []
    for t in tokens:
        r = await send(t, title, body, data)
        out[r] += 1
        if r == "gone":
            gone.append(t)
    if gone:
        try:
            async with db.admin_tx() as c:
                await c.execute("DELETE FROM app.push_devices WHERE token = ANY($1::text[])", gone)
        except Exception as e:
            log.warning("could not forget old devices: %s", type(e).__name__)
    return out


async def notify_alert(conn, tenant_id, alert_id, title: str, body: str, severity: str, quiet: bool) -> None:
    """Push a new alert to the account's devices. Called when an alert is raised; sends in the background so the alert itself is never held up."""
    if not enabled() or quiet or severity == "info":
        return
    tokens = [r["token"] for r in await conn.fetch("SELECT token FROM app.push_devices WHERE tenant_id=$1", tenant_id)]
    if not tokens:
        return
    t = asyncio.get_event_loop().create_task(deliver(tokens, title, body, {"kind": "alert", "alert_id": str(alert_id)}))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
