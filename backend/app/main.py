import asyncio
import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import errors
from .config import get_settings
from .db import db
from .routers import (auth as auth_routes, admin_alerts, admin_framerate, admin_maintenance, admin_messaging, admin_misc, admin_plans, admin_recorders, admin_shop, admin_staff, admin_storage,
                      admin_summaries, admin_system, admin_tenants, admin_views, admin_webhooks, internal, internal_evidence, internal_events, public_messaging)
from .messaging import delivery, evidence as delivery_evidence
from .services import maintenance, reports, sweeper, system_health

logging.basicConfig(level=logging.INFO)

# The web pages load only their own scripts and styles and talk only to this server, so a page cannot be made to load or send anything elsewhere.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; manifest-src 'self'; connect-src 'self'; font-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
RETIRE_SW = '''self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) await caches.delete(k);
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) { try { await c.navigate("/"); } catch (err) {} }
})()));
'''
_DOCS = ("/docs", "/openapi.json")
# A request that came through a proxy (the website's front door) carries one of these headers; our own services call the API directly and do not.
_PROXIED = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await db.init(s.database_url, s.db_pool_min, s.db_pool_max)
    tasks = [asyncio.create_task(sweeper.run_forever()), asyncio.create_task(delivery_evidence.refresh_forever()), asyncio.create_task(maintenance.run_forever()), asyncio.create_task(delivery.run_forever()), asyncio.create_task(reports.run_forever()), asyncio.create_task(system_health.run_forever())]
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
        if request.url.path.startswith("/internal/") and any(h in request.headers for h in _PROXIED):
            # The /internal routes are for the registrar, gateway and workers on this server. Anything that arrives through the public web
            # address is refused here as well, whatever the web server's settings are (the service token would still be needed).
            return JSONResponse({"error": {"code": "not_found", "message": "Not Found", "request_id": rid}}, status_code=404, headers={"X-Request-ID": rid})
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(_DOCS):
            response.headers.setdefault("X-Frame-Options", "DENY")
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers.setdefault("Content-Security-Policy", CSP)         # a page with its own policy (the evidence viewer plays video) keeps it
            if not request.url.path.startswith(("/admin/v1", "/internal")):
                response.headers.setdefault("Cache-Control", "no-cache")        # pages and scripts are re-checked each time, so an update reaches everyone
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        async with db.pool.acquire() as c:
            await c.fetchval("SELECT 1")
        return {"ok": True}

    app.include_router(auth_routes.staff)
    for r in (admin_alerts, admin_framerate, admin_maintenance, admin_messaging, admin_misc, admin_plans, admin_recorders, admin_shop, admin_staff, admin_storage, admin_summaries, admin_system, admin_tenants,
              admin_views, admin_webhooks, internal, internal_evidence, internal_events, public_messaging):
        app.include_router(r.router)

    web = Path(s.web_dir) if s.web_dir else Path(__file__).resolve().parents[1] / "web"
    # Everyone signs in at "/", and the links from an invitation or a password reset open "/set-password". One small page serves both (web/console/signin.html).
    signin = web / "console" / "signin.html"
    if signin.is_file():
        @app.get("/", include_in_schema=False)
        @app.get("/set-password", include_in_schema=False)
        async def signin_page():
            return FileResponse(signin, headers={"Cache-Control": "no-store"})
    # Addresses that used to lead somewhere else lead to the sign-in page now, so old bookmarks and emailed links keep working.
    @app.get("/platform/set-password", include_in_schema=False)
    async def old_set_password_link(request: Request):
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/set-password{q}", status_code=301)
    for old in ("/platform", "/staff", "/next"):
        @app.get(old, include_in_schema=False)
        @app.get(old + "/{rest:path}", include_in_schema=False)
        async def old_address(rest: str = ""):
            return RedirectResponse("/", status_code=301)
    # An older version of the web app left a service worker in some browsers. Its next update check gets this one, which empties its caches, removes itself and
    # reloads the page, so every device moves over by itself.
    @app.get("/sw.js", include_in_schema=False)
    async def retire_old_worker():
        return Response(RETIRE_SW, media_type="application/javascript", headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"})
    if (web / "favicon.ico").is_file():
        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            return FileResponse(web / "favicon.ico", media_type="image/vnd.microsoft.icon")
    if (web / "console").is_dir():
        app.mount("/console", StaticFiles(directory=web / "console", html=True), name="console")     # the Control Center (Jas iVision's staff console)
    if (web / "img").is_dir():
        app.mount("/img", StaticFiles(directory=web / "img"), name="web_img")
    return app


app = create_app()
