"""What a recorder says about itself, and the four alerts that come from it: disk failure, recording stopped, storage low, and a wrong clock.

The Dahua gateway asks each recorder every ten minutes (disks, clock, and for each channel how many recordings it holds for the last two hours and the last day) and
sends the answers here. A reading the recorder could not give is None, and None never raises an alert: not knowing is not the same as a fault."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import detectors
from .alerts import raise_alert

DISK_ERROR = 2                    # the low four bits of a disk's status: 0 asleep, 1 working, 2 faulty
RECENT_HOURS = 2
DAY_COOLDOWN = 24 * 60
HISTORY, STEADY, STOPPED = 12, 4, 2     # readings kept; a channel that recorded in each of the last 4 and in neither of the last 2 has stopped (about 20 minutes)
RESTART_SLACK = 120               # seconds of uptime that can go backwards between two readings without it meaning a restart


def percent_full(disks: list) -> int | None:
    """How full the recorder's working disks are together, in whole per cent. None when no disk has a size (nothing formatted yet)."""
    sized = [d for d in disks if d["state"] != DISK_ERROR and d["total_mb"] > 0]
    total = sum(d["total_mb"] for d in sized)
    if not total:
        return None
    return round(100 * (total - sum(min(d["free_mb"], d["total_mb"]) for d in sized)) / total)


def minutes_out(clock: dict, tz: str, now: datetime | None = None) -> int | None:
    """How far the recorder's clock is from the true time, in minutes. Uses its UTC reading when it gives one, else its own local reading in the customer's time zone."""
    now = now or datetime.now(timezone.utc)
    if clock.get("utc") and clock["utc"] > 1_500_000_000:
        return round(abs(clock["utc"] - now.timestamp()) / 60)
    if clock.get("local"):
        try:
            local = datetime.fromisoformat(clock["local"]).replace(tzinfo=ZoneInfo(tz))
        except ValueError:
            return None
        return round(abs((local - now).total_seconds()) / 60)
    return None


async def apply(conn, rec, h: dict) -> list[str]:
    """Stores the reading and raises whatever it calls for. `rec` is the recorder row (id, tenant_id, device_id, name, timezone). Returns the alert types raised."""
    name = rec["name"] or rec["device_id"]
    raised = []
    prev = await conn.fetchval("SELECT health FROM app.recorders WHERE id=$1", rec["id"]) or {}
    now = datetime.now(timezone.utc)

    # ---- facts worked out once, so the app and Platform Admin can show them without redoing the sums
    disks = h.get("disks")
    h["percent_full"] = percent_full(disks) if disks is not None else None
    h["clock_out_minutes"] = minutes_out(h["clock"], rec["timezone"], now) if h.get("clock") else None
    restarts = [t for t in (prev.get("restarts") or []) if now - datetime.fromisoformat(t) < timedelta(days=1)]
    up, was = h.get("uptime_seconds"), prev.get("uptime_seconds")
    restarted = up is not None and was is not None and up + RESTART_SLACK < was          # the uptime went back down: it started again in between
    if restarted:
        restarts.append(now.isoformat())
    h["restarts"] = restarts
    # which channels are being recorded this moment, remembered for the last twelve readings (two hours): a channel that has recorded steadily and then stops is a fault, whereas a
    # channel that only records on motion switches on and off all day and is never judged this way
    hist = {c: (prev.get("rec_history") or {}).get(c, "")[-(HISTORY - 1):] + ("1" if on else "0") for c, on in (h.get("recording_now") or {}).items()}
    h["rec_history"] = hist
    await conn.execute("UPDATE app.recorders SET health=$2, health_at=now() WHERE id=$1", rec["id"], h)

    async def alert(kind, **kw):
        if await raise_alert(conn, rec["tenant_id"], kind, camera_name=rec["device_id"], **kw):
            raised.append(kind)

    if disks is not None:
        bad = [d for d in disks if d["state"] == DISK_ERROR]
        if bad or not disks:
            what = f"disk {bad[0]['num'] + 1}" if bad else "any hard disk"
            await alert("disk_fault", body=f"{name} reports a problem with {what}. Video may not be saved. Please have the recorder checked.",
                        details={"recorder": name, "disk": f"Disk {bad[0]['num'] + 1}" if bad else "No disk"}, dedupe_key=f"disk:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
        # How full the disks are is shown, never alerted on: a recorder that records all the time and replaces its oldest video is full by design (both of ours read 0 free). What
        # matters is whether it keeps saving (the recording alerts) and how far back it can look (the retention alert). "Storage low" comes from the recorder's own alarm,
        # which it raises only when it is set to stop, not replace, and runs out of room.
        # a disk that is wearing out: warned while it still works, so the video on it can be saved
        worn = int(await detectors._param(conn, rec["tenant_id"], "disk_wear", "percent", 20))
        for d in disks:
            if d.get("life_percent") is not None and d["life_percent"] <= worn and d["state"] != DISK_ERROR:
                left = round(d["life_percent"])
                await alert("disk_wear", body=f"Disk {d['num'] + 1} of {name} has about {left}% of its life left. Replace it soon, before it fails and the video on it is lost.",
                            details={"recorder": name, "disk": f"Disk {d['num'] + 1}", "percent_left": left}, dedupe_key=f"wear:{rec['id']}:{d['num']}", cooldown_minutes=7 * DAY_COOLDOWN)
    if h["clock_out_minutes"] is not None:
        limit = int(await detectors._param(conn, rec["tenant_id"], "clock_wrong", "minutes_out", 5))
        if h["clock_out_minutes"] >= limit:
            await alert("clock_wrong", body=f"{name}'s clock is {h['clock_out_minutes']} minutes out, so its recordings and reports show the wrong times.",
                        details={"recorder": name, "minutes_out": h["clock_out_minutes"]}, dedupe_key=f"clock:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
    silent = [c for c, v in (h.get("recording") or {}).items() if (v.get("day") or 0) > 0 and v.get("recent") == 0]
    stopped = [c for c, s in hist.items() if len(s) >= STEADY + STOPPED and s[-STOPPED:] == "0" * STOPPED and s[-(STEADY + STOPPED):-STOPPED] == "1" * STEADY]
    silent = sorted(set(silent) | set(stopped), key=int)
    if silent:
        await alert("recording_stopped", body=f"{name} has not saved any video from {len(silent)} camera{'s' if len(silent) != 1 else ''} in the last {RECENT_HOURS} hours, although it did earlier. "
                    "Please check the recorder's disk and recording schedule.", details={"recorder": name, "cameras": len(silent)}, dedupe_key=f"recording:{rec['id']}", cooldown_minutes=12 * 60)
    # how many days of video the recorder holds, against what the customer wants to be able to look back on (opt-in: they choose the number)
    if h.get("days_held") is not None:
        target = int(await detectors._param(conn, rec["tenant_id"], "retention_low", "days", 30))
        if h["days_held"] < target:
            await alert("retention_low", body=f"{name} holds about {h['days_held']} days of video. You wanted {target}. Fit bigger disks, or lower the picture quality, to keep more.",
                        details={"recorder": name, "days": h["days_held"], "target": target}, dedupe_key=f"retention:{rec['id']}", cooldown_minutes=7 * DAY_COOLDOWN)
    # restarts: one is told, and three in a day is a power problem
    if restarted:
        n = len(restarts)
        body = (f"{name} has restarted {n} times in the last day. Check its power supply, and consider a UPS (a small battery that rides out power cuts)." if n >= 3
                else f"{name} restarted. If this happens again, check its power supply.")
        await alert("recorder_restarted", body=body, details={"recorder": name, "times": n}, dedupe_key=f"restart:{rec['id']}:{n}", cooldown_minutes=60)
    # hardware the recorder reports on: heat, a stopped fan, a failed camera power port
    hw = h.get("hardware") or {}
    if hw:
        limit = int(await detectors._param(conn, rec["tenant_id"], "hardware_fault", "temperature", 70))
        problems = []
        if hw.get("temperature_c") is not None and hw["temperature_c"] >= limit:
            problems.append(f"it is at {round(hw['temperature_c'])}\u00b0C")
        if hw.get("fan_ok") is False:
            problems.append("a fan has stopped")
        if hw.get("poe_fault") is True:
            problems.append("a camera power port has failed")
        if problems:
            await alert("hardware_fault", body=f"{name}: {' and '.join(problems)}. Heat shortens a recorder's life: check the air flow around it.",
                        details={"recorder": name, "what": ", ".join(problems)}, dedupe_key=f"hardware:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
    return raised


async def apply_alarms(conn, rec, events: list[dict]) -> list[str]:
    """What the recorder announced by itself. Each becomes the same alert a reading would raise, but at once instead of at the next check.
    A disk or storage alarm is the recorder's own word, so it needs no second reading to confirm it."""
    name = rec["name"] or rec["device_id"]
    raised = []

    async def alert(kind, **kw):
        if await raise_alert(conn, rec["tenant_id"], kind, camera_name=kw.pop("camera", rec["device_id"]), **kw):
            raised.append(kind)

    for e in events:
        k = e["kind"]
        if k in ("disk_error", "disk_missing"):
            which = f"disk {e['disk'] + 1}" if e.get("disk") is not None else "a disk"
            await alert("disk_fault", body=f"{name} reports a problem with {which}. Video may not be saved. Please have the recorder checked.",
                        details={"recorder": name, "disk": which.capitalize()}, dedupe_key=f"disk:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
        elif k == "storage_low":
            pct = f"{e['percent']}% full" if e.get("percent") is not None else "out of room"
            await alert("storage_full", body=f"{name} says its storage is {pct} and it is not replacing its oldest video, so new video may not be saved.",
                        details={"recorder": name, "percent_full": e.get("percent") or ""}, dedupe_key=f"storage:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
        elif k in ("video_tamper", "video_loss") and e.get("channel"):
            cam = await conn.fetchrow("SELECT id, name FROM app.cameras WHERE tenant_id=$1 AND recorder_id=$2 AND channel_no=$3", rec["tenant_id"], rec["id"], e["channel"])
            if not cam:
                continue                                         # a channel the customer has not turned into a camera: nothing to tell them
            if k == "video_tamper":
                await alert("view_changed", title=f"Camera covered: {cam['name']}", body=f"The recorder says {cam['name']} has been blocked or turned away. Check that nothing is in front of it.",
                            camera_id=cam["id"], camera=cam["name"], details={"what": "covered"}, dedupe_key=f"view:{cam['id']}", cooldown_minutes=DAY_COOLDOWN)
            else:
                await alert("camera_offline", body=f"The recorder reports that it has lost the picture from {cam['name']}. Check the camera's power and cable.",
                            camera_id=cam["id"], camera=cam["name"], dedupe_key=f"camera_offline:{cam['id']}", cooldown_minutes=60)
        elif k == "reboot":
            await alert("recorder_restarted", body=f"{name} restarted. If this happens again, check its power supply.", details={"recorder": name, "times": 1},
                        dedupe_key=f"restart:{rec['id']}:alarm", cooldown_minutes=60)
    return raised
