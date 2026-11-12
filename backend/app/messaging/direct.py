"""Sends one WhatsApp message straight to the provider's Direct Send address: the text is written here, no template or webhook is involved.

    POST {url}   Authorization: Bearer {key}   {"source": "...", "to": "919876543210", "content": "...", "category": "utility"}

The provider answers 2xx (it has accepted the message) with a message_id, and delivers a little later. A 4xx answer is final; a 5xx, a refusal to slow down or a
connection failure is worth trying again. Nothing here raises for a problem at the other end."""
import json
import re

import httpx

from . import netguard
from .webhook import MAX_RESPONSE, Result, classify


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


async def send(cfg: dict, key: str, to: str, content: str, *, allow_private: bool = False, ssl_context=None, resolver=None) -> Result:
    try:
        target = await netguard.target_for(cfg["url"], allow_private=allow_private, resolver=resolver)
    except netguard.BlockedAddress as e:
        return Result(False, permanent=True, error=str(e))
    body = json.dumps({"source": cfg["source"], "to": phone_digits(to), "content": content, "category": cfg["category"]}, ensure_ascii=False).encode("utf-8")
    ip = f"[{target.ip}]" if ":" in target.ip else target.ip
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "Host": target.host if target.port == 443 else f"{target.host}:{target.port}"}
    timeout = httpx.Timeout(float(cfg["timeout"]), connect=min(5.0, float(cfg["timeout"])))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, verify=ssl_context or True, trust_env=False) as client:
            async with client.stream("POST", f"https://{ip}:{target.port}{target.path}", headers=headers, content=body, extensions={"sni_hostname": target.host}) as r:
                chunks, size = [], 0
                async for c in r.aiter_bytes():
                    size += len(c)
                    chunks.append(c)
                    if size > MAX_RESPONSE:
                        break
                text = b"".join(chunks)[:MAX_RESPONSE].decode("utf-8", "replace")
                res = classify(r.status_code, text)
                if res.ok:
                    try:
                        res.message_id = str((json.loads(text) or {}).get("message_id") or "")[:200] or None
                    except ValueError:
                        pass
                elif res.status:
                    res.error = f"The provider answered HTTP {res.status}."
                return res
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return Result(False, permanent=False, error=f"Could not reach the provider ({type(e).__name__}).")
