import hashlib
import hmac
import time

import jwt

from .config import get_settings
from .errors import ApiError

_ISS = "gate-insight"


def _secret(aud: str) -> str:
    s = get_settings()
    if aud == "ticket":              # the short note handed out between the password and the Face ID step: its own key, so it can never pass as a sign-in token
        return hmac.new(s.jwt_secret_platform.encode(), b"second-step-ticket-v1", hashlib.sha256).hexdigest()
    return {"platform": s.jwt_secret_platform}[aud]


def issue_token(aud: str, claims: dict, ttl_seconds: int) -> str:
    """Production note: replace with tokens from your identity provider (RS256 + JWKS). The checks in verify_token stay."""
    now = int(time.time())
    payload = {**claims, "aud": aud, "iss": _ISS, "iat": now, "exp": now + int(ttl_seconds)}
    return jwt.encode(payload, _secret(aud), algorithm="HS256")


def verify_token(aud: str, token: str) -> dict:
    try:
        return jwt.decode(
            token, _secret(aud), algorithms=["HS256"], audience=aud, issuer=_ISS,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        raise ApiError(401, "invalid_token", "Sign in again.")
