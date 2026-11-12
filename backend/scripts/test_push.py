"""Send a test push notification for every kind of alert to the phones signed in as one customer, to see how each one reads. Nothing is raised: no alert is saved,
no WhatsApp message is sent. Each test starts with "TEST" so it is not mistaken for the real thing.

    python -m scripts.test_push --email owner@shop.in                   # all of them, a few seconds apart
    python -m scripts.test_push --email owner@shop.in --only crowding,disk_fault
    python -m scripts.test_push --list                                  # the names you can use for --only

Run on the server, as a user that can read the application's .env. Needs push to be set up (GI_FCM_PROJECT_ID and GI_FCM_SERVICE_ACCOUNT_FILE)."""
import argparse
import asyncio
import sys

from app.config import get_settings
from app.db import db
from app.services import push, push_text
from app.services.alert_catalog import ALERT_TYPES

CLOCK = "11:42 PM"
# What each alert says about itself, as a real one would: (camera name, details)
SAMPLES = {
    "crowding": ("Jas Office", {"people": 4, "limit": 3, "minutes": 2}),
    "people_in_area": ("Front camera", {"area": "Billing Counter", "people": 6, "limit": 4, "minutes": 2}),
    "queue_long": ("Front camera", {"area": "Billing Counter", "length": 8, "limit": 5, "minutes": 3}),
    "phone_use": ("Front camera", {"area": "Billing Counter", "seconds": 25}),
    "blocked_plate": ("Main Gate", {"plate": "GJ05AB1234"}),
    "vehicle_overstay": ("Main Gate", {"plate": "GJ05AB1234", "minutes": 45}),
    "low_confidence_plate": ("Main Gate", {"plate": "GJ05AB1234"}),
    "after_hours": ("Back Gate", {}),
    "recorder_offline": ("Warehouse", {"recorder": "Warehouse"}),
    "camera_offline": ("Back Gate", {}),
    "connection_unstable": ("", {"recorder": "Warehouse", "drops": 5}),
    "back_to_normal": ("Back Gate", {"what_recovered": "Back Gate"}),
    "recording_stopped": ("", {"recorder": "Warehouse", "cameras": 2}),
    "disk_fault": ("", {"recorder": "Warehouse", "disk": "Disk 2"}),
    "disk_wear": ("", {"recorder": "Warehouse", "disk": "Disk 2", "percent_left": 12}),
    "storage_full": ("", {"recorder": "Warehouse"}),
    "retention_low": ("", {"recorder": "Warehouse", "days": 14, "target": 30}),
    "clock_wrong": ("", {"recorder": "Warehouse", "minutes_out": 7}),
    "recorder_restarted": ("", {"recorder": "Warehouse", "times": 3}),
    "hardware_fault": ("", {"recorder": "Warehouse", "what": "temperature is above normal"}),
    "view_changed": ("Back Gate", {"what": "moved"}),
    "picture_poor": ("Back Gate", {"what": "dark"}),
    "counting_stopped": ("Front Door", {"hours": 3}),
    "daily_summary": ("", {"summary": "7 cameras working, 1 needs a look."}),
}


async def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--email", help="the customer's sign-in email")
    a.add_argument("--only", help="a comma-separated list of alert names (see --list)")
    a.add_argument("--gap", type=float, default=4.0, help="seconds between notifications (default 4)")
    a.add_argument("--list", action="store_true")
    args = a.parse_args()
    if args.list:
        print("\n".join(SAMPLES))
        return 0
    if not args.email:
        print("Say whose phones to use: --email owner@shop.in", file=sys.stderr)
        return 1
    names = [n.strip() for n in args.only.split(",")] if args.only else list(SAMPLES)
    bad = [n for n in names if n not in SAMPLES]
    if bad:
        print("Not an alert name: " + ", ".join(bad) + ". Use --list to see them.", file=sys.stderr)
        return 1
    if not push.enabled():
        print("Push is not set up on this server (GI_FCM_PROJECT_ID and GI_FCM_SERVICE_ACCOUNT_FILE).", file=sys.stderr)
        return 1
    s = get_settings()
    await db.init(s.database_url, 1, 2)
    try:
        async with db.admin_tx() as c:
            tokens = [r["token"] for r in await c.fetch(
                "SELECT d.token FROM app.push_devices d JOIN app.tenant_users u ON u.id = d.user_id WHERE lower(u.email) = lower($1)", args.email.strip())]
        if not tokens:
            print("No phone is signed in with notifications on for that email. Open the app on the phone, sign in, and tap Turn on for notifications.", file=sys.stderr)
            return 1
        print(f"Sending {len(names)} test notifications to {len(tokens)} device(s).")
        for n in names:
            cam, details = SAMPLES[n]
            title, body = push_text.compose(n, ALERT_TYPES[n]["title"], cam, details, CLOCK, ALERT_TYPES[n]["title"], "")
            r = await push.deliver(tokens, f"TEST · {title}", body, {"kind": "test"})
            print(f"  {n:22} {r}")
            await asyncio.sleep(args.gap)
    finally:
        await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
