"""Password hashing and the rules a new password must meet.

scrypt from the standard library (memory-hard, no extra package). The format keeps its own parameters, so they can be raised later
and old hashes still verify. Hashing takes about 50 ms and 16 MB, so it runs in a thread and never blocks the server."""
import asyncio
import base64
import hashlib
import hmac
import os

from ..errors import ApiError

N, R, P = 2 ** 14, 8, 1
MIN_LENGTH = 10
_COMMON = {"password", "password1", "password12", "password123", "1234567890", "12345678901", "qwertyuiop", "qwerty12345", "iloveyou12",
           "welcome123", "admin12345", "letmein123", "gateinsight", "changeme123", "abcdefghij"}


def _b(x: bytes) -> str:
    return base64.b64encode(x).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${N}${R}${P}${_b(salt)}${_b(h)}"


def verify_password(password: str, stored: str | None) -> bool:
    """False for a wrong password and for a missing or unreadable hash. Always does the same work, so timing does not reveal which."""
    try:
        _, n, r, p, salt, want = (stored or DUMMY).split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, base64.b64decode(want)) and stored is not None
    except (ValueError, TypeError):
        return False


DUMMY = hash_password("not-a-real-password")      # checked against when an account does not exist, so unknown emails take as long as known ones


async def hash_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_async(password: str, stored: str | None) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)


def check_policy(password: str, email: str = "") -> None:
    """Long beats complicated: ten characters minimum, no common passwords, not the email. Raises a plain message a customer can act on."""
    if len(password) < MIN_LENGTH:
        raise ApiError(422, "weak_password", f"Use at least {MIN_LENGTH} characters.")
    if len(password) > 200:
        raise ApiError(422, "weak_password", "That password is too long.")
    low = password.lower()
    if low in _COMMON or (email and low in (email.lower(), email.lower().split("@")[0])) or len(set(low)) < 4:
        raise ApiError(422, "weak_password", "That password is too easy to guess. Choose a longer one that is not a common word.")
