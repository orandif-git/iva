"""Sign-in for the staff of Jas iVision (the Control Center): email and password.

Access tokens are short (15 minutes). A refresh token lasts longer, can be used once, and is swapped for a new one each time; presenting
an old one again means it was copied, so the whole sign-in is revoked. Only hashes of refresh, invite and reset tokens are stored.

Failures that must leave a mark in the database (a wrong-password count, a revoked sign-in) are RETURNED as an ApiError, not raised: the router raises
them after the transaction has committed, because raising inside it would roll the mark back.

Every failure to sign in looks the same from outside (same message, same work done), so the page cannot be used to find out which
emails have accounts."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..errors import ApiError
from ..security import issue_token
from . import passwords
from .loginguard import guard

WRONG = ("wrong_credentials", "That email and password do not match.")


def _fail(status=401, code=None, msg=None):
    return ApiError(status, code or WRONG[0], msg or WRONG[1])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_refresh(conn, account_id, family=None, ttl: timedelta | None = None) -> str:
    s = get_settings()
    raw = secrets.token_urlsafe(32)
    ttl = ttl or timedelta(hours=s.staff_refresh_hours)
    await conn.execute(
        """INSERT INTO platform.auth_tokens (kind, account_type, account_id, family, token_hash, expires_at)
           VALUES ('refresh','staff',$1,$2,$3,$4)""",
        account_id, family or uuid.uuid4(), token_hash(raw), _now() + ttl)
    return raw


def _long(ttl: timedelta) -> bool:
    return ttl > timedelta(days=1)


async def _tokens(conn, account_id, family=None, ttl: timedelta | None = None) -> dict:
    s = get_settings()
    ttl = ttl or timedelta(hours=s.staff_refresh_hours)
    return {"access_token": issue_token("platform", {"sub": str(account_id)}, s.access_token_minutes * 60), "token_type": "bearer", "expires_in": s.access_token_minutes * 60,
            "refresh_token": await _issue_refresh(conn, account_id, family, ttl), "remember": _long(ttl)}


async def _record_failure(conn, table: str, ids: list) -> None:
    s = get_settings()
    for i in ids:
        await conn.execute(
            f"""UPDATE {table} SET
                  locked_until = CASE WHEN failed_logins + 1 >= $2 THEN now() + make_interval(mins => $3) ELSE locked_until END,
                  failed_logins = CASE WHEN failed_logins + 1 >= $2 THEN 0 ELSE failed_logins + 1 END WHERE id = $1""",
            i, s.lockout_failures, s.lockout_minutes)


async def staff_login(conn, email: str, password: str, ip: str | None, remember: bool = False) -> dict | ApiError:
    s = get_settings()
    email = email.strip().lower()
    guard.check(ip, email, s.login_ip_limit, s.lockout_failures, s.lockout_minutes * 60)
    r = await conn.fetchrow("SELECT id, name, role::text AS role, status, password_hash, locked_until FROM platform.staff_users WHERE email = $1", email)
    if r and r["locked_until"] and r["locked_until"] > _now():
        guard.failed(email)
        raise ApiError(429, "too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
    pw_ok = await passwords.verify_async(password, r["password_hash"] if r else None)          # the same work whether or not the email exists
    if not (r and pw_ok and r["status"] == "active"):
        guard.failed(email)
        if r:
            await _record_failure(conn, "platform.staff_users", [r["id"]])
        return _fail()
    guard.succeeded(email)
    await conn.execute("UPDATE platform.staff_users SET failed_logins=0, locked_until=NULL, last_login_at=now() WHERE id=$1", r["id"])
    out = await _tokens(conn, r["id"], ttl=timedelta(days=s.staff_remember_days) if remember else None)
    out["user"] = {"id": r["id"], "name": r["name"], "role": r["role"]}
    return out


# -------------------------------------------------------------------------------- refresh and logout
async def refresh(conn, raw: str, remember: bool | None = None) -> dict | ApiError:
    """Swap a refresh token for a new one. The sign-in keeps its length (12 hours, or 30 days when the person chose to stay signed in) and each use starts it again.
    `remember` True or False changes the length from now on (the switch under More)."""
    row = await conn.fetchrow(
        "SELECT id, account_id, family, created_at, expires_at, used_at, revoked_at FROM platform.auth_tokens WHERE token_hash=$1 AND kind='refresh' AND account_type='staff'", token_hash(raw))
    if not row or row["revoked_at"] or row["expires_at"] < _now():
        raise ApiError(401, "invalid_token", "Sign in again.")
    claimed = await conn.fetchval("UPDATE platform.auth_tokens SET used_at=now() WHERE id=$1 AND used_at IS NULL RETURNING id", row["id"])
    if claimed is None:                                   # used before: someone has a copy. End this sign-in everywhere.
        await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE family=$1 AND revoked_at IS NULL", row["family"])
        return ApiError(401, "invalid_token", "Sign in again.")
    u = await conn.fetchrow("SELECT id, status FROM platform.staff_users WHERE id=$1", row["account_id"])
    if not u or u["status"] != "active":
        raise ApiError(401, "invalid_token", "Sign in again.")
    s = get_settings()
    ttl = row["expires_at"] - row["created_at"]
    if remember is not None:
        ttl = timedelta(days=s.staff_remember_days) if remember else timedelta(hours=s.staff_refresh_hours)
    return await _tokens(conn, row["account_id"], family=row["family"], ttl=ttl)


async def logout(conn, raw: str) -> None:
    row = await conn.fetchrow("SELECT family FROM platform.auth_tokens WHERE token_hash=$1 AND kind='refresh' AND account_type='staff'", token_hash(raw))
    if row:
        await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE family=$1 AND revoked_at IS NULL", row["family"])


async def revoke_all(conn, account_id, except_family=None) -> None:
    await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE account_type='staff' AND account_id=$1 AND kind='refresh' AND revoked_at IS NULL AND ($2::uuid IS NULL OR family<>$2)",
                       account_id, except_family)


# ----------------------------------------------------------------------------- invitations and resets
async def make_link_token(conn, kind: str, account_id, created_by) -> tuple[str, datetime]:
    """One invitation or reset link per person at a time: a new one cancels the old."""
    s = get_settings()
    await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE account_type='staff' AND account_id=$1 AND kind IN ('invite','reset') AND used_at IS NULL AND revoked_at IS NULL", account_id)
    raw = secrets.token_urlsafe(32)
    exp = _now() + (timedelta(days=s.invite_days) if kind == "invite" else timedelta(hours=s.reset_hours))
    await conn.execute("INSERT INTO platform.auth_tokens (kind, account_type, account_id, token_hash, created_by, expires_at) VALUES ($1,'staff',$2,$3,$4,$5)",
                       kind, account_id, token_hash(raw), created_by, exp)
    return raw, exp


def link(raw: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/set-password?token={raw}"


async def set_staff_password_with_token(conn, raw: str, password: str) -> None:
    """The same one-time link for a staff member: their first password, or a new one."""
    row = await conn.fetchrow("SELECT id, account_id, expires_at, used_at, revoked_at FROM platform.auth_tokens WHERE token_hash=$1 AND kind IN ('invite','reset') AND account_type='staff'", token_hash(raw))
    if not row or row["used_at"] or row["revoked_at"] or row["expires_at"] < _now():
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask an owner for a new one.")
    u = await conn.fetchrow("SELECT id, email, status FROM platform.staff_users WHERE id=$1", row["account_id"])
    if not u or u["status"] != "active":
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask an owner for a new one.")
    passwords.check_policy(password, str(u["email"]))
    h = await passwords.hash_async(password)
    if await conn.fetchval("UPDATE platform.auth_tokens SET used_at=now() WHERE id=$1 AND used_at IS NULL RETURNING id", row["id"]) is None:
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask an owner for a new one.")
    await conn.execute("UPDATE platform.staff_users SET password_hash=$2, failed_logins=0, locked_until=NULL WHERE id=$1", u["id"], h)
    await revoke_all(conn, u["id"])
