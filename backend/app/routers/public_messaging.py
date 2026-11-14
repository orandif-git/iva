"""The route reached without a login:  /e/<token>  (the evidence a person was told about).

It is protected by an unguessable signed token that expires."""
import html
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ..db import db
from ..errors import ApiError
from ..messaging import evidence

router = APIRouter(tags=["public"])
SAFE = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    csp = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    h = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1c2330}}main{{max-width:640px;margin:0 auto;padding:16px}}
.card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px #0002}}img{{width:100%;border-radius:8px;display:block}}h1{{font-size:18px;margin:0 0 4px}}
p{{margin:4px 0;color:#566}}small{{color:#889}}</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(h, status_code=status, headers={**SAFE, "Content-Security-Policy": csp})


async def _load(token: str):
    parsed = evidence.read_token(token)
    if parsed is None:
        raise ApiError(404, "not_found", "Not found.")
    eid, expired = parsed
    if expired:
        return None, "expired"
    async with db.edge_tx() as conn:
        row = await conn.fetchrow("SELECT * FROM platform.edge_evidence($1)", eid)
    return row, ("gone" if not row else None)


def _ranged(data: bytes, request: Request, content_type: str) -> Response:
    """Video players ask for byte ranges (to seek, and some will not start without them), so a single range is honoured."""
    headers = {**SAFE, "Accept-Ranges": "bytes", "Content-Disposition": "inline"}
    spec = request.headers.get("range", "")
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", spec.strip()) if spec else None
    size = len(data)
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start, end = int(m.group(1)), (int(m.group(2)) if m.group(2) else size - 1)
        else:
            start, end = max(0, size - int(m.group(2))), size - 1
        end = min(end, size - 1)
        if start >= size or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        return Response(data[start:end + 1], status_code=206, media_type=content_type, headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
    return Response(data, media_type=content_type, headers=headers)


@router.api_route("/e/{token}", methods=["GET", "HEAD"], include_in_schema=False)
async def view_evidence(token: str):
    row, problem = await _load(token)
    if problem:
        return _page("Link expired", "<div class='card'><h1>This link has expired</h1><p>For security, evidence links only work for a few days. Please open the Jas iVision app.</p></div>", 410)
    from zoneinfo import ZoneInfo
    when = row["created_at"].astimezone(ZoneInfo(row["timezone"])).strftime("%d %b %Y, %I:%M %p")
    t = html.escape(token)
    has_image = row["kind"] == "image"
    if row["clip_id"]:
        poster = f" poster='/e/{t}/image.jpg'" if has_image else ""
        media = f"<video controls playsinline preload='metadata'{poster} src='/e/{t}/clip.mp4'>Your browser cannot play this video.</video>"
        if has_image:
            media += f"<p><small><a href='/e/{t}/image.jpg'>Open the still picture</a></small></p>"
    else:
        media = f"<img src='/e/{t}/image.jpg' alt='Evidence picture'>" if has_image else "<p>The video is still being prepared. Please try again in a minute.</p>"
    body = (f"<div class='card'><h1>{html.escape(row['title'] or 'Alert')}</h1><p>{html.escape(row['camera'] or '')} · {html.escape(when)}</p>"
            f"<p>{html.escape(row['body'] or '')}</p><br>{media}<p><small>{html.escape(row['company'])} · Jas iVision</small></p></div>")
    return _page("Evidence", body)


@router.api_route("/e/{token}/image.jpg", methods=["GET", "HEAD"], include_in_schema=False)
async def evidence_image(token: str):
    row, problem = await _load(token)
    if problem or row["kind"] != "image":
        return Response(status_code=410 if problem else 404, headers=SAFE)
    data = await evidence.store().get(row["storage_key"])
    if data is None:
        return Response(status_code=410, headers=SAFE)
    return Response(data, media_type=row["content_type"], headers={**SAFE, "Content-Disposition": "inline"})


@router.api_route("/e/{token}/clip.mp4", methods=["GET", "HEAD"], include_in_schema=False)
async def evidence_clip(token: str, request: Request):
    row, problem = await _load(token)
    if problem:
        return Response(status_code=410, headers=SAFE)
    key = row["storage_key"] if row["kind"] == "clip" else row["clip_key"]
    if not key:
        return Response(status_code=404, headers=SAFE)
    data = await evidence.store().get(key)
    if data is None:
        return Response(status_code=410, headers=SAFE)
    return _ranged(data, request, "video/mp4")


@router.api_route("/e/{token}/report.xlsx", methods=["GET", "HEAD"], include_in_schema=False)
async def report_file(token: str):
    """The Excel file of a report that was announced on WhatsApp. The link works for a few hours (the owner's link setting), then the app is the way in."""
    parsed = evidence.read_token(token)
    if parsed is None:
        return Response(status_code=404, headers=SAFE)
    rid, expired = parsed
    if expired:
        return _page("Link expired", "<div class='card'><h1>This link has expired</h1><p>For security, report links only work for a few hours. Please open the Jas iVision app and look under Reports.</p></div>", 410)
    from ..services.reports import filename
    async with db.edge_tx() as conn:
        r = await conn.fetchrow("SELECT * FROM platform.edge_report($1)", rid)
    if not r or not r["file_key"]:
        return Response(status_code=404, headers=SAFE)
    data = await evidence.store().get(r["file_key"])
    if data is None:
        return Response(status_code=410, headers=SAFE)
    name = filename(r["report_type"], r["period_start"], r["period_end"], r["timezone"])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={**SAFE, "Content-Disposition": f'attachment; filename="{name}"'})
