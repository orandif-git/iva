"""Faults in our own platform, as opposed to a customer's camera or recorder.

Each analysis worker asks for its cameras once a minute. When a worker has been silent for a while, the cameras it looks after are not being analysed. That is our
fault, not the customer's, so those cameras are NOT reported to the customer as offline. Instead a fault is opened here, shown in Platform Admin, and the staff chosen
there are told (once when it starts, once when it is over). A camera or a recorder that really goes offline is reported to the customer exactly as before."""
import json
import logging
import uuid
from datetime import datetime, timezone

from ..config import get_settings
from ..db import db
from ..messaging import webhook
from . import alert_catalog as cat

log = logging.getLogger("gateinsight.platform_fault")
WORKER_SILENT_SECONDS = 150            # a worker asks every 60 s: two and a half misses
KIND = "analysis_stopped"


async def worker_polled(conn, shard: int, of: int) -> None:
    await conn.execute("INSERT INTO platform.worker_seen (shard, of) VALUES ($1,$2) ON CONFLICT (shard, of) DO UPDATE SET seen_at = now()", shard, of)


async def silent_shards(conn) -> tuple[int, list[int]]:
    """(`of`, the shards whose worker has gone silent). `of` is 0 when no worker has ever asked, in which case nothing is known and nothing is held back."""
    latest = await conn.fetchrow("SELECT of FROM platform.worker_seen ORDER BY seen_at DESC LIMIT 1")
    if not latest:
        return 0, []
    of = latest["of"]
    rows = await conn.fetch("SELECT shard FROM platform.worker_seen WHERE of=$1 AND seen_at < now() - make_interval(secs => $2)", of, float(WORKER_SILENT_SECONDS))
    return of, sorted(r["shard"] for r in rows)


async def settings(conn) -> dict:
    r = await conn.fetchrow("SELECT enabled, people FROM platform.fault_notify WHERE id")
    people = (r["people"] if r else []) or []
    if isinstance(people, str):
        people = json.loads(people)
    return {"enabled": r["enabled"] if r else True, "people": people}


async def track(conn, cameras_affected: int, silent: list[int]) -> tuple[str, dict] | None:
    """Opens the fault when analysis has stopped and closes it when it is back. Returns ("started" | "ended", the fault) when staff should be told now."""
    open_ = await conn.fetchrow("SELECT * FROM platform.faults WHERE kind=$1 AND ended_at IS NULL", KIND)
    if silent and cameras_affected > 0:
        if open_:
            await conn.execute("UPDATE platform.faults SET cameras = greatest(cameras, $2) WHERE id=$1", open_["id"], cameras_affected)
            return None
        f = await conn.fetchrow("INSERT INTO platform.faults (kind, detail, cameras, told_at) VALUES ($1,$2,$3, now()) ON CONFLICT DO NOTHING RETURNING *", KIND,
                                f"{len(silent)} analysis worker{'s' if len(silent) != 1 else ''} stopped reporting.", cameras_affected)
        return ("started", dict(f)) if f else None
    if open_ and not silent:
        f = await conn.fetchrow("UPDATE platform.faults SET ended_at = now() WHERE id=$1 RETURNING *", open_["id"])
        return ("ended", dict(f))
    return None


async def tell_staff(what: str, fault: dict) -> int:
    """Sends the platform_fault webhook event to each person chosen in Platform Admin. Returns how many were sent. Never raises."""
    from ..messaging.delivery import endpoint_headers, webhook_settings, ssl_context
    try:
        async with db.service_tx() as conn:
            cfg = await settings(conn)
            ep = await conn.fetchrow("SELECT * FROM platform.webhook_endpoints WHERE alert_type='platform_fault'")
            if not cfg["enabled"] or not cfg["people"] or not ep or not ep["active"]:
                return 0
            hook, headers = await webhook_settings(conn), endpoint_headers(ep)
        started = what == "started"
        title = "Video analysis stopped" if started else "Video analysis restored"
        mins = int(((fault.get("ended_at") or datetime.now(timezone.utc)) - fault["started_at"]).total_seconds() // 60)
        detail = (f"{fault['cameras']} cameras are not being analysed. Customers are not being told their cameras are offline." if started
                  else f"Analysis is running again after {mins} minute{'s' if mins != 1 else ''}.")
        sent = 0
        for p in cfg["people"]:
            values = webhook.values_for(tenant_name="Jas iVision", alert_id=fault["id"], title=title, event=cat.event_name("platform_fault"),
                                        details={"location": "Platform", "time": datetime.now(timezone.utc).strftime("%d %b, %H:%M UTC"), "details": detail, "cameras": fault["cameras"]},
                                        person_name=p.get("name", ""), phone=p.get("phone", ""))
            res = await webhook.send(ep["url"], headers, webhook.render(ep["payload_template"], values).encode(), event=cat.event_name("platform_fault"), delivery_id=str(uuid.uuid4()),
                                     secret=hook["secret"], allow_private=get_settings().messaging_allow_private_hosts, ssl_context=ssl_context())
            sent += 1 if res.ok else 0
        return sent
    except Exception:
        log.exception("could not tell staff about the platform fault")
        return 0
