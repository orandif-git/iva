"""Print the daily or weekly summary of a customer exactly as it would be sent, without sending anything and without saving anything.

    python -m scripts.print_summary --customer "Jas Office"                 # the daily summary
    python -m scripts.print_summary --customer "Jas Office" --kind weekly
    python -m scripts.print_summary --list                                  # the customers you can name

The customer is found by part of its name. Run on the server, as a user that can read the application's .env. It uses the settings saved in Platform Admin."""
import argparse
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import db
from app.services import summaries, summary_config


async def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--customer", help="part of the customer's name")
    a.add_argument("--kind", choices=("daily", "weekly"), default="daily")
    a.add_argument("--list", action="store_true")
    args = a.parse_args()
    s = get_settings()
    await db.init(s.database_url, 1, 2)
    try:
        async with db.service_tx() as conn:
            rows = await conn.fetch("SELECT id, name, timezone, open_time, close_time, closed_days FROM app.tenants WHERE status::text IN ('trial','active','past_due') ORDER BY name")
            if args.list or not args.customer:
                print("\n".join(r["name"] for r in rows))
                return 0
            hits = [r for r in rows if args.customer.lower() in r["name"].lower()]
            if len(hits) != 1:
                print("Name one customer. " + ("No customer matches." if not hits else "These match: " + ", ".join(r["name"] for r in hits)), file=sys.stderr)
                return 1
            t, cfg = hits[0], await summary_config.load(conn)
            built = await summaries.compose(conn, t, args.kind, cfg, datetime.now(ZoneInfo(t["timezone"])).date())
        print(built["text"])
        print("\n--- " + str(len(built["text"])) + " characters" + (f", {built['dropped']} line(s) left out to fit" if built["dropped"] else ""))
        print("--- phone:", built["headline"])
        print("--- phone (Tamil):", built["headline_ta"])
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
