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
from .routers import (auth as auth_routes, admin_users, admin_alerts, admin_webhooks, admin_billing, admin_framerate, admin_maintenance, admin_messaging, admin_aiview, admin_misc, admin_plans, admin_staff, admin_storage, admin_recorders, admin_rtsp, admin_tenants, customer_alerts, customer_areas, customer_health, customer_live, customer_devices, customer_billing, customer_cameras, customer_events,
                      customer_recorders, customer_reports, internal, internal_evidence, internal_events, public_messaging)
from .messaging import delivery, evidence as delivery_evidence
from .services import billing, maintenance, reports, sweeper

logging.basicConfig(level=logging.INFO)

# The web pages load only their own scripts and styles and talk only to this server, so a page cannot be made to load or send anything elsewhere.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; manifest-src 'self'; connect-src 'self'; font-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# The new customer app (Flutter, served at /next/) draws on a canvas, so it needs WebAssembly and its own inline styles. It loads scripts only from this server and talks to it,
# plus the three Google addresses a browser needs to get its push address (Firebase Cloud Messaging).
RETIRE_SW = '''self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) await caches.delete(k);
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) { try { await c.navigate("/"); } catch (err) {} }
})()));
'''
NEXT_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; manifest-src 'self'; "
            "connect-src 'self' data: blob: https://fcmregistrations.googleapis.com https://firebaseinstallations.googleapis.com https://fcm.googleapis.com; "
            "font-src 'self' data:; worker-src 'self' blob:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
_DOCS = ("/docs", "/openapi.json")
# A request that came through a proxy (the website's front door) carries one of these headers; our own services call the API directly and do not.
_PROXIED = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not s.staff_require_mfa:
        logging.getLogger("gateinsight").warning("Staff sign in with a password only (GI_STAFF_REQUIRE_MFA=false). Use a long, unique password and turn two-step back on when you can.")
    await db.init(s.database_url, s.db_pool_min, s.db_pool_max)
    tasks = [asyncio.create_task(sweeper.run_forever()), asyncio.create_task(delivery_evidence.refresh_forever()), asyncio.create_task(maintenance.run_forever()), asyncio.create_task(delivery.run_forever()), asyncio.create_task(reports.run_forever()), asyncio.create_task(billing.run_forever())]
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
                response.headers.setdefault("Content-Security-Policy", NEXT_CSP if request.url.path.startswith("/next") else CSP)         # a page with its own policy (the evidence viewer plays video) keeps it
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
    for r in (admin_users, admin_alerts, admin_webhooks, admin_aiview, admin_plans, admin_staff, admin_storage, admin_misc, admin_tenants, admin_framerate, admin_recorders, admin_messaging, admin_maintenance, admin_billing, admin_rtsp, customer_recorders, customer_cameras, customer_events, customer_alerts, customer_areas, customer_health, customer_live, customer_devices, customer_reports, customer_billing,
              internal, internal_events, internal_evidence, public_messaging):
        app.include_router(r.router)

    web = Path(s.web_dir) if s.web_dir else Path(__file__).resolve().parents[1] / "web"
    if (web / "index.html").is_file():
        @app.get("/set-password", include_in_schema=False)
        async def set_password_page():
            return FileResponse(web / "index.html")
        @app.get("/staff/set-password", include_in_schema=False)
        async def staff_set_password_page():
            return FileResponse(web / "staff" / "index.html")
        nxt = web.parent / "web_next"
        if (nxt / "index.html").is_file():          # the new customer app, built from /app (see scripts/build_app.sh)
            if s.default_app == "next":
                # The new app opens at the plain address. The old one is still there for support and for a person who needs it: /?classic=1 opens it and a
                # cookie keeps it for 30 days; /?next=1 goes back. Password links (/set-password) and the staff console are untouched.
                @app.get("/", include_in_schema=False)
                async def front_door(request: Request):
                    q = request.query_params
                    if "next" in q:
                        r = RedirectResponse("/next/#/", status_code=302)
                        r.delete_cookie("gi_ui", path="/")
                        return r
                    if "classic" in q or request.cookies.get("gi_ui") == "classic":
                        r = FileResponse(web / "index.html")
                        if "classic" in q:
                            r.set_cookie("gi_ui", "classic", max_age=30 * 86400, path="/", secure=s.env != "dev", httponly=True, samesite="lax")
                        return r
                    return RedirectResponse("/next/#/", status_code=302)
                # Phones and computers that used the old app still hold its service worker, which keeps old files. While the new app is the front door the server
                # answers the old worker's update check with one that empties its caches, removes itself and reloads the page, so every device moves over by itself.
                @app.get("/sw.js", include_in_schema=False)
                async def retire_old_worker():
                    return Response(RETIRE_SW, media_type="application/javascript", headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"})
            app.mount("/next", StaticFiles(directory=nxt, html=True), name="web_next")
        app.mount("/", StaticFiles(directory=web, html=True), name="web")        # last, so it never hides an API route
    return app


app = create_app()
