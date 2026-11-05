"""One-time codes from an authenticator app (RFC 6238: 6 digits, 30 seconds, SHA-1, which every authenticator app supports)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 0xF
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify(secret: str, code: str, last_step: int = 0, now: float | None = None) -> int | None:
    """The 30-second step the code belongs to, or None. Accepts one step either side for clock drift, and never a step that was already used."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    cur = int((time.time() if now is None else now) // STEP)
    for step in (cur, cur - 1, cur + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret: str, account: str, issuer: str = "Gate Insight") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period={STEP}"
