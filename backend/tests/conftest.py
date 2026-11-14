"""Tests run against a real PostgreSQL with the real schema, because row-level security and the audit trigger are
the things most worth testing. Point TEST_PG_HOST / TEST_PG_PORT at a throwaway server (see README)."""
import base64
import os
import pathlib
import uuid

PG_HOST = os.environ.get("TEST_PG_HOST", "/tmp/pgtest")
PG_PORT = os.environ.get("TEST_PG_PORT", "55432")
ADMIN_DSN = f"postgresql://postgres@/postgres?host={PG_HOST}&port={PG_PORT}"
DB_NAME = "gi_test"
SUPER_DSN = f"postgresql://postgres@/{DB_NAME}?host={PG_HOST}&port={PG_PORT}"

os.environ.update({
    "GI_ENV": "test",
    "GI_DATABASE_URL": f"postgresql://gi_api:gi@/{DB_NAME}?host={PG_HOST}&port={PG_PORT}",
    "GI_JWT_SECRET_PLATFORM": "p" * 40,
    "GI_SERVICE_TOKEN": "s" * 40,
    "GI_KEY_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode(),
    "GI_MESSAGING_ALLOW_PRIVATE_HOSTS": "true",        # the mock provider and mail server run on localhost
    "GI_PUBLIC_BASE_URL": "https://gi.test",
    "GI_EVIDENCE_LOCAL_DIR": "/tmp/gi-test-evidence",
})

import re  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.db import db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import issue_token  # noqa: E402

SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "schema.sql"


@pytest_asyncio.fixture(scope="session")
async def su():
    """Superuser connection used only to set up test data."""
    a = await asyncpg.connect(ADMIN_DSN)
    await a.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
    await a.execute(f"CREATE DATABASE {DB_NAME}")
    await a.close()
    c = await asyncpg.connect(SUPER_DSN)
    await c.execute(SCHEMA.read_text())
    await c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='gi_api') THEN CREATE ROLE gi_api LOGIN PASSWORD 'gi'; END IF; END $$")
    await c.execute("GRANT app_user, platform_admin, service_worker, public_edge TO gi_api")
    await c.execute("GRANT CONNECT ON DATABASE gi_test TO gi_api")
    await c.execute("DELETE FROM platform.alert_switches")          # the schema starts the area alerts switched off; tests start with everything on and test that separately
    await c.execute("INSERT INTO platform.plans VALUES ('quick','Quick connect',79900,'snapshots','{}'), ('full','Full speed',149900,'realtime_5fps','{\"anpr\": true}')")
    yield c
    await c.close()


# A test "user" is a staff Owner working on one customer. Many tests are written in short, customer-centred paths (/v1/cameras, /v1/recipients ...); this client
# sends them to the Control Center's own routes for that customer (/admin/v1/tenants/<id>/shop/...), so the tests exercise the real staff endpoints.
USER_TENANT: dict[str, uuid.UUID] = {}


def to_shop(method: str, path: str, tenant) -> str | None:
    base, q = f"/admin/v1/tenants/{tenant}/shop", ""
    if "?" in path:
        path, q = path.split("?", 1)
        q = "?" + q
    for prefix, target in (("/v1/cameras", "cameras"), ("/v1/recipients", "people"), ("/v1/alert-types", "events"), ("/v1/vehicle-lists", "vehicle-lists")):
        if path == prefix or path.startswith(prefix + "/"):
            return f"{base}/{target}{path[len(prefix):]}{q}"
    if path == "/v1/area-kinds":
        return f"{base}/area-kinds{q}"
    if path == "/v1/opening-hours":
        return f"{base}/hours{q}"
    if path == "/v1/recorders":
        return f"/admin/v1/tenants/{tenant}/recorders" if method == "POST" else f"/admin/v1/recorders?tenant_id={tenant}"
    m = re.fullmatch(r"/v1/recorders/([^/]+)/channels", path)
    if m:
        return f"{base}/recorders/{m.group(1)}/channels{q}"
    m = re.fullmatch(r"/v1/recorders/([^/]+)/connection", path)
    if m:
        return f"/admin/v1/recorders/{m.group(1)}/connection{q}"
    return None


class ShopClient(httpx.AsyncClient):
    async def request(self, method, url, **kw):
        if isinstance(url, str) and url.startswith("/v1/"):
            tok = ((kw.get("headers") or {}).get("Authorization") or "").removeprefix("Bearer ")
            if tok in USER_TENANT:
                new = to_shop(method, url, USER_TENANT[tok])
                if new:
                    url = new
                    body = kw.get("json")
                    if isinstance(body, dict) and "/shop/people" in new and method == "POST":
                        kw["json"] = {k: v for k, v in body.items() if k in ("name", "phone")}
                    if isinstance(body, dict) and new.endswith("/recorders") and method == "POST":
                        kw["json"] = {"reason": "Added by a test", **body}
        return await super().request(method, url, **kw)


@pytest_asyncio.fixture(scope="session")
async def client(su):
    await db.init(os.environ["GI_DATABASE_URL"], 1, 5)
    app = create_app(use_lifespan=False)
    async with ShopClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await db.close()


class Factory:
    def __init__(self, su):
        self.su = su

    async def staff(self, role="owner", mfa=True, status="active"):
        sid = await self.su.fetchval("INSERT INTO platform.staff_users (email,name,role,status) VALUES ($1,$2,$3::platform.staff_role,$4) RETURNING id",
                                     f"{uuid.uuid4().hex[:8]}@gi.test", f"Staff {role}", role, status)
        return {"id": sid, "headers": {"Authorization": "Bearer " + issue_token("platform", {"sub": str(sid), "mfa": mfa}, 600)}}

    async def tenant(self, name=None, plan="full", limit=5, status="active"):
        name = name or f"Tenant {uuid.uuid4().hex[:6]}"
        tid = await self.su.fetchval(
            "INSERT INTO app.tenants (name, slug, plan_id, camera_limit, status) VALUES ($1,$2,$3,$4,$5::app.tenant_status) RETURNING id",
            name, name.lower().replace(" ", "-"), plan, limit, status)
        return tid

    async def user(self, tenant_id, role="admin", status="active"):
        """Someone who works on this one customer: a staff Owner. (`role` and `status` are kept so older tests read the same.)"""
        owner = await self.staff("owner")
        USER_TENANT[owner["headers"]["Authorization"].removeprefix("Bearer ")] = tenant_id
        return {"id": owner["id"], "tenant_id": tenant_id, "headers": owner["headers"]}

    @staticmethod
    def service():
        return {"Authorization": "Bearer " + os.environ["GI_SERVICE_TOKEN"]}


@pytest_asyncio.fixture(scope="session")
async def make(su):
    return Factory(su)


@pytest_asyncio.fixture(autouse=True)
async def _platform_state(su):
    """What is known about our own platform (which analysis workers are alive, open faults, who is told, check intervals) starts empty for every test,
    so a test that ran minutes earlier cannot make this one think a worker has gone silent."""
    await su.execute("TRUNCATE platform.worker_seen, platform.faults, platform.fault_notify, platform.alert_checks")
    from app.services import maintenance
    maintenance._last_run.clear()
