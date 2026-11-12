"""What a recorder says about itself, and the four alerts that come from it: disk failure, recording stopped, storage low, and a wrong clock.

The Dahua gateway asks each recorder every ten minutes (disks, clock, and for each channel how many recordings it holds for the last two hours and the last day) and
sends the answers here. A reading the recorder could not give is None, and None never raises an alert: not knowing is not the same as a fault."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import detectors
from .alerts import raise_alert

DISK_ERROR = 2                    # the low four bits of a disk's status: 0 asleep, 1 working, 2 faulty
RECENT_HOURS = 2
DAY_COOLDOWN = 24 * 60


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
    await conn.execute("UPDATE app.recorders SET health=$2, health_at=now() WHERE id=$1", rec["id"], h)

    async def alert(kind, **kw):
        if await raise_alert(conn, rec["tenant_id"], kind, camera_name=rec["device_id"], **kw):
            raised.append(kind)

    disks = h.get("disks")
    if disks is not None:
        bad = [d for d in disks if d["state"] == DISK_ERROR]
        if bad or not disks:
            what = f"disk {bad[0]['num'] + 1}" if bad else "any hard disk"
            await alert("disk_fault", body=f"{name} reports a problem with {what}. Video may not be saved. Please have the recorder checked.",
                        details={"recorder": name, "disk": f"Disk {bad[0]['num'] + 1}" if bad else "No disk"}, dedupe_key=f"disk:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
        full = percent_full(disks)
        limit = int(await detectors._param(conn, rec["tenant_id"], "storage_full", "percent", 90))
        if full is not None and full >= limit:
            await alert("storage_full", body=f"{name} is {full}% full. When it is full it starts to overwrite its oldest video, or stops saving.",
                        details={"recorder": name, "percent_full": full}, dedupe_key=f"storage:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
    if h.get("clock"):
        out = minutes_out(h["clock"], rec["timezone"])
        limit = int(await detectors._param(conn, rec["tenant_id"], "clock_wrong", "minutes_out", 5))
        if out is not None and out >= limit:
            await alert("clock_wrong", body=f"{name}'s clock is {out} minutes out, so its recordings and reports show the wrong times.",
                        details={"recorder": name, "minutes_out": out}, dedupe_key=f"clock:{rec['id']}", cooldown_minutes=DAY_COOLDOWN)
    silent = [c for c, v in (h.get("recording") or {}).items() if (v.get("day") or 0) > 0 and v.get("recent") == 0]
    if silent:
        await alert("recording_stopped", body=f"{name} has not saved any video from {len(silent)} camera{'s' if len(silent) != 1 else ''} in the last {RECENT_HOURS} hours, although it did earlier. "
                    "Please check the recorder's disk and recording schedule.", details={"recorder": name}, dedupe_key=f"recording:{rec['id']}", cooldown_minutes=12 * 60)
    return raised
