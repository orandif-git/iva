"""Sign-in routes. Public: they are how a token is obtained. Sign-in attempts are limited per address, per email and per account."""
from fastapi import APIRouter, Depends, Request, Response

from ..db import db
from ..deps import CustomerCtx, client_ip, customer_ctx
from ..errors import ApiError
from ..schemas import ChangePasswordIn, LoginIn, RefreshIn, SetPasswordIn, StaffLoginIn
from ..services import auth, passwords

customer = APIRouter(prefix="/v1/auth", tags=["sign-in"])
staff = APIRouter(prefix="/admin/v1/auth", tags=["sign-in"])


def _raise_if_error(result):
    if isinstance(result, ApiError):                    # raised only now, after the transaction committed (see services/auth.py)
        raise result
    return result


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@customer.post("/login")
async def customer_login(body: LoginIn, request: Request, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.customer_login(conn, body.email, body.password, body.tenant, client_ip(request))
    return _raise_if_error(result)


@customer.post("/refresh")
async def customer_refresh(body: RefreshIn, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.refresh(conn, "customer", body.refresh_token)
    return _raise_if_error(result)


@customer.post("/logout", status_code=204)
async def customer_logout(body: RefreshIn):
    async with db.admin_tx() as conn:
        await auth.logout(conn, "customer", body.refresh_token)


@customer.post("/set-password", status_code=204)
async def set_password(body: SetPasswordIn, request: Request):
    """From an invitation or a reset link. The link works once."""
    from ..config import get_settings
    from ..services.loginguard import guard
    s = get_settings()
    guard.check(client_ip(request), "set-password", s.login_ip_limit, 10 ** 9, 1)       # per-address limit only
    async with db.admin_tx() as conn:
        await auth.set_password_with_token(conn, body.token, body.password)


@customer.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordIn, ctx: CustomerCtx = Depends(customer_ctx())):
    if ctx.viewing_as_staff:
        raise ApiError(403, "read_only_session", "This is a view-only session.")
    async with db.admin_tx() as conn:
        u = await conn.fetchrow("SELECT id, email, password_hash FROM app.tenant_users WHERE id=$1 AND tenant_id=$2", ctx.user_id, ctx.tenant_id)
        if not u or not await passwords.verify_async(body.current_password, u["password_hash"]):
            result = ApiError(401, "wrong_credentials", "Your current password is not right.")
        else:
            passwords.check_policy(body.new_password, str(u["email"]))
            await conn.execute("UPDATE app.tenant_users SET password_hash=$2 WHERE id=$1", u["id"], await passwords.hash_async(body.new_password))
            await auth.revoke_all(conn, "customer", u["id"])            # other devices sign in again
            result = None
    if result:
        raise result


@staff.get("/options")
async def staff_options():
    """Whether the staff sign-in page should ask for an authenticator code."""
    from ..config import get_settings
    return {"mfa_required": get_settings().staff_require_mfa}


@staff.post("/login")
async def staff_login(body: StaffLoginIn, request: Request, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.staff_login(conn, body.email, body.password, body.code or "", client_ip(request))
    return _raise_if_error(result)


@staff.post("/refresh")
async def staff_refresh(body: RefreshIn, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.refresh(conn, "staff", body.refresh_token)
    return _raise_if_error(result)


@staff.post("/logout", status_code=204)
async def staff_logout(body: RefreshIn):
    async with db.admin_tx() as conn:
        await auth.logout(conn, "staff", body.refresh_token)
