import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import errors
from .config import get_settings
from .db import db
from .routers import (auth as auth_routes, admin_users, admin_billing, admin_framerate, admin_maintenance, admin_messaging, admin_misc, admin_recorders, admin_rtsp, admin_tenants, customer_alerts, customer_billing, customer_cameras, customer_events,
                      customer_recorders, customer_reports, internal, internal_evidence, internal_events, public_messaging)
from .messaging import delivery
from .services import billing, maintenance, reports, sweeper

logging.basicConfig(level=logging.INFO)


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
    app = FastAPI(title="Gate Insight API", version="0.1.0", lifespan=lifespan if use_lifespan else None,
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
    return app


app = create_app()
