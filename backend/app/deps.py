"""Authentication and per-request database context.

Rules enforced here for every route:
  - Staff, customer and service credentials are different token audiences and never interchange.
  - Roles are read from the database, not trusted from the token, so a disabled or demoted user loses access at once.
  - The tenant id comes only from the verified token. Routes never read it from the URL or body in the customer API.
  - View-as-customer sessions are read-only twice over: non-GET requests are refused, and the transaction is READ ONLY.
"""
import hmac
import uuid
from dataclasses import dataclass

from fastapi import Request

from .config import get_settings
from .db import db
from .errors import ApiError
from .security import verify_token

STAFF_ROLES = ("owner", "support", "technician", "finance", "analyst")


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
    allowed = set(roles) or set(STAFF_ROLES)

    async def dep(request: Request):
        claims = verify_token("platform", _bearer(request))
        if claims.get("mfa") is not True:
            raise ApiError(401, "mfa_required", "Complete multi-factor sign-in.")
        staff_id = _uuid(claims["sub"])
        async with db.admin_tx() as conn:
            row = await conn.fetchrow("SELECT id, name, role::text AS role, status FROM platform.staff_users WHERE id=$1", staff_id)
            if not row or row["status"] != "active":
                raise ApiError(401, "invalid_token", "Sign in again.")
            if row["role"] not in allowed:
                raise ApiError(403, "forbidden", "Your role cannot do this.")
            yield AdminCtx(conn, Staff(row["id"], row["name"], row["role"]), request.state.request_id, client_ip(request))

    return dep


# ----------------------------------------------------------------------- customer
@dataclass
class CustomerCtx:
    conn: object
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    role: str
    viewing_as_staff: bool
    request_id: str

    async def audit(self, action, target_type=None, target_id=None, after=None, reason=None):
        await self.conn.execute(
            "SELECT platform.audit_from_app($1,$2,$3,$4,$5)",
            action, target_type, str(target_id) if target_id is not None else None, after, reason,
        )


def customer_ctx(*roles: str):
    allowed = set(roles) or {"admin", "regular"}

    async def dep(request: Request):
        claims = verify_token("customer", _bearer(request))
        tenant_id = _uuid(claims.get("tenant_id"))
        sub = _uuid(claims["sub"])
        imp_id = claims.get("imp")

        staff_name = None
        if imp_id:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                raise ApiError(403, "read_only_session", "This is a view-only session. Changes are blocked.")
            async with db.admin_tx() as c:
                s = await c.fetchrow(
                    """SELECT st.name FROM platform.impersonation_sessions s
                       JOIN platform.staff_users st ON st.id = s.staff_id
                       WHERE s.id=$1 AND s.tenant_id=$2 AND s.staff_id=$3
                         AND s.ended_at IS NULL AND s.expires_at > now() AND st.status='active'""",
                    _uuid(imp_id), tenant_id, sub,
                )
            if not s:
                raise ApiError(401, "session_ended", "This view-only session has ended.")
            staff_name = s["name"]

        settings = {
            "app.actor_id": str(sub),
            "app.actor_name": staff_name or "",
            "app.actor_role": "staff_view" if imp_id else "",
            "app.request_id": request.state.request_id,
        }
        async with db.tenant_tx(tenant_id, read_only=bool(imp_id), settings=settings) as conn:
            tenant = await conn.fetchrow("SELECT status::text AS status FROM app.tenants WHERE id=$1", tenant_id)
            if not tenant:
                raise ApiError(401, "invalid_token", "Sign in again.")
            if imp_id:
                name, role = staff_name, "admin"
            else:
                u = await conn.fetchrow("SELECT name, role::text AS role, status FROM app.tenant_users WHERE id=$1", sub)
                if not u or u["status"] != "active":
                    raise ApiError(401, "invalid_token", "Sign in again.")
                name, role = u["name"], u["role"]
                if tenant["status"] in ("suspended", "cancelled"):
                    raise ApiError(403, "tenant_suspended", "This account is suspended. Contact support.")
                await conn.execute("SELECT set_config('app.actor_name', $1, true), set_config('app.actor_role', $2, true)", name, role)
            if role not in allowed:
                raise ApiError(403, "forbidden", "Only an administrator can do this.")
            yield CustomerCtx(conn, tenant_id, sub, name, role, bool(imp_id), request.state.request_id)

    return dep


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
