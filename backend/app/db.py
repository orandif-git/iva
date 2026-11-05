"""Database access.

Every request runs in one transaction under a restricted database role:
  app_user       customer API. Row-level security limits it to one tenant.
  platform_admin admin console. Can read across tenants (BYPASSRLS), every write is audited.
  service_worker registrar and workers.
The connection itself logs in as a low-privilege role that is a member of those three; SET LOCAL ROLE
switches for the length of the transaction only, so a pooled connection never keeps elevated rights.
"""
import json
from contextlib import asynccontextmanager

import asyncpg

_ROLES = {"app_user", "platform_admin", "service_worker"}


async def _init_conn(conn: asyncpg.Connection) -> None:
    for t in ("jsonb", "json"):
        await conn.set_type_codec(t, encoder=lambda v: json.dumps(v, default=str), decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def init(self, dsn: str, min_size: int = 2, max_size: int = 10) -> None:
        self.pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init_conn)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    @asynccontextmanager
    async def _tx(self, role: str, tenant_id=None, read_only: bool = False, settings: dict | None = None):
        assert role in _ROLES
        async with self.pool.acquire() as conn:
            async with conn.transaction(readonly=read_only):
                await conn.execute(f"SET LOCAL ROLE {role}")
                if tenant_id is not None:
                    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                for k, v in (settings or {}).items():
                    await conn.execute("SELECT set_config($1, $2, true)", k, v or "")
                yield conn

    def tenant_tx(self, tenant_id, read_only: bool = False, settings: dict | None = None):
        return self._tx("app_user", tenant_id, read_only, settings)

    def admin_tx(self):
        return self._tx("platform_admin")

    def service_tx(self):
        return self._tx("service_worker")


db = Database()
