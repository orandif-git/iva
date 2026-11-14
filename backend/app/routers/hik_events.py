"""The private address a Hikvision recorder sends its fault messages to:  /hik/events/<code>.  No sign-in: the long secret code in the address is the proof.
A wrong code gets the same plain "not found" as any address that does not exist, and nothing is written."""
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..db import db
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


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else None)


@router.post("/hik/events/{code}", include_in_schema=False)
@router.put("/hik/events/{code}", include_in_schema=False)
async def receive(code: str, request: Request):
    body = await _body(request)
    async with db.service_tx() as conn:
        if not await hik_alerts.receive(conn, code, request.headers.get("content-type", ""), body, _ip(request)):
            raise ApiError(404, "not_found", "Not found.")
    return PlainTextResponse("OK")


@router.get("/hik/events/{code}", include_in_schema=False)
async def check(code: str):
    """Open this address from the recorder's network to see that it reaches us. Shows nothing about the customer."""
    async with db.service_tx() as conn:
        if not await conn.fetchval("SELECT 1 FROM platform.alert_links WHERE code_hash=$1", hik_alerts.fingerprint(code)):
            raise ApiError(404, "not_found", "Not found.")
    return PlainTextResponse("Ready to receive alerts.")
