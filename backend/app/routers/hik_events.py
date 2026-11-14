"""The private address a Hikvision recorder sends its fault messages to:  /hik/events/<code>.  No sign-in: the long secret code in the address is the proof.
A wrong code gets the same plain "not found" as any address that does not exist, and nothing is written. Many wrong codes from one address are slowed down (the web
server should limit this address too: see DEPLOY.md). The database is reached with the narrow public role only (services/hik_alerts.py)."""
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..deps import client_ip
from ..errors import ApiError
from ..services import hik_alerts

router = APIRouter(tags=["hikvision alerts"])


async def _body(request: Request) -> bytes:
    """The message, refusing anything too big before it is all read."""
    if int(request.headers.get("content-length") or 0) > hik_alerts.MAX_BODY:
        raise ApiError(413, "too_big", "Too big.")
    out = b""
    async for chunk in request.stream():
        out += chunk
        if len(out) > hik_alerts.MAX_BODY:
            raise ApiError(413, "too_big", "Too big.")
    return out


def _refuse_if_guessing(ip) -> None:
    if hik_alerts.unknown.blocked(ip):
        raise ApiError(429, "too_many_attempts", "Too many requests.")


@router.post("/hik/events/{code}", include_in_schema=False)
@router.put("/hik/events/{code}", include_in_schema=False)
async def receive(code: str, request: Request):
    ip = client_ip(request)
    _refuse_if_guessing(ip)
    body = await _body(request)
    if not await hik_alerts.receive(code, request.headers.get("content-type", ""), body, ip):
        hik_alerts.unknown.miss(ip)
        raise ApiError(404, "not_found", "Not found.")
    return PlainTextResponse("OK")


@router.get("/hik/events/{code}", include_in_schema=False)
async def check(code: str, request: Request):
    """Open this address from the recorder's network to see that it reaches us. Shows nothing about the customer."""
    ip = client_ip(request)
    _refuse_if_guessing(ip)
    if not await hik_alerts.exists(code):
        hik_alerts.unknown.miss(ip)
        raise ApiError(404, "not_found", "Not found.")
    return PlainTextResponse("Ready to receive alerts.")
