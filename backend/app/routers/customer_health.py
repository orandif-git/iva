"""One look at whether the shop's cameras and connection are fine. Feeds the health banner and panel on the customer app's Home screen."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..deps import CustomerCtx, customer_ctx

router = APIRouter(prefix="/v1", tags=["customer: health"])
STALE_SECONDS = 180            # a camera that has not reported for this long is not "working"
DROPS_IN_AN_HOUR = 3           # this many recorder drops in an hour is "unstable"


@router.get("/health-summary")
async def health_summary(ctx: CustomerCtx = Depends(customer_ctx())):
    c = ctx.conn
    recorders = await c.fetch(
        """SELECT r.id, coalesce(r.name, r.device_id) AS name, r.status::text AS status, r.last_heartbeat_at,
                  (SELECT count(*) FROM app.recorder_link_events e WHERE e.recorder_id=r.id AND e.status='offline' AND e.at > now() - interval '1 hour')::int AS drops
           FROM app.recorders r ORDER BY name""")
    cams = await c.fetch(
        """SELECT c.id, c.name, c.stream_state, c.stream_seen_at, c.last_frame_at, r.status::text AS recorder_status, coalesce(r.name, r.device_id) AS recorder
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id=c.recorder_id WHERE c.enabled ORDER BY c.name""")
    now = datetime.now(timezone.utc)

    def fresh(ts):
        return ts is not None and (now - ts).total_seconds() <= STALE_SECONDS

    down, working, starting = [], 0, 0
    for k in cams:
        if k["recorder_status"] == "offline" or k["stream_state"] in ("offline", "reconnecting") or (k["stream_state"] == "streaming" and not fresh(k["stream_seen_at"])):
            down.append({"id": k["id"], "name": k["name"], "recorder": k["recorder"]})
        elif k["stream_state"] == "streaming":
            working += 1
        else:
            starting += 1
    unstable = [{"id": r["id"], "name": r["name"], "drops": r["drops"]} for r in recorders if r["status"] == "online" and r["drops"] >= DROPS_IN_AN_HOUR]
    offline = [{"id": r["id"], "name": r["name"], "since": r["last_heartbeat_at"]} for r in recorders if r["status"] == "offline"]

    msg = await c.fetchrow(
        """SELECT count(*) FILTER (WHERE status IN ('sent','delivered','read'))::int AS sent, count(*) FILTER (WHERE status='failed')::int AS failed,
                  count(*) FILTER (WHERE status='queued' AND created_at < now() - interval '10 minutes')::int AS stuck,
                  max(created_at) FILTER (WHERE status IN ('sent','delivered','read')) AS last_sent
           FROM app.notifications WHERE channel='whatsapp' AND created_at > now() - interval '24 hours'""")
    newest = max([k["stream_seen_at"] for k in cams if k["stream_seen_at"]] or [None], default=None)
    age = int((now - newest).total_seconds()) if newest else None
    open_alerts = await c.fetchval("SELECT count(*) FROM app.alerts WHERE acknowledged_at IS NULL AND (snoozed_until IS NULL OR snoozed_until <= now()) AND NOT coalesce((details->>'test')::boolean, false)")

    issues = []
    for r in offline:
        issues.append({"kind": "recorder_offline", "level": "critical", "title": f"{r['name']} is offline", "id": r["id"]})
    for k in down:
        issues.append({"kind": "camera_down", "level": "critical", "title": f"{k['name']} camera is not sending pictures", "id": k["id"]})
    for r in unstable:
        issues.append({"kind": "recorder_unstable", "level": "warning", "title": f"{r['name']} keeps dropping ({r['drops']} times in the last hour)", "id": r["id"]})
    if msg["failed"] and msg["failed"] >= max(3, msg["sent"]):
        issues.append({"kind": "messages_failing", "level": "warning", "title": "Many WhatsApp messages are failing", "id": None})
    if msg["stuck"]:
        issues.append({"kind": "messages_stuck", "level": "warning", "title": f"{msg['stuck']} WhatsApp messages are waiting to be sent", "id": None})
    if recorders and age is not None and age > STALE_SECONDS and not offline and not down:
        issues.append({"kind": "data_old", "level": "warning", "title": "Information is older than usual", "id": None})

    return {
        "status": "attention" if issues else "ok", "issues": issues, "open_alerts": open_alerts,
        "recorders": {"total": len(recorders), "online": sum(r["status"] == "online" for r in recorders), "offline": offline, "unstable": unstable},
        "cameras": {"total": len(cams), "working": working, "starting": starting, "down": down},
        "messages": {"sent_24h": msg["sent"], "failed_24h": msg["failed"], "waiting_too_long": msg["stuck"], "last_sent": msg["last_sent"]},
        "data": {"newest_seconds_ago": age},
    }
