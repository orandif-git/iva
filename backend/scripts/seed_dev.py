"""Create demo data in a development database and print sign-in tokens.

Usage (needs a privileged connection, because it inserts across schemas):
    GI_SEED_DSN=postgresql://postgres@localhost/gateinsight python -m scripts.seed_dev
"""
import asyncio
import os
import sys

import asyncpg

from app.security import issue_token


async def main() -> None:
    dsn = os.environ.get("GI_SEED_DSN")
    if not dsn:
        sys.exit("Set GI_SEED_DSN to a superuser connection string for the development database.")
    c = await asyncpg.connect(dsn)
    await c.execute("""INSERT INTO platform.plans VALUES
        ('quick','Quick connect',79900,'snapshots','{}'),
        ('full','Full speed 5 fps',149900,'realtime_5fps','{"anpr": true}') ON CONFLICT DO NOTHING""")
    staff = {}
    for role, name in (("owner", "Aman"), ("support", "Priya")):
        staff[role] = await c.fetchval(
            """INSERT INTO platform.staff_users (email, name, role, mfa_enabled) VALUES ($1,$2,$3::platform.staff_role,true)
               ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name RETURNING id""", f"{name.lower()}@example.test", name, role)
    tenant = await c.fetchval(
        """INSERT INTO app.tenants (name, slug, city, plan_id, camera_limit, status) VALUES ('Sharma Auto Components','sharma-auto','Sanand','full',10,'active')
           ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""")
    user = await c.fetchval(
        """INSERT INTO app.tenant_users (tenant_id, email, name, role) VALUES ($1,'owner@sharma.example.test','Sharma Admin','admin')
           ON CONFLICT (tenant_id, email) DO UPDATE SET name=EXCLUDED.name RETURNING id""", tenant)
    await c.close()
    print("Staff tokens (valid 8 hours), use as:  Authorization: Bearer <token>")
    for role, sid in staff.items():
        print(f"  {role:10s} {issue_token('platform', {'sub': str(sid), 'mfa': True}, 8 * 3600)}")
    print("\nCustomer admin token (valid 8 hours):")
    print(f"  tenant {tenant}\n  {issue_token('customer', {'sub': str(user), 'tenant_id': str(tenant)}, 8 * 3600)}")


if __name__ == "__main__":
    asyncio.run(main())
