"""Authentication and per-request database context.

Rules enforced here for every route:
  - Staff and service credentials are different token audiences and never interchange.
  - Roles are read from the database, not trusted from the token, so a disabled or demoted person loses access at once.
"""
import hmac
import uuid
from dataclasses import dataclass

from fastapi import Request

from .config import get_settings
from .db import db
from .errors import ApiError
from .security import verify_token

STAFF_ROLES = ("owner", "admin", "onboarding", "support")


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer ") or len(h) < 15:
        raise ApiError(401, "missing_token", "Sign in to continue.")
    return h[7:].strip()


def _uuid(value, what: str = "token") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ApiError(401, "invalid_token", "Sign in again.")


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ----------------------------------------------------------------- platform admin
@dataclass
class Staff:
    id: uuid.UUID
    name: str
    role: str


@dataclass
class AdminCtx:
    conn: object
    staff: Staff
    request_id: str
    ip: str | None

    async def audit(self, action, *, tenant_id=None, target_type=None, target_id=None, reason=None, before=None, after=None):
        await self.conn.execute(
            """INSERT INTO platform.audit_log
               (actor_type, actor_id, actor_name, actor_role, tenant_id, action, target_type, target_id, reason, before, after, ip, request_id)
               VALUES ('staff',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::inet,$12)""",
            self.staff.id, self.staff.name, self.staff.role, tenant_id, action, target_type,
            str(target_id) if target_id is not None else None, reason, before, after, self.ip, self.request_id,
        )


def admin_ctx(*roles: str):
    """Who may call an endpoint. `owner` also lets in `admin` (the Owner's trusted manager); `owner!` means the Owner and nobody else (the WhatsApp connection and key)."""
    allowed = set(roles) or set(STAFF_ROLES)
    if "owner!" in allowed:
        allowed = {"owner"}
    elif "owner" in allowed:
        allowed.add("admin")

    async def dep(request: Request):
        claims = verify_token("platform", _bearer(request))
        staff_id = _uuid(claims["sub"])
        async with db.admin_tx() as conn:
            row = await conn.fetchrow("SELECT id, name, role::text AS role, status FROM platform.staff_users WHERE id=$1", staff_id)
            if not row or row["status"] != "active":
                raise ApiError(401, "invalid_token", "Sign in again.")
            if row["role"] not in allowed:
                raise ApiError(403, "forbidden", "Your role cannot do this.")
            yield AdminCtx(conn, Staff(row["id"], row["name"], row["role"]), request.state.request_id, client_ip(request))

    return dep


# ----------------------------------------------------------------------- one customer's data, seen by staff (see routers/admin_shop.py)
@dataclass
class CustomerCtx:
    conn: object
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    role: str
    request_id: str
    all_kinds: bool = False        # the Control Center sees every kind of marked area

    async def audit(self, action, target_type=None, target_id=None, after=None, reason=None):
        await self.conn.execute(
            "SELECT platform.audit_from_app($1,$2,$3,$4,$5)",
            action, target_type, str(target_id) if target_id is not None else None, after, reason,
        )


# ------------------------------------------------------------------------ service
@dataclass
class ServiceCtx:
    conn: object
    request_id: str


async def service_ctx(request: Request):
    presented = _bearer(request)
    if not hmac.compare_digest(presented.encode(), get_settings().service_token.encode()):
        raise ApiError(401, "invalid_token", "Not allowed.")
    async with db.service_tx() as conn:
        yield ServiceCtx(conn, request.state.request_id)
