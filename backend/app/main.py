import asyncio
import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import errors
from .config import get_settings
from .db import db
from .routers import (auth as auth_routes, admin_users, admin_billing, admin_framerate, admin_maintenance, admin_messaging, admin_misc, admin_recorders, admin_rtsp, admin_tenants, customer_alerts, customer_billing, customer_cameras, customer_events,
                      customer_recorders, customer_reports, internal, internal_evidence, internal_events, public_messaging)
from .messaging import delivery
from .services import billing, maintenance, reports, sweeper

logging.basicConfig(level=logging.INFO)

# The web pages load only their own scripts and styles and talk only to this server, so a page cannot be made to load or send anything elsewhere.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
_DOCS = ("/docs", "/openapi.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await db.init(s.database_url, s.db_pool_min, s.db_pool_max)
    tasks = [asyncio.create_task(sweeper.run_forever()), asyncio.create_task(maintenance.run_forever()), asyncio.create_task(delivery.run_forever()), asyncio.create_task(reports.run_forever()), asyncio.create_task(billing.run_forever())]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await db.close()


def create_app(use_lifespan: bool = True) -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Jas iVision API", version="0.1.0", lifespan=lifespan if use_lifespan else None,
                  docs_url="/docs" if s.env == "dev" else None, redoc_url=None, openapi_url="/openapi.json" if s.env == "dev" else None)
    errors.register(app)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(_DOCS):
            response.headers.setdefault("X-Frame-Options", "DENY")
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers.setdefault("Content-Security-Policy", CSP)         # a page with its own policy (the evidence viewer plays video) keeps it
            if not request.url.path.startswith(("/v1", "/admin/v1", "/internal")):
                response.headers.setdefault("Cache-Control", "no-cache")        # pages and scripts are re-checked each time, so an update reaches everyone
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        async with db.pool.acquire() as c:
            await c.fetchval("SELECT 1")
        return {"ok": True}

    app.include_router(auth_routes.customer)
    app.include_router(auth_routes.staff)
    for r in (admin_users, admin_misc, admin_tenants, admin_framerate, admin_recorders, admin_messaging, admin_maintenance, admin_billing, admin_rtsp, customer_recorders, customer_cameras, customer_events, customer_alerts, customer_reports, customer_billing,
              internal, internal_events, internal_evidence, public_messaging):
        app.include_router(r.router)

    web = Path(s.web_dir) if s.web_dir else Path(__file__).resolve().parents[1] / "web"
    if (web / "index.html").is_file():
        @app.get("/set-password", include_in_schema=False)
        async def set_password_page():
            return FileResponse(web / "index.html")
        app.mount("/", StaticFiles(directory=web, html=True), name="web")        # last, so it never hides an API route
    return app


app = create_app()
