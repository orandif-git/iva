"""Create a staff member (for example the first super admin), or reset their password or authenticator.

    python -m scripts.create_staff --email you@company.in --name "Your Name" --role owner

Run on the server, as a user that can read the application's .env. It asks for the password (hidden, typed twice) and prints a QR-less setup
key and link for an authenticator app (Google Authenticator, Microsoft Authenticator, Authy): add it there, then sign in with the 6-digit code.
Running it again for an existing email sets a new password and a new authenticator, and signs the person out everywhere.
A staff member can never sign in without both."""
import argparse
import asyncio
import getpass
import sys

import asyncpg

from app.config import get_settings
from app.crypto import vault
from app.services import passwords, totp

ROLES = ("owner", "support", "technician", "finance", "analyst")


async def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--role", required=True, choices=ROLES)
    a.add_argument("--dsn", help="a database owner connection (default: the application's own)")
    args = a.parse_args()
    email = args.email.strip().lower()
    pw = getpass.getpass("Password (at least 10 characters): ")
    if pw != getpass.getpass("Again: "):
        print("The two passwords differ.", file=sys.stderr)
        return 1
    try:
        passwords.check_policy(pw, email)
    except Exception as e:
        print(getattr(e, "message", e), file=sys.stderr)
        return 1
    secret = totp.new_secret()
    dsn = args.dsn or get_settings().database_url
    c = await asyncpg.connect(dsn)
    try:
        async with c.transaction():
            await c.execute("SET LOCAL ROLE platform_admin") if not args.dsn else None
            sid = await c.fetchval(
                """INSERT INTO platform.staff_users (email, name, role, status, password_hash) VALUES ($1,$2,$3::platform.staff_role,'active',$4)
                   ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, role=EXCLUDED.role, status='active', password_hash=EXCLUDED.password_hash,
                                                      failed_logins=0, locked_until=NULL, totp_last_step=0 RETURNING id""",
                email, args.name, args.role, await passwords.hash_async(pw))
            await c.execute("UPDATE platform.staff_users SET totp_secret_ciphertext=$2, mfa_enabled=true WHERE id=$1", sid, vault().encrypt(secret, f"totp:{sid}"))
            await c.execute("UPDATE platform.auth_tokens SET revoked_at=now() WHERE account_type='staff' AND account_id=$1 AND revoked_at IS NULL", sid)
    finally:
        await c.close()
    print(f"\nStaff member ready: {email} ({args.role})\n")
    print("Add this to your authenticator app (type the key in, or open the link on your phone):")
    print("  key :", secret)
    print("  link:", totp.uri(secret, email))
    print("\nSign in with your email, password and the 6-digit code the app shows.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
