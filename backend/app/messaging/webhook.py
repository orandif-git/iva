"""Alert webhooks: fill in the payload, sign it, send it to the platform owner's address, and read the answer.

An alert leaves as one event per person who should be told. The platform owner decides what the payload looks like: a JSON template with
{shortcodes}. The system that receives the event sends the WhatsApp message. Nothing comes back to us except the HTTP answer.

Safety: only https addresses that point to the public internet are called (see netguard), redirects are never followed, header values are kept
encrypted, and every event is signed so the receiver can check it came from us."""
import hashlib
import hmac
import json
import re
import ssl
import time
from dataclasses import dataclass

import httpx

from ..services import alert_catalog as cat
from . import netguard

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_RESPONSE = 4000
MAX_TEMPLATE = 8000
SHORTCODE = re.compile(r"\{(\w+)\}")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,60}$")
OURS = {"x-event", "x-delivery-id", "x-timestamp", "x-signature", "x-test", "host", "content-length", "transfer-encoding", "connection"}
RETRY_STATUSES = {408, 425, 429}


@dataclass
class Result:
    ok: bool
    permanent: bool = False
    error: str | None = None
    status: int | None = None
    message_id: str | None = None          # only email gives one
    raw: str | None = None


# What a shortcode shows in the preview and in a test event.
SAMPLE = {
    "customer": "Sunrise Mart", "camera": "Back gate", "time": "11 Nov, 11:42 PM", "details": "No video is being received.", "title": "Camera offline",
    "to_name": "Anita", "to_phone": "919876543210", "event_type": "system.camera_offline", "alert_id": "0b6f2c1e-3a77-4d3e-9c1a-5a1f9e000001",
    "recorder": "Warehouse", "drops": "5", "minutes_out": "7", "what_recovered": "Entrance camera", "percent_full": "91", "days_left": "2", "disk": "Disk 2",
    "used": "42", "limit": "50", "invoice": "INV-2026-0042", "due_date": "15 Nov", "device": "iPhone", "place": "Surat", "summary": "7 cameras working, 1 needs a look",
    "plate": "GJ05AB1234", "direction": "in", "hours": "8", "stay": "45 minutes", "confidence": "62", "people": "54", "area": "Billing counter", "length": "8", "minutes": "3", "wait_minutes": "6", "seconds": "25",
    "image_url": "https://ivision.example/e/Xk3q/image.jpg", "clip_url": "https://ivision.example/e/Xk3q/clip.mp4",
    "cameras": "12", "what": "moved", "report": "Footfall summary", "period": "3 Nov to 9 Nov", "document_url": "https://ivision.example/e/Xk3q/report.xlsx",
}


def default_template(alert_type: str) -> str:
    """The payload a new endpoint starts with. The owner edits it; every shortcode of the alert can be added."""
    meta = cat.CATALOG[alert_type]
    if alert_type == "report_ready":
        return ("{\n  \"phone_number\": \"{to_phone}\",\n  \"template_name\": \"system_report_ready_v1\",\n  \"customer\": \"{customer}\",\n  \"report\": \"{report}\",\n"
                "  \"period\": \"{period}\",\n  \"document_url\": \"{document_url}\"\n}")
    lines = ['"phone_number": "{to_phone}"', f'"template_name": "{cat.event_name(alert_type).replace(".", "_")}_v1"', '"customer": "{customer}"', '"camera": "{camera}"',
             '"time": "{time}"', '"details": "{details}"']
    for k in cat.MEDIA_SHORTCODES.get(meta["media"], []):
        lines.append(f'"{k}": "{{{k}}}"')
    return "{\n  " + ",\n  ".join(lines) + "\n}"


def render(template: str, values: dict[str, str]) -> str:
    """Fills in the shortcodes. A value goes into the JSON text with its quotes, slashes and line breaks escaped, so it can never change the structure."""
    def one(m):
        k = m.group(1)
        return json.dumps(str(values[k]), ensure_ascii=False)[1:-1] if k in values else m.group(0)
    return SHORTCODE.sub(one, template)


def validate_template(alert_type: str, template: str) -> None:
    """Raises ValueError with a sentence the owner can act on."""
    if not template.strip() or len(template) > MAX_TEMPLATE:
        raise ValueError("The payload is empty or too long.")
    allowed = set(cat.shortcodes_for(alert_type))
    for k in dict.fromkeys(SHORTCODE.findall(template)):
        if k not in allowed:
            raise ValueError(f"{{{k}}} is not a shortcode of this alert. Use only the ones listed.")
    try:
        data = json.loads(render(template, SAMPLE))
    except ValueError as e:
        raise ValueError(f"The payload is not valid JSON ({e}).")
    if not isinstance(data, dict):
        raise ValueError("The payload must be a JSON object, starting with { and ending with }.")


def preview(template: str) -> str:
    out = render(template, SAMPLE)
    try:
        return json.dumps(json.loads(out), indent=2, ensure_ascii=False)
    except ValueError:
        return out


def validate_headers(headers: dict) -> dict[str, str]:
    if not isinstance(headers, dict) or len(headers) > 20:
        raise ValueError("Headers must be a short list of name and value pairs.")
    out = {}
    for k, v in headers.items():
        if not isinstance(k, str) or not HEADER_NAME.match(k):
            raise ValueError(f"'{k}' is not a valid header name.")
        if k.lower() in OURS:
            raise ValueError(f"{k} is set by the system and cannot be changed.")
        if not isinstance(v, str) or not 0 < len(v) <= 1000 or any(c in v for c in "\r\n\x00"):
            raise ValueError(f"The value of {k} must be one line of text.")
        out[k] = v
    return out


def check_url(url: str, *, allow_private: bool = False) -> None:
    try:
        netguard.check_url(url, allow_private=allow_private)
    except netguard.BlockedAddress as e:
        raise ValueError(str(e))


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def values_for(*, tenant_name: str, alert_id, title: str, event: str, details: dict, person_name: str, phone: str, image_url: str = "", clip_url: str = "", document_url: str = "") -> dict[str, str]:
    """The value of every shortcode for one person and one alert. A shortcode with nothing to show is an empty string."""
    v = {k: "" for k in SAMPLE}
    for k, x in (details or {}).items():
        if k in v and x is not None:
            v[k] = str(x)
    v.update({"customer": tenant_name, "camera": (details or {}).get("location", ""), "time": (details or {}).get("time", ""), "details": (details or {}).get("details", ""),
              "title": title or (details or {}).get("event", ""), "to_name": person_name or "", "to_phone": re.sub(r"\D", "", phone or ""), "event_type": event, "alert_id": str(alert_id),
              "image_url": image_url, "clip_url": clip_url, "document_url": document_url})
    return v


def classify(status: int, text: str) -> Result:
    if 200 <= status < 300:
        return Result(True, status=status, raw=text[:500])
    retry = status >= 500 or status in RETRY_STATUSES
    return Result(False, permanent=not retry, status=status, error=f"The webhook answered HTTP {status}.", raw=text[:500])


async def send(url: str, headers: dict[str, str], body: bytes, *, event: str, delivery_id: str, secret: str | None, test: bool = False, allow_private: bool = False,
               ssl_context: ssl.SSLContext | None = None, resolver=None) -> Result:
    """Sends one event. Never raises for a problem at the other end: returns a Result saying whether to try again."""
    try:
        target = await netguard.target_for(url, allow_private=allow_private, resolver=resolver)
    except netguard.BlockedAddress as e:
        return Result(False, permanent=True, error=str(e))
    ts = str(int(time.time()))
    out = {"Content-Type": "application/json", **headers, "X-Event": event, "X-Delivery-Id": delivery_id, "X-Timestamp": ts}
    if secret:
        out["X-Signature"] = sign(secret, ts, body)
    if test:
        out["X-Test"] = "true"
    ip = f"[{target.ip}]" if ":" in target.ip else target.ip
    out["Host"] = target.host if target.port == 443 else f"{target.host}:{target.port}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, verify=ssl_context or True, trust_env=False) as client:
            async with client.stream("POST", f"https://{ip}:{target.port}{target.path}", headers=out, content=body, extensions={"sni_hostname": target.host}) as r:
                chunks, size = [], 0
                async for c in r.aiter_bytes():
                    size += len(c)
                    chunks.append(c)
                    if size > MAX_RESPONSE:
                        break
                return classify(r.status_code, b"".join(chunks)[:MAX_RESPONSE].decode("utf-8", "replace"))
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return Result(False, permanent=False, error=f"Could not reach the webhook ({type(e).__name__}).")
