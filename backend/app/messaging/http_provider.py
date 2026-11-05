"""The configurable HTTP integration: one recipe, written in the admin console, describes how to call any messaging provider.

Recipe (config):
  method           POST (default) or PUT
  url              https://...   {{variables}} here are percent-encoded
  headers          {"Authorization": "Bearer {{secret.api_key}}"}
  body             any JSON, with {{variables}} in string values (see render.py)
  success          {"status": [200, 201, 202], "json": {"path": "status", "in": ["queued"]}}   both parts optional
  message_id_path  where the provider's message id is in the answer, for example  messages.0.id
  error_path       where to read an error text from, for example  error.message
  retry_statuses   extra statuses worth retrying (429, 408 and every 5xx always are)
  webhook          how the provider reports delivery (see parse_webhook)
"""
import hashlib
import hmac
import json
import ssl
from dataclasses import dataclass

import httpx

from . import netguard, render
from .paths import check_rule, dig

MAX_RESPONSE = 64 * 1024
TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
ALWAYS_RETRY = {408, 425, 429}


@dataclass
class Result:
    ok: bool
    permanent: bool = False            # when not ok: True = do not retry
    message_id: str | None = None
    error: str | None = None
    status: int | None = None
    raw: str | None = None


def validate_config(cfg: dict, secret_names: set[str], *, allow_private: bool = False) -> None:
    """Raises render.RenderError (or netguard.BlockedAddress) with a message an administrator can act on."""
    if not isinstance(cfg, dict):
        raise render.RenderError("the recipe must be an object")
    if cfg.get("method", "POST") not in ("POST", "PUT"):
        raise render.RenderError("method must be POST or PUT")
    url = cfg.get("url")
    if not isinstance(url, str):
        raise render.RenderError("url is required")
    render.validate_structure(url, secret_names, "url")
    probe = render.fill(url, {}, encode=True)
    netguard.check_url(probe if probe.startswith("https://") else probe, allow_private=allow_private)
    hdrs = cfg.get("headers", {})
    if not isinstance(hdrs, dict):
        raise render.RenderError("headers must be an object")
    for k, v in hdrs.items():
        if not render.TOKEN.match(k) or k.lower() in render.FORBIDDEN_HEADERS:
            raise render.RenderError(f"header '{k}' is not allowed")
        render.validate_structure(str(v), secret_names, f"headers.{k}")
    if "body" in cfg:
        render.validate_structure(cfg["body"], secret_names, "body")
    st = cfg.get("success", {}).get("status", [])
    if not isinstance(st, list) or not all(isinstance(x, int) and 100 <= x < 600 for x in st):
        raise render.RenderError("success.status must be a list of HTTP status numbers")
    for key in ("message_id_path", "error_path"):
        if key in cfg and not isinstance(cfg[key], str):
            raise render.RenderError(f"{key} must be text")
    if "webhook" in cfg:
        validate_webhook(cfg["webhook"], secret_names)


def validate_webhook(w: dict, secret_names: set[str]) -> None:
    if not isinstance(w, dict):
        raise render.RenderError("webhook must be an object")
    auth = w.get("auth", {})
    if auth.get("type") not in ("header_equals", "hmac_sha256") or not auth.get("header") or auth.get("secret") not in secret_names:
        raise render.RenderError("webhook.auth needs a type (header_equals or hmac_sha256), a header, and the name of a stored secret")
    for key in ("id_path", "status_path"):
        if not isinstance(w.get(key), str):
            raise render.RenderError(f"webhook.{key} is required")
    sm = w.get("status_map")
    allowed = {"sent", "delivered", "read", "failed"}
    if not isinstance(sm, dict) or not sm or not set(sm.values()) <= allowed:
        raise render.RenderError("webhook.status_map must map the provider's words to: sent, delivered, read, failed")


def build_context(msg: dict, secrets: dict) -> dict:
    """msg: recipient{phone,name,email}, template{name,language}, vars[list], media_url, link_url, link_token, message_id, alert{...}"""
    r, t, a = msg.get("recipient", {}), msg.get("template", {}), msg.get("alert", {})
    phone = r.get("phone") or ""
    values = {"recipient.phone": phone, "recipient.phone_digits": phone.lstrip("+"), "recipient.name": r.get("name") or "",
              "recipient.email": r.get("email") or "", "template.name": t.get("name", ""), "template.language": t.get("language", "en"),
              "media.url": msg.get("media_url") or "", "media.type": (msg.get("media_type") or "") if msg.get("media_url") else "",
              "media.is_image": "1" if msg.get("media_url") and msg.get("media_type") == "image" else "",
              "media.is_video": "1" if msg.get("media_url") and msg.get("media_type") == "video" else "", "link.url": msg.get("link_url") or "", "link.token": msg.get("link_token") or "",
              "message.id": msg.get("message_id") or "", "alert.title": a.get("title", ""), "alert.body": a.get("body", ""),
              "alert.camera": a.get("camera", ""), "alert.time": a.get("time", "")}
    return {"values": values, "vars": [str(v) for v in msg.get("vars", [])], "secrets": secrets}


def prepare(cfg: dict, ctx: dict) -> tuple[str, str, dict, bytes | None]:
    method = cfg.get("method", "POST")
    url = render.fill(cfg["url"], ctx, encode=True)
    headers = render.render_headers(cfg.get("headers", {}), ctx)
    body = render.render_body(cfg["body"], ctx) if "body" in cfg else None
    if body is not None and not any(h.lower() == "content-type" for h in headers):
        headers["Content-Type"] = "application/json"
    return method, url, headers, body


def classify(cfg: dict, status: int, text: str, ctx: dict) -> Result:
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    ok_status = cfg.get("success", {}).get("status") or list(range(200, 300))
    ok = status in ok_status
    rule = cfg.get("success", {}).get("json")
    if ok and rule:
        ok = data is not None and check_rule(data, rule)
    err = None
    if cfg.get("error_path") and data is not None:
        e = dig(data, cfg["error_path"])
        err = None if e is None else str(e)[:300]
    if ok:
        mid = dig(data, cfg["message_id_path"]) if cfg.get("message_id_path") and data is not None else None
        return Result(True, message_id=None if mid is None else str(mid), status=status, raw=text[:500])
    retry = status >= 500 or status in ALWAYS_RETRY or status in cfg.get("retry_statuses", [])
    msg = render.mask_secrets(err or f"The provider answered HTTP {status}.", ctx)
    return Result(False, permanent=not retry, error=msg, status=status, raw=render.mask_secrets(text[:500], ctx))


async def send(cfg: dict, secrets: dict, msg: dict, *, allow_private: bool = False, ssl_context: ssl.SSLContext | None = None,
               resolver=None, dry_run: bool = False):
    """Sends one message. Never raises for a provider problem: returns a Result. With dry_run it returns the request that would be sent."""
    ctx = build_context(msg, secrets)
    try:
        method, url, headers, body = prepare(cfg, ctx)
        if dry_run:
            return {"method": method, "url": url, "headers": {k: render.mask_secrets(v, ctx) for k, v in headers.items()},
                    "body": json.loads(render.mask_secrets(body.decode(), ctx)) if body else None}
        target = await netguard.target_for(url, allow_private=allow_private, resolver=resolver)
    except (render.RenderError, netguard.BlockedAddress) as e:
        return Result(False, permanent=True, error=render.mask_secrets(str(e), ctx))
    ip = f"[{target.ip}]" if ":" in target.ip else target.ip
    pinned = f"https://{ip}:{target.port}{target.path}"
    headers = {**headers, "Host": target.host if target.port == 443 else f"{target.host}:{target.port}"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, verify=ssl_context or True, trust_env=False) as client:
            async with client.stream(method, pinned, headers=headers, content=body, extensions={"sni_hostname": target.host}) as r:
                chunks, size = [], 0
                async for c in r.aiter_bytes():
                    size += len(c)
                    chunks.append(c)
                    if size > MAX_RESPONSE:
                        break
                text = b"".join(chunks)[:MAX_RESPONSE].decode("utf-8", "replace")
                return classify(cfg, r.status_code, text, ctx)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return Result(False, permanent=False, error=render.mask_secrets(f"Could not reach the provider ({type(e).__name__}).", ctx))


# ------------------------------------------------------------------------ status webhook
def verify_webhook(cfg: dict, secrets: dict, headers, raw_body: bytes) -> bool:
    auth = cfg["webhook"]["auth"]
    secret = str(secrets.get(auth["secret"], ""))
    presented = headers.get(auth["header"], "")
    if not secret or not presented:
        return False
    if auth["type"] == "header_equals":
        return hmac.compare_digest(presented.encode(), secret.encode())
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    prefix = auth.get("prefix", "")
    if prefix and presented.startswith(prefix):
        presented = presented[len(prefix):]
    return hmac.compare_digest(presented.lower().encode(), expected.encode())


def parse_webhook(cfg: dict, payload) -> list[dict]:
    """Returns [{message_id, status, error}] using the recipe's paths. The payload may hold one event or a list (events_path)."""
    w = cfg["webhook"]
    events = dig(payload, w["events_path"], []) if w.get("events_path") else [payload]
    out = []
    for ev in events if isinstance(events, list) else []:
        mid, st = dig(ev, w["id_path"]), dig(ev, w["status_path"])
        mapped = w["status_map"].get(str(st)) if st is not None else None
        if mid is None or mapped is None:
            continue
        err = dig(ev, w["error_path"]) if w.get("error_path") else None
        out.append({"message_id": str(mid), "status": mapped, "error": None if err is None else str(err)[:300]})
    return out
