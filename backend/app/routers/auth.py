"""Sign-in routes for staff. Public: they are how a token is obtained. Attempts are limited per address, per email and per account."""
from fastapi import APIRouter, Request, Response

from ..config import get_settings
from ..db import db
from ..deps import client_ip
from ..errors import ApiError
from ..schemas import PasskeyLoginIn, RefreshIn, RememberIn, SetPasswordIn, StaffLoginIn
from ..services import auth, passkeys
from ..services.loginguard import guard

staff = APIRouter(prefix="/admin/v1/auth", tags=["sign-in"])


def _raise_if_error(result):
    if isinstance(result, ApiError):                    # raised only now, after the transaction committed (see services/auth.py)
        raise result
    return result


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@staff.post("/login")
async def staff_login(body: StaffLoginIn, request: Request, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.staff_login(conn, body.email, body.password, client_ip(request), body.remember)
    return _raise_if_error(result)


@staff.post("/refresh")
async def staff_refresh(body: RefreshIn, response: Response):
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.refresh(conn, body.refresh_token)
    return _raise_if_error(result)


@staff.post("/remember")
async def staff_remember(body: RememberIn, response: Response):
    """Switch "Stay signed in on this phone" on or off for the sign-in that holds this token. Answers with the new tokens, like a refresh."""
    _no_store(response)
    async with db.admin_tx() as conn:
        result = await auth.refresh(conn, body.refresh_token, body.remember)
    return _raise_if_error(result)


@staff.post("/passkey/options")
async def passkey_options(request: Request, response: Response):
    """Start a Face ID sign-in: a one-time challenge for the phone to sign."""
    _no_store(response)
    s = get_settings()
    guard.check(client_ip(request), "passkey-options", s.login_ip_limit, 10 ** 9, 1)
    async with db.admin_tx() as conn:
        return await passkeys.login_options(conn)


@staff.post("/passkey/login")
async def passkey_login(body: PasskeyLoginIn, request: Request, response: Response):
    """Finish a Face ID sign-in. The answer is checked against the public key saved when the phone was set up."""
    _no_store(response)
    s = get_settings()
    guard.check(client_ip(request), "passkey-login", s.login_ip_limit, 10 ** 9, 1)
    async with db.admin_tx() as conn:
        sid = await passkeys.check_login(conn, body.id, body.clientDataJSON, body.authenticatorData, body.signature)
        return await auth.passkey_login(conn, sid)


@staff.post("/logout", status_code=204)
async def staff_logout(body: RefreshIn):
    async with db.admin_tx() as conn:
        await auth.logout(conn, body.refresh_token)


@staff.post("/set-password", status_code=204)
async def staff_set_password(body: SetPasswordIn, request: Request):
    """From an invitation or reset link. The link works once."""
    s = get_settings()
    guard.check(client_ip(request), "staff-set-password", s.login_ip_limit, 10 ** 9, 1)
    async with db.admin_tx() as conn:
        await auth.set_staff_password_with_token(conn, body.token, body.password)
