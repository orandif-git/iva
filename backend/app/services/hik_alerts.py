"""Hikvision alerts: a recorder tells us about its faults by sending messages to a private address on our server (/hik/events/<code>).

The code in the address is a long random secret, one per recorder. We keep only its SHA-256 fingerprint, so the database alone cannot be used to send fake alerts.
A message is XML (sometimes JSON), alone or as one part of a multipart message that may also carry a picture; we read only the text parts.
Real faults become the normal system alerts. Everything else is written to a log the team can read, so a message type we have not seen before shows up the first time it arrives."""
import hashlib
import json
import re
import secrets
from email.parser import BytesParser
from email.policy import default as email_policy
from xml.etree import ElementTree as ET

from ..config import get_settings
from ..db import db
from . import alerts

MAX_BODY = 6_000_000          # a message with a picture can be a few hundred KB; anything near this is not a recorder
KEEP_DAYS = 30
FAULT_COOLDOWN = 24 * 60      # a fault is raised once a day however often the recorder repeats it

# What a recorder says  ->  the alert it causes.  Anything not here is only written to the log.
FAULTS = {
    "diskfull": "storage_full",
    "diskerror": "disk_fault",
    "recordException": "recording_stopped",
    "videoloss": "camera_offline",
}


def new_code() -> str:
    return secrets.token_urlsafe(24)


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def receiver() -> tuple[str, int]:
    """Where a recorder sends its messages: our site's name and a PLAIN HTTP port. Hikvision recorders (checked on a DS-7632NXI-K2) accept only plain HTTP for event alerts,
    so a site that is served over HTTPS must still answer plain HTTP on port 80 for /hik/events/ (see DEPLOY.md)."""
    from urllib.parse import urlsplit
    u = urlsplit(get_settings().public_base_url)
    return u.hostname or "localhost", (80 if u.scheme == "https" else u.port or 80)


def address(code: str) -> str:
    host, port = receiver()
    return f"http://{host}{'' if port == 80 else f':{port}'}/hik/events/{code}"


# ------------------------------------------------------------------------------------------------ reading a message
def _text(e: ET.Element | None) -> str:
    return (e.text or "").strip() if e is not None else ""


def _from_xml(raw: bytes) -> dict | None:
    if b"<!DOCTYPE" in raw[:2000].upper() or b"<!ENTITY" in raw.upper():        # never expand anything a stranger sends
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    for e in root.iter():
        e.tag = e.tag.split("}")[-1]
    if root.tag != "EventNotificationAlert":
        return None
    return {k: _text(root.find(k)) for k in ("eventType", "eventState", "channelID", "dynChannelID", "dateTime", "eventDescription")}


def _from_json(raw: bytes) -> dict | None:
    try:
        j = json.loads(raw)
    except ValueError:
        return None
    if isinstance(j, dict) and isinstance(j.get("EventNotificationAlert"), dict):
        j = j["EventNotificationAlert"]
    if not isinstance(j, dict) or "eventType" not in j:
        return None
    return {k: str(j.get(k) or "") for k in ("eventType", "eventState", "channelID", "dynChannelID", "dateTime", "eventDescription")}


def parse(content_type: str, body: bytes) -> list[dict]:
    """Every event found in a message, whatever shape it came in. Never raises: a message we cannot read gives an empty list."""
    parts: list[bytes] = []
    try:
        msg = BytesParser(policy=email_policy).parsebytes(b"Content-Type: " + content_type.encode("latin-1", "ignore") + b"\r\n\r\n" + body)
        if msg.is_multipart():
            parts = [p.get_payload(decode=True) or b"" for p in msg.iter_parts() if p.get_content_maintype() in ("application", "text")]
        else:
            parts = [body]
    except Exception:
        parts = [body]
    out = []
    for p in parts or [body]:
        p = p.strip()
        ev = _from_json(p) if p[:1] in (b"{", b"[") else _from_xml(p)
        if ev is None and p:                       # some recorders wrap the text in extra lines: look for the XML itself
            m = re.search(rb"<EventNotificationAlert.*?</EventNotificationAlert>", p, re.S)
            ev = _from_xml(m.group(0)) if m else None
        if ev and ev["eventType"]:
            out.append(ev)
    return out


def _channel(ev: dict) -> int | None:
    for k in ("channelID", "dynChannelID"):
        if ev.get(k, "").isdigit():
            n = int(ev[k])
            return n if n < 100000 else None
    return None


# ------------------------------------------------------------------------------------------------ what we do about it
def _wording(kind: str, name: str, ch: int | None) -> tuple[str, dict, str]:
    cam = f"Camera {ch}" if ch else "A camera"
    if kind == "diskfull":
        return f"{name} says its storage is full, so new video may not be saved.", {"recorder": name, "percent_full": "100"}, f"hik:{name}:storage"
    if kind == "diskerror":
        return f"{name} reports a problem with a hard disk. Video may not be saved. Please have the recorder checked.", {"recorder": name, "disk": "A disk"}, f"hik:{name}:disk"
    if kind == "recordException":
        return f"{name} could not save video. Please have the recorder checked.", {"recorder": name}, f"hik:{name}:record"
    return f"{cam} on {name} has stopped sending video.", {"recorder": name}, f"hik:{name}:video:{ch or 0}"


LOG_PER_HOUR = 600           # a link may write this many lines an hour; a flood of different message types cannot fill the table


async def _log(conn, link_id, ev: dict, ch, result: str, raw: str) -> None:
    """Write a line in the log. The same quiet message repeated every few seconds is written once in a while, not every time."""
    quiet = ev["eventState"] != "active"
    window = "30 minutes" if quiet else "1 minute"
    if await conn.fetchval(
            f"""SELECT 1 FROM platform.alert_link_messages WHERE link_id=$1 AND event_type=$2 AND channel IS NOT DISTINCT FROM $3 AND state IS NOT DISTINCT FROM $4
                AND result=$5 AND received_at > now() - interval '{window}' LIMIT 1""", link_id, ev["eventType"], ch, ev["eventState"] or None, result):
        return
    if await conn.fetchval("SELECT count(*) FROM platform.alert_link_messages WHERE link_id=$1 AND received_at > now() - interval '1 hour'", link_id) >= LOG_PER_HOUR:
        return
    await conn.execute("INSERT INTO platform.alert_link_messages (link_id, event_type, state, channel, event_time, result, raw) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                       link_id, ev["eventType"][:60], (ev["eventState"] or None) and ev["eventState"][:20], ch, ev["dateTime"][:40] or None, result, raw[:1500])
    await conn.execute("DELETE FROM platform.alert_link_messages WHERE link_id=$1 AND received_at < now() - make_interval(days => $2)", link_id, KEEP_DAYS)


class UnknownCodes:
    """Counts messages with a wrong code per sender address, so guessing codes (or just flooding the address) is slowed down before the database is asked."""

    def __init__(self, limit: int = 30, window: float = 60.0, clock=None):
        import time
        from collections import defaultdict, deque
        self.limit, self.window, self.clock = limit, window, clock or time.monotonic
        self._q = defaultdict(deque)

    def blocked(self, ip: str | None) -> bool:
        q = self._q[ip or "?"]
        now = self.clock()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def miss(self, ip: str | None) -> None:
        if len(self._q) > 20000:
            self._q.clear()
        self._q[ip or "?"].append(self.clock())


unknown = UnknownCodes()


async def exists(code: str) -> bool:
    async with db.edge_tx() as conn:
        return bool(await conn.fetchval("SELECT platform.edge_hik_exists($1)", fingerprint(code)))


async def receive(code: str, content_type: str, body: bytes, ip: str | None) -> bool:
    """Handles one message from a recorder. False when the code is not one of ours (the caller answers 'not found'); True otherwise, even for a message we could not read.
    The lookup and the log use the narrow public role; an alert is raised under the customer's own restricted role."""
    async with db.edge_tx() as conn:
        link = await conn.fetchrow("SELECT * FROM platform.edge_hik_touch($1, $2::inet)", fingerprint(code), ip)
    if not link:
        return False
    stopped = link["tenant_status"] in ("suspended", "cancelled")
    notes = []                                  # (event, channel, what we did, the text to keep)
    events = parse(content_type, body)
    if not events:
        text = body[:300].decode("utf-8", "replace") if b"\x00" not in body[:300] else "(not text)"
        notes.append(({"eventType": "unreadable", "eventState": "", "dateTime": ""}, None, "Could not read this message", text))
    for ev in events:
        kind, ch = ev["eventType"], _channel(ev)
        if kind == "heartBeat":
            continue
        raw = f"{kind} {ev['eventState']} channel {ch} {ev['eventDescription']}".strip()
        if ev["eventState"] != "active":
            notes.append((ev, ch, "Noted. Nothing to send.", raw))
        elif stopped:
            notes.append((ev, ch, "Not sent: this customer's service has stopped.", raw))
        elif kind in FAULTS:
            what, details, key = _wording(kind, link["name"], ch)
            async with db.tenant_tx(link["tenant_id"]) as tconn:
                made = await alerts.raise_alert(tconn, link["tenant_id"], FAULTS[kind], body=what, camera_name=link["name"] if kind != "videoloss" or not ch else f"Camera {ch}",
                                                details=details, dedupe_key=key, cooldown_minutes=FAULT_COOLDOWN if kind != "videoloss" else 60)
            notes.append((ev, ch, "Alert raised." if made else "Alert not raised again (sent recently, or switched off).", raw))
        else:
            notes.append((ev, ch, "Logged only. No alert for this kind of message yet.", raw))
    if notes:
        async with db.edge_tx() as conn:
            for ev, ch, result, raw in notes:
                await _log(conn, link["id"], ev, ch, result, raw)
    return True


# ------------------------------------------------------------------------------------------------ for the team
def setup_command(code: str) -> str:
    """The one command that tells a recorder where to send its alerts. The recorder's address and password are asked for, never stored here.
    Plain HTTP only: this is the one value the recorder accepts (it refuses HTTPS)."""
    host, port = receiver()
    return ("curl --digest -u admin -X PUT -H 'Content-Type: application/xml' http://RECORDER_ADDRESS/ISAPI/Event/notification/httpHosts/1 -d "
            f"'<HttpHostNotification><id>1</id><url>/hik/events/{code}</url><protocolType>HTTP</protocolType><parameterFormatType>XML</parameterFormatType>"
            f"<addressingFormatType>hostname</addressingFormatType><hostName>{host}</hostName><portNo>{port}</portNo>"
            "<httpAuthenticationMethod>none</httpAuthenticationMethod></HttpHostNotification>'")
