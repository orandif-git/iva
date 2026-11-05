"""Sign-in for customers and staff.

Customers: email and password. Staff: email, password AND a code from an authenticator app (the admin API requires `mfa` in the token),
unless the server is set to GI_STAFF_REQUIRE_MFA=false: then a password is enough, the token says `mfa: false`, and its refresh token starts with
`n_`. Turning two-step back on refuses those tokens at once (they cannot be upgraded); a refresh token without that prefix always came from a
sign-in that had the code.
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
from ..crypto import vault
from ..errors import ApiError
from ..security import issue_token
from . import passwords, totp
from .loginguard import guard

WRONG = ("wrong_credentials", "That email and password do not match.")


def _fail(status=401, code=None, msg=None):
    return ApiError(status, code or WRONG[0], msg or WRONG[1])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_refresh(conn, account_type: str, account_id, family=None, ttl: timedelta | None = None, mfa: bool = True) -> str:
    s = get_settings()
    raw = ("" if mfa else "n_") + secrets.token_urlsafe(32)          # the prefix is part of the secret: it cannot be removed without invalidating the token
    ttl = ttl or (timedelta(days=s.customer_refresh_days) if account_type == "customer" else timedelta(hours=s.staff_refresh_hours))
    await conn.execute(
        """INSERT INTO platform.auth_tokens (kind, account_type, account_id, family, token_hash, expires_at)
           VALUES ('refresh',$1,$2,$3,$4,$5)""",
        account_type, account_id, family or uuid.uuid4(), token_hash(raw), _now() + ttl)
    return raw


def _access(aud: str, claims: dict) -> str:
    return issue_token(aud, claims, get_settings().access_token_minutes * 60)


async def _tokens(conn, account_type: str, account_id, family=None, **claims) -> dict:
    aud = "customer" if account_type == "customer" else "platform"
    s = get_settings()
    return {"access_token": _access(aud, {"sub": str(account_id), **claims}), "token_type": "bearer", "expires_in": s.access_token_minutes * 60,
            "refresh_token": await _issue_refresh(conn, account_type, account_id, family, mfa=claims.get("mfa", True))}


async def _record_failure(conn, table: str, ids: list) -> None:
    s = get_settings()
    for i in ids:
        await conn.execute(
            f"""UPDATE {table} SET
                  locked_until = CASE WHEN failed_logins + 1 >= $2 THEN now() + make_interval(mins => $3) ELSE locked_until END,
                  failed_logins = CASE WHEN failed_logins + 1 >= $2 THEN 0 ELSE failed_logins + 1 END WHERE id = $1""",
            i, s.lockout_failures, s.lockout_minutes)


# ------------------------------------------------------------------------------------------- customers
async def customer_login(conn, email: str, password: str, tenant_slug: str | None, ip: str | None) -> dict | ApiError:
    s = get_settings()
    email = email.strip().lower()
    guard.check(ip, email, s.login_ip_limit, s.lockout_failures, s.lockout_minutes * 60)
    rows = await conn.fetch(
        """SELECT u.id, u.tenant_id, u.name, u.password_hash, u.status, u.locked_until, t.slug, t.name AS tenant_name, t.status::text AS tenant_status
           FROM app.tenant_users u JOIN app.tenants t ON t.id = u.tenant_id
           WHERE u.email = $1 AND ($2::text IS NULL OR t.slug = $2)""", email, tenant_slug)
    now = _now()
    if any(r["locked_until"] and r["locked_until"] > now for r in rows):
        guard.failed(email)
        raise ApiError(429, "too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
    ok, bad = [], []
    for r in rows:                                          # one password check per account; none at all when there are no accounts
        (ok if await passwords.verify_async(password, r["password_hash"]) and r["status"] == "active" else bad).append(r)
    if not rows:
        await passwords.verify_async(password, None)       # same work as a real check
    if not ok:
        guard.failed(email)
        await _record_failure(conn, "app.tenant_users", [r["id"] for r in bad])
        return _fail()
    if len(ok) > 1:
        return ApiError(409, "choose_account", "This email belongs to more than one organisation. Enter your organisation's short name as well.")
    u = ok[0]
    if u["tenant_status"] in ("suspended", "cancelled"):
        raise ApiError(403, "tenant_suspended", "This account is suspended. Contact support.")
    guard.succeeded(email)
    await conn.execute("UPDATE app.tenant_users SET failed_logins=0, locked_until=NULL, last_login_at=now() WHERE id=$1", u["id"])
    out = await _tokens(conn, "customer", u["id"], tenant_id=str(u["tenant_id"]))
    out["user"] = {"id": u["id"], "name": u["name"], "tenant": u["tenant_name"]}
    return out


# ---------------------------------------------------------------------------------------------- staff
async def staff_login(conn, email: str, password: str, code: str, ip: str | None) -> dict | ApiError:
    s = get_settings()
    email = email.strip().lower()
    guard.check(ip, email, s.login_ip_limit, s.lockout_failures, s.lockout_minutes * 60)
    r = await conn.fetchrow(
        """SELECT id, name, role::text AS role, status, password_hash, totp_secret_ciphertext, totp_last_step, locked_until
           FROM platform.staff_users WHERE email = $1""", email)
    if r and r["locked_until"] and r["locked_until"] > _now():
        guard.failed(email)
        raise ApiError(429, "too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
    pw_ok = await passwords.verify_async(password, r["password_hash"] if r else None)
    step = None
    if r and pw_ok and r["status"] == "active" and r["totp_secret_ciphertext"] and code:
        secret = vault().decrypt(bytes(r["totp_secret_ciphertext"]), f"totp:{r['id']}")
        step = totp.verify(secret, code, r["totp_last_step"])
    if not (r and pw_ok and r["status"] == "active" and (step or not s.staff_require_mfa)):
        guard.failed(email)
        if r:
            await _record_failure(conn, "platform.staff_users", [r["id"]])
        return _fail(msg="Wrong email, password or code." if s.staff_require_mfa else "That email and password do not match.")
    guard.succeeded(email)
    await conn.execute("UPDATE platform.staff_users SET failed_logins=0, locked_until=NULL, last_login_at=now(), totp_last_step=coalesce($2, totp_last_step) WHERE id=$1", r["id"], step)
    out = await _tokens(conn, "staff", r["id"], mfa=bool(step))                  # a valid code gives a full token even when it is not required
    out["user"] = {"id": r["id"], "name": r["name"], "role": r["role"]}
    return out


# -------------------------------------------------------------------------------- refresh and logout
async def refresh(conn, account_type: str, raw: str) -> dict | ApiError:
    row = await conn.fetchrow(
        "SELECT id, account_id, family, expires_at, used_at, revoked_at FROM platform.auth_tokens WHERE token_hash=$1 AND kind='refresh' AND account_type=$2",
        token_hash(raw), account_type)
    if not row or row["revoked_at"] or row["expires_at"] < _now():
        raise ApiError(401, "invalid_token", "Sign in again.")
    claimed = await conn.fetchval("UPDATE platform.auth_tokens SET used_at=now() WHERE id=$1 AND used_at IS NULL RETURNING id", row["id"])
    if claimed is None:                                   # used before: someone has a copy. End this sign-in everywhere.
        await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE family=$1 AND revoked_at IS NULL", row["family"])
        return ApiError(401, "invalid_token", "Sign in again.")
    if account_type == "customer":
        u = await conn.fetchrow("""SELECT u.id, u.tenant_id, u.status, t.status::text AS ts FROM app.tenant_users u JOIN app.tenants t ON t.id=u.tenant_id WHERE u.id=$1""", row["account_id"])
        if not u or u["status"] != "active" or u["ts"] in ("suspended", "cancelled"):
            raise ApiError(401, "invalid_token", "Sign in again.")
        claims = {"tenant_id": str(u["tenant_id"])}
    else:
        u = await conn.fetchrow("SELECT id, status FROM platform.staff_users WHERE id=$1", row["account_id"])
        if not u or u["status"] != "active":
            raise ApiError(401, "invalid_token", "Sign in again.")
        mfa_done = not raw.startswith("n_")
        if get_settings().staff_require_mfa and not mfa_done:     # signed in without a code while that was allowed: sign in again, with one
            return ApiError(401, "invalid_token", "Sign in again.")
        claims = {"mfa": mfa_done}
    return await _tokens(conn, account_type, row["account_id"], family=row["family"], **claims)


async def logout(conn, account_type: str, raw: str) -> None:
    row = await conn.fetchrow("SELECT family FROM platform.auth_tokens WHERE token_hash=$1 AND kind='refresh' AND account_type=$2", token_hash(raw), account_type)
    if row:
        await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE family=$1 AND revoked_at IS NULL", row["family"])


async def revoke_all(conn, account_type: str, account_id, except_family=None) -> None:
    await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE account_type=$1 AND account_id=$2 AND kind='refresh' AND revoked_at IS NULL AND ($3::uuid IS NULL OR family<>$3)",
                       account_type, account_id, except_family)


# ----------------------------------------------------------------------------- invites and resets
async def make_link_token(conn, kind: str, account_id, created_by, account_type: str = "customer") -> tuple[str, datetime]:
    """One invite or reset link per account at a time: a new one cancels the old. account_type is customer or staff."""
    s = get_settings()
    await conn.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE account_type=$2 AND account_id=$1 AND kind IN ('invite','reset') AND used_at IS NULL AND revoked_at IS NULL", account_id, account_type)
    raw = secrets.token_urlsafe(32)
    exp = _now() + (timedelta(days=s.invite_days) if kind == "invite" else timedelta(hours=s.reset_hours))
    await conn.execute("INSERT INTO platform.auth_tokens (kind, account_type, account_id, token_hash, created_by, expires_at) VALUES ($1,$6,$2,$3,$4,$5)",
                       kind, account_id, token_hash(raw), created_by, exp, account_type)
    return raw, exp


def link(raw: str, staff: bool = False) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/{'staff/' if staff else ''}set-password?token={raw}"


async def set_password_with_token(conn, raw: str, password: str) -> None:
    row = await conn.fetchrow("SELECT id, kind, account_id, expires_at, used_at, revoked_at FROM platform.auth_tokens WHERE token_hash=$1 AND kind IN ('invite','reset') AND account_type='customer'", token_hash(raw))
    if not row or row["used_at"] or row["revoked_at"] or row["expires_at"] < _now():
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask for a new one.")
    u = await conn.fetchrow("SELECT id, email, status FROM app.tenant_users WHERE id=$1", row["account_id"])
    if not u or u["status"] == "disabled":
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask for a new one.")
    passwords.check_policy(password, str(u["email"]))
    h = await passwords.hash_async(password)
    if await conn.fetchval("UPDATE platform.auth_tokens SET used_at=now() WHERE id=$1 AND used_at IS NULL RETURNING id", row["id"]) is None:
        raise ApiError(400, "link_invalid", "This link has expired or was already used. Ask for a new one.")
    await conn.execute("UPDATE app.tenant_users SET password_hash=$2, status='active', failed_logins=0, locked_until=NULL WHERE id=$1", u["id"], h)
    await revoke_all(conn, "customer", u["id"])             # a reset signs out every device


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
    await revoke_all(conn, "staff", u["id"])
