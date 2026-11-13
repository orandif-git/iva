"""The end of a customer's service. On the day after the end date everything stops (the customer is suspended, which stops alerts, summaries and the app) and the people
chosen for the shop get one WhatsApp message saying so. Nothing is deleted: giving the customer a later end date and reactivating them brings everything back."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..db import db
from .alerts import raise_alert

log = logging.getLogger("gateinsight.service_end")


async def run() -> int:
    """Ends service for every customer whose end date has passed in their own time zone. Returns how many were ended."""
    n = 0
    async with db.service_tx() as conn:
        rows = await conn.fetch("SELECT id, timezone, ends_on FROM app.tenants WHERE ends_on IS NOT NULL AND status::text <> 'suspended'")
        for t in rows:
            if datetime.now(ZoneInfo(t["timezone"])).date() <= t["ends_on"]:
                continue
            # The message goes first: a suspended customer is no longer sent anything.
            await raise_alert(conn, t["id"], "service_ended", body=f"Your Jas iVision service ended on {t['ends_on'].strftime('%d %b %Y')}. Alerts and summaries have stopped. Please contact us to start again.",
                              details={"ended_on": t["ends_on"].isoformat()}, dedupe_key=f"service_ended:{t['ends_on']}", cooldown_minutes=60 * 24 * 365)
            await conn.execute("UPDATE app.tenants SET status='suspended', suspended_at=now(), suspended_reason=$2 WHERE id=$1", t["id"], f"Service ended on {t['ends_on']}")
            n += 1
    return n
