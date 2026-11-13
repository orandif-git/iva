"""Send one test push notification to a device, to check the Firebase set-up (GI_FCM_PROJECT_ID and GI_FCM_SERVICE_ACCOUNT_FILE).

    python -m scripts.test_push --token <the device's push token>

Run on the server, as a user that can read the application's .env."""
import argparse
import asyncio
import sys

from app.services import push


async def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--token", required=True, help="a device's push (FCM) token")
    args = a.parse_args()
    if not push.enabled():
        print("Push is not set up: GI_FCM_PROJECT_ID and GI_FCM_SERVICE_ACCOUNT_FILE are needed.", file=sys.stderr)
        return 1
    r = await push.deliver([args.token.strip()], "Jas iVision test", "If you can read this, push is working.", {"kind": "test"})
    print("sent:", r)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
