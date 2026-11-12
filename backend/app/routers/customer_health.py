"""One look at whether the shop's cameras and connection are fine. Feeds the health banner and panel on the customer app's Home screen."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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

    ours = await c.fetchval("SELECT EXISTS (SELECT 1 FROM platform.faults WHERE ended_at IS NULL)")     # our own analysis has stopped: a silent camera is not the customer's problem

    def fresh(ts):
        return ours or (ts is not None and (now - ts).total_seconds() <= STALE_SECONDS)

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

    # A payment that is due: shown as a line on Home (there is no alert or message for it). The earliest unpaid invoice, with its date in the customer's own time.
    inv = await c.fetchrow(
        """SELECT i.number, i.total_paise, i.due_at, t.timezone FROM app.invoices i JOIN app.tenants t ON t.id = i.tenant_id
           WHERE i.status='issued' AND i.due_at IS NOT NULL ORDER BY i.due_at LIMIT 1""")
    payment = None
    if inv:
        local = inv["due_at"].astimezone(ZoneInfo(inv["timezone"]))
        payment = {"invoice": inv["number"], "amount": f"₹{inv['total_paise'] / 100:,.0f}", "due_date": f"{local.day} {local.strftime('%b %Y')}", "overdue": inv["due_at"] <= now,
                   "days": abs((local.date() - now.astimezone(ZoneInfo(inv["timezone"])).date()).days)}

    return {
        "status": "attention" if issues else "ok", "issues": issues, "open_alerts": open_alerts, "payment": payment,
        "recorders": {"total": len(recorders), "online": sum(r["status"] == "online" for r in recorders), "offline": offline, "unstable": unstable},
        "cameras": {"total": len(cams), "working": working, "starting": starting, "down": down},
        "messages": {"sent_24h": msg["sent"], "failed_24h": msg["failed"], "waiting_too_long": msg["stuck"], "last_sent": msg["last_sent"]},
        "data": {"newest_seconds_ago": age},
    }


HEALTH_RULES = ("camera_offline", "recorder_offline", "connection_unstable", "disk_fault", "disk_wear", "recording_stopped", "clock_wrong", "recorder_restarted", "hardware_fault",
                "storage_full", "view_changed", "picture_poor")


@router.get("/health-detail")
async def health_detail(ctx: CustomerCtx = Depends(customer_ctx())):
    """For the Health tab, one line per camera (is it recording, when did it last send a picture, is its picture fine) and how many problems each of the last seven days had."""
    c = ctx.conn
    now = datetime.now(timezone.utc)
    rows = await c.fetch(
        """SELECT c.id, c.name, c.channel_no, c.recorder_id, c.stream_state, c.last_frame_at, c.stream_seen_at, r.status::text AS recorder_status, r.health AS rec_health,
                  p.bad_view_kind, p.bad_view_since, p.poor_kind, p.poor_since
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id = c.recorder_id LEFT JOIN app.camera_picture_state p ON p.camera_id = c.id
           WHERE c.enabled ORDER BY c.name""")
    cams = []
    for k in rows:
        h = k["rec_health"] if isinstance(k["rec_health"], dict) else {}
        now_rec = (h.get("recording_now") or {}).get(str(k["channel_no"])) if k["channel_no"] is not None else None
        seen = k["last_frame_at"] or k["stream_seen_at"]
        issue = k["bad_view_kind"] or k["poor_kind"]
        cams.append({"id": k["id"], "name": k["name"], "recorder_id": k["recorder_id"], "online": k["recorder_status"] != "offline" and k["stream_state"] == "streaming",
                     "seconds_since_picture": int((now - seen).total_seconds()) if seen else None,
                     "picture": issue,                      # moved, covered, blurry, dark, or nothing
                     "recording": None if now_rec is None else bool(now_rec)})
    days = await c.fetch(
        """SELECT (a.created_at AT TIME ZONE t.timezone)::date AS day, count(*)::int AS n
           FROM app.alerts a JOIN app.tenants t ON t.id = a.tenant_id
           WHERE a.rule_type = ANY($1) AND a.created_at > now() - interval '8 days' AND NOT coalesce((a.details->>'test')::boolean, false)
           GROUP BY 1""", list(HEALTH_RULES))
    tz = await c.fetchval("SELECT timezone FROM app.tenants LIMIT 1") or "Asia/Kolkata"
    today = now.astimezone(ZoneInfo(tz)).date()
    by_day = {d["day"]: d["n"] for d in days}
    from datetime import timedelta
    strip = [{"date": (today - timedelta(days=i)).isoformat(), "problems": by_day.get(today - timedelta(days=i), 0)} for i in range(6, -1, -1)]
    return {"cameras": cams, "days": strip}


@router.get("/summary")
async def latest_summary(ctx: CustomerCtx = Depends(customer_ctx())):
    """The newest daily or weekly summary, whole, for the Home screen. The same text that went to WhatsApp."""
    r = await ctx.conn.fetchrow("SELECT rule_type, body, details, created_at FROM app.alerts WHERE rule_type IN ('daily_summary','weekly_summary') ORDER BY created_at DESC LIMIT 1")
    if not r:
        return {"summary": None}
    return {"summary": {"kind": "weekly" if r["rule_type"] == "weekly_summary" else "daily", "text": r["body"] or "", "created_at": r["created_at"]}}
