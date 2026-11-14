"""How one customer's recorders and cameras are doing, for the Health page of a customer in the Control Center. Read only.

Everything here is already known to the server: what the recorders and the analysis worker last reported (see recorder_health.py), the picture checks, the connection history and the
messages. A camera counts as working when its recorder is online and the worker has sent a picture result in the last three minutes."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STALE_SECONDS = 180             # a camera that has not reported for this long is not "working"
DROPS_IN_AN_HOUR = 3            # this many connection drops in an hour is "unstable"
CLOCK_OUT_MINUTES = 5
HEALTH_RULES = ("camera_offline", "recorder_offline", "connection_unstable", "disk_fault", "disk_wear", "recording_stopped", "clock_wrong", "recorder_restarted", "hardware_fault",
                "storage_full", "view_changed", "picture_poor")
PICTURE_WORDS = {"moved": "has moved", "covered": "is covered", "blurry": "is blurry", "dark": "is too dark"}


def _health(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _disks(h: dict) -> dict:
    disks = h.get("disks")
    if disks is None:
        return {"state": None, "count": None, "percent_full": h.get("percent_full")}
    if not disks:
        return {"state": "none", "count": 0, "percent_full": None}
    bad = any(d.get("state") == 2 for d in disks)
    worn = any(d.get("life_percent") is not None and d["life_percent"] <= 20 for d in disks)
    return {"state": "fault" if bad else "wearing" if worn else "ok", "count": len(disks), "percent_full": h.get("percent_full")}


def _history(rows: list, now: datetime) -> list[dict]:
    """The drops and returns of the last day as short lines, newest first: an offline followed by an online is one line ("went offline, back after 3 minutes")."""
    out, down = [], None
    for e in rows:
        if e["status"] == "offline":
            if down is not None:
                out.append({"at": down, "text": "Went offline and was not seen coming back."})
            down = e["at"]
        elif down is not None:
            mins = max(1, round((e["at"] - down).total_seconds() / 60))
            out.append({"at": down, "text": f"Went offline, back online after {mins} minute{'s' if mins != 1 else ''}."})
            down = None
        else:
            out.append({"at": e["at"], "text": "Came back online."})
    if down is not None:
        out.append({"at": down, "text": "Went offline. Not back yet."})
    return sorted(out, key=lambda x: x["at"], reverse=True)[:12]


def _detail(r, h: dict, link_rows: list, chans: list, cam_by_channel: dict, now: datetime) -> dict:
    """Everything the recorder's window shows. A value the recorder did not report is None, never a guess."""
    hw = h.get("hardware") or {}
    now_rec, online = h.get("recording_now") or {}, h.get("channel_online") or {}
    disks = [{"number": d.get("num"), "total_mb": d.get("total_mb"), "free_mb": d.get("free_mb"), "state": "fault" if d.get("state") == 2 else "wearing" if (d.get("life_percent") is not None and d["life_percent"] <= 20) else "ok",
              "life_percent": d.get("life_percent"), "model": d.get("model"), "serial": d.get("serial")} for d in (h.get("disks") or [])]
    clock = h.get("clock") or {}
    return {"brand": r["brand"], "protocol": r["protocol"], "device_id": r["device_id"], "firmware": r["firmware"], "address": r["address"], "channel_count": r["channel_count"],
            "uptime_seconds": h.get("uptime_seconds"), "disks": disks, "clock_local": clock.get("local"), "temperature_c": hw.get("temperature_c"), "fan_ok": hw.get("fan_ok"), "poe_fault": hw.get("poe_fault"),
            "channels": [{"number": c["channel_no"], "name": c["name"], "camera": cam_by_channel.get((r["id"], c["channel_no"])),
                          "online": online.get(str(c["channel_no"])), "recording": now_rec.get(str(c["channel_no"]))} for c in chans],
            "history": _history(link_rows, now)}


async def report(conn) -> dict:
    now = datetime.now(timezone.utc)
    tz = await conn.fetchval("SELECT timezone FROM app.tenants LIMIT 1") or "Asia/Kolkata"
    recs = await conn.fetch(
        """SELECT r.id, coalesce(r.name, r.device_id) AS name, r.device_id, r.brand::text AS brand, r.protocol::text AS protocol, r.firmware, host(r.last_ip) AS address, r.channel_count,
                  r.status::text AS status, r.last_heartbeat_at, r.health, r.health_at,
                  (SELECT count(*) FROM app.recorder_link_events e WHERE e.recorder_id=r.id AND e.status='offline' AND e.at > now() - interval '1 hour')::int AS drops
           FROM app.recorders r ORDER BY 2""")
    events = {}
    for e in await conn.fetch("SELECT recorder_id, status, at FROM app.recorder_link_events WHERE at > now() - interval '24 hours' ORDER BY at"):
        events.setdefault(e["recorder_id"], []).append(e)
    channels = {}
    for ch in await conn.fetch("SELECT recorder_id, channel_no, name FROM app.recorder_channels WHERE active ORDER BY channel_no"):
        channels.setdefault(ch["recorder_id"], []).append(ch)
    cam_by_channel = {}
    cams = await conn.fetch(
        """SELECT c.id, c.name, c.channel_no, c.recorder_id, c.stream_state, c.stream_seen_at, c.last_frame_at, p.bad_view_kind, p.poor_kind
           FROM app.cameras c LEFT JOIN app.camera_picture_state p ON p.camera_id = c.id WHERE c.enabled ORDER BY c.name""")
    by_rec = {r["id"]: r for r in recs}
    for k in cams:
        cam_by_channel[(k["recorder_id"], k["channel_no"])] = k["name"]
    issues, recorders, cameras = [], [], []

    for r in recs:
        h = _health(r["health"])
        disks = _disks(h)
        out = h.get("clock_out_minutes")
        level = "crit" if r["status"] == "offline" else "warn" if (r["drops"] >= DROPS_IN_AN_HOUR or disks["state"] in ("fault", "wearing") or (out or 0) >= CLOCK_OUT_MINUTES) else "ok"
        if r["status"] == "never":
            level = "ok"
        recorders.append({"id": r["id"], "name": r["name"], "status": r["status"], "last_seen": r["last_heartbeat_at"], "level": level, "drops_last_hour": r["drops"],
                          "disks": disks, "clock_out_minutes": out, "oldest_recording": h.get("oldest_recording"), "days_held": h.get("days_held"),
                          "restarts_today": len(h.get("restarts") or []), "temperature_c": (h.get("hardware") or {}).get("temperature_c"), "read_at": r["health_at"],
                          "detail": _detail(r, h, events.get(r["id"], []), channels.get(r["id"], []), cam_by_channel, now)})
        if r["status"] == "offline":
            issues.append({"level": "crit", "text": f"{r['name']} is offline."})
        elif r["drops"] >= DROPS_IN_AN_HOUR:
            issues.append({"level": "warn", "text": f"{r['name']} keeps dropping ({r['drops']} times in the last hour)."})
        if disks["state"] == "fault":
            issues.append({"level": "crit", "text": f"A disk in {r['name']} has failed."})
        elif disks["state"] == "wearing":
            issues.append({"level": "warn", "text": f"A disk in {r['name']} is wearing out."})
        if (out or 0) >= CLOCK_OUT_MINUTES:
            issues.append({"level": "warn", "text": f"The clock of {r['name']} is {out} minutes out."})

    working = 0
    for k in cams:
        rec = by_rec.get(k["recorder_id"])
        h = _health(rec["health"]) if rec else {}
        seen = k["last_frame_at"] or k["stream_seen_at"]
        age = int((now - seen).total_seconds()) if seen else None
        rec_down = bool(rec and rec["status"] == "offline")
        fresh = k["stream_seen_at"] is not None and (now - k["stream_seen_at"]).total_seconds() <= STALE_SECONDS
        ok = not rec_down and k["stream_state"] == "streaming" and fresh
        working += ok
        issue = k["bad_view_kind"] or k["poor_kind"]
        recording = (h.get("recording_now") or {}).get(str(k["channel_no"])) if k["channel_no"] is not None else None
        cameras.append({"id": k["id"], "name": k["name"], "recorder": rec["name"] if rec else None, "working": ok, "recorder_offline": rec_down, "seconds_since_picture": age,
                        "recording": None if recording is None else bool(recording), "picture": issue})
        if not ok and not rec_down and k["stream_state"] != "unknown":
            issues.append({"level": "crit", "text": f"{k['name']} is not sending pictures."})
        if issue and ok:
            issues.append({"level": "warn", "text": f"{k['name']} {PICTURE_WORDS.get(issue, 'has a picture problem')}."})

    msg = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE status IN ('sent','delivered','read'))::int AS sent, count(*) FILTER (WHERE status='failed')::int AS failed,
                  count(*) FILTER (WHERE status='queued' AND created_at < now() - interval '10 minutes')::int AS stuck
           FROM app.notifications WHERE channel='whatsapp' AND created_at > now() - interval '24 hours'""")
    if msg["failed"] and msg["failed"] >= max(3, msg["sent"]):
        issues.append({"level": "warn", "text": "Many WhatsApp messages are failing."})
    if msg["stuck"]:
        issues.append({"level": "warn", "text": f"{msg['stuck']} WhatsApp messages have been waiting for more than 10 minutes."})

    local_today = now.astimezone(ZoneInfo(tz)).date()
    rows = await conn.fetch(
        """SELECT (a.created_at AT TIME ZONE $2)::date AS day, count(*)::int AS n FROM app.alerts a
           WHERE a.rule_type = ANY($1) AND a.created_at > now() - interval '8 days' AND NOT coalesce((a.details->>'test')::boolean, false) GROUP BY 1""", list(HEALTH_RULES), tz)
    by_day = {r["day"]: r["n"] for r in rows}
    days = [{"date": (local_today - timedelta(days=i)).isoformat(), "problems": by_day.get(local_today - timedelta(days=i), 0)} for i in range(6, -1, -1)]

    worst = "crit" if any(i["level"] == "crit" for i in issues) else "warn" if issues else "ok"
    return {"generated_at": now, "overall": worst, "issues": issues, "recorders": recorders, "cameras": cameras,
            "counts": {"recorders": len(recs), "recorders_online": sum(1 for r in recs if r["status"] == "online"), "cameras": len(cams), "cameras_working": working},
            "messages": {"sent_24h": msg["sent"], "failed_24h": msg["failed"], "waiting_too_long": msg["stuck"]}, "days": days}
