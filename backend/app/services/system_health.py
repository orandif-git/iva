"""How our own system is doing: the server, the services, the camera pipeline, messages and storage.

Everything here reads what is already there (the server's own counters, the workers' check-ins, the database). A small sample is saved every few minutes so the
page can show the last day and say how long the disk will last. Nothing here changes any customer's data."""
import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..config import get_settings
from ..db import db
from ..messaging import evidence

log = logging.getLogger("gateinsight.system_health")
SAMPLE_SECONDS = 300
KEEP_DAYS = 8
WORKER_OK_SECONDS, WORKER_SLOW_SECONDS = 150, 600
SILENT_SECONDS = 600
LIMITS = {"disk": (80, 90), "mem": (85, 95), "load": (70, 90)}       # amber from, red from (per cent)
BUSY_TABLES = {"footfall_minutes": "People counts", "plate_reads": "Vehicle reads", "notifications": "Messages", "alerts": "Alerts", "zone_samples": "Area readings",
               "audit_log": "Activity log", "camera_pictures": "Camera pictures", "recorder_health_log": "Recorder readings", "tenants": "Customers", "staff_users": "Console users", "auth_tokens": "Sign-ins", "footfall_counts": "People counts"}


def level(value: float, kind: str) -> str:
    warn, crit = LIMITS[kind]
    return "crit" if value >= crit else "warn" if value >= warn else "ok"


def worst(*levels: str) -> str:
    return "crit" if "crit" in levels else "warn" if "warn" in levels else "ok"


# ----------------------------------------------------------------------------------------------------- the server itself
def _meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                out[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    return out


def host() -> dict:
    """Processor, memory and disk of this machine. Values are None where the machine does not say (for example on a laptop)."""
    cpus = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    m = _meminfo()
    mem_pct = round((1 - m["MemAvailable"] / m["MemTotal"]) * 100, 1) if m.get("MemTotal") and "MemAvailable" in m else None
    path = get_settings().evidence_local_dir
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    d = shutil.disk_usage(path or "/")
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    except OSError:
        up = None
    return {"cpus": cpus, "load_pct": round(min(999.0, load1 / cpus * 100), 1) if load1 is not None else None, "mem_pct": mem_pct,
            "mem_total_gb": round(m["MemTotal"] / 1e9, 1) if m.get("MemTotal") else None,
            "disk_pct": round(d.used / d.total * 100, 1), "disk_total_gb": round(d.total / 1e9, 1), "disk_free_gb": round(d.free / 1e9, 1), "uptime_seconds": up}


def _folder_mb(path: str, budget: float = 15.0) -> float | None:
    """Size of the evidence folder on this server's disk. Gives up after a few seconds on a huge folder and says nothing then."""
    if not os.path.isdir(path):
        return 0.0
    end, total = time.monotonic() + budget, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
        if time.monotonic() > end:
            return None
    return round(total / 1e6, 1)


async def _reachable(url: str) -> bool:
    u = urlparse(url)
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(u.hostname, u.port or (443 if u.scheme == "https" else 80)), 1.0)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _ago(ts) -> str | None:
    return ts.astimezone(timezone.utc).isoformat() if ts else None


# ------------------------------------------------------------------------------------------------------------ the report
async def cameras(conn) -> dict:
    row = await conn.fetchrow(
        f"""SELECT count(*) AS expected,
                   count(*) FILTER (WHERE c.stream_state='streaming' AND c.stream_seen_at > now() - interval '2 minutes') AS streaming,
                   count(*) FILTER (WHERE c.stream_state='offline' AND c.stream_seen_at > now() - interval '{SILENT_SECONDS} seconds') AS offline,
                   avg(c.analysis_fps) FILTER (WHERE c.stream_state='streaming' AND c.stream_seen_at > now() - interval '2 minutes') AS fps
            FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id
            WHERE c.enabled AND t.status NOT IN ('suspended','cancelled') AND (c.use_footfall OR c.use_anpr OR c.use_areas)""")
    silent = await conn.fetch(
        f"""SELECT c.name AS camera, t.name AS customer, c.stream_seen_at FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id
            WHERE c.enabled AND t.status NOT IN ('suspended','cancelled') AND (c.use_footfall OR c.use_anpr OR c.use_areas)
              AND (c.stream_seen_at IS NULL OR c.stream_seen_at < now() - interval '{SILENT_SECONDS} seconds') ORDER BY c.stream_seen_at NULLS FIRST, t.name LIMIT 10""")
    return {"expected": row["expected"], "streaming": row["streaming"], "offline": row["offline"], "fps": round(float(row["fps"]), 1) if row["fps"] is not None else None,
            "silent": [{"camera": r["camera"], "customer": r["customer"], "last_seen": _ago(r["stream_seen_at"])} for r in silent]}


async def worker_state(conn) -> dict:
    """The analysis worker as the server sees it: running when it asked for its cameras in the last two and a half minutes."""
    w = await conn.fetchrow("SELECT max(seen_at) AS seen, extract(epoch FROM now() - max(seen_at)) AS age FROM platform.worker_seen")
    if not w["seen"]:
        return {"level": "crit", "text": "Has not reported yet", "last_seen": None}
    age = float(w["age"])
    return {"level": "ok" if age <= WORKER_OK_SECONDS else "warn" if age <= WORKER_SLOW_SECONDS else "crit",
            "text": "Running" if age <= WORKER_OK_SECONDS else "Slow to report" if age <= WORKER_SLOW_SECONDS else "Not reporting", "last_seen": _ago(w["seen"])}


async def status(conn) -> dict:
    """The few numbers behind the status lights in the top bar and the server strip on the Overview. Cheap, and safe for every console user."""
    h = host()
    cams = await cameras(conn)
    w = await worker_state(conn)
    waiting = await conn.fetchrow("SELECT count(*)::int AS n, extract(epoch FROM now() - min(created_at))::float8 AS age FROM app.notifications WHERE status='queued' AND next_attempt_at < now() - interval '2 minutes'")
    days = await disk_days_left(conn, h["disk_pct"])
    load = h["load_pct"]
    cam_level = "crit" if w["level"] == "crit" and cams["expected"] else "crit" if cams["silent"] else "warn" if cams["streaming"] != cams["expected"] else "ok"
    stuck = int(waiting["age"] // 60) if waiting["age"] else 0
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "cpu": {"pct": load, "level": level(load, "load") if load is not None else "ok", "cpus": h["cpus"]},
            "memory": {"pct": h["mem_pct"], "level": level(h["mem_pct"], "mem") if h["mem_pct"] is not None else "ok", "total_gb": h["mem_total_gb"]},
            "disk": {"pct": h["disk_pct"], "level": level(h["disk_pct"], "disk") if (days is None or days >= 7) else "warn" if days >= 2 else "crit", "days_left": days, "free_gb": h["disk_free_gb"]},
            "worker": w,
            "cameras": {"analysed": cams["streaming"], "expected": cams["expected"], "silent": len(cams["silent"]), "fps": cams["fps"], "level": cam_level},
            "messages": {"waiting": waiting["n"], "oldest_minutes": stuck or None, "level": "crit" if stuck >= 10 else "warn" if waiting["n"] else "ok"}}


async def services(conn) -> list[dict]:
    s = get_settings()
    out = [{"name": "Control Center and API", "level": "ok", "text": "Running", "last_seen": _ago(datetime.now(timezone.utc))}]
    out.append({"name": "Analysis worker", **await worker_state(conn)})
    for name, url, proto in (("Dahua gateway", s.dahua_relay_url, "auto_register"), ("Recorder registrar", s.gb_relay_url, "gb28181")):
        up = await _reachable(url)
        seen = await conn.fetchval("SELECT max(last_heartbeat_at) FROM app.recorders WHERE protocol=$1::app.recorder_protocol", proto)
        out.append({"name": name, "level": "ok" if up else "crit", "text": "Running" if up else "Not answering", "last_seen": _ago(seen)})
    t0 = time.monotonic()
    await conn.fetchval("SELECT 1")
    out.append({"name": "Database", "level": "ok", "text": "Running", "last_seen": _ago(datetime.now(timezone.utc)), "ms": round((time.monotonic() - t0) * 1000, 1)})
    return out


async def messages(conn) -> dict:
    c = {r["status"]: r["n"] for r in await conn.fetch("SELECT status::text AS status, count(*) AS n FROM app.notifications WHERE created_at > now() - interval '24 hours' GROUP BY 1")}
    old = await conn.fetchval("SELECT extract(epoch FROM now() - min(created_at))::float8 FROM app.notifications WHERE status='queued' AND next_attempt_at < now() - interval '2 minutes'")
    hours = {r["h"]: r["n"] for r in await conn.fetch(
        "SELECT floor(extract(epoch FROM now() - created_at) / 3600)::int AS h, count(*) AS n FROM app.notifications WHERE created_at > now() - interval '24 hours' AND status IN ('sent','delivered','read') GROUP BY 1")}
    sent = sum(c.get(k, 0) for k in ("sent", "delivered", "read"))
    failed, wait = c.get("failed", 0), c.get("queued", 0)
    stuck = int(old // 60) if old else 0
    lv = "crit" if stuck >= 10 else "warn" if (failed >= 5 and failed * 10 > sent + failed) else "ok"
    return {"level": lv, "sent": sent, "failed": failed, "waiting": wait, "oldest_waiting_minutes": stuck or None, "per_hour": [hours.get(23 - i, 0) for i in range(24)]}


async def storage(conn) -> dict:
    s = get_settings()
    last = await conn.fetchrow("SELECT evidence_mb FROM platform.health_samples WHERE evidence_mb IS NOT NULL ORDER BY ts DESC LIMIT 1")
    db_mb = await conn.fetchval("SELECT pg_database_size(current_database())::float8 / 1e6")
    tabs = await conn.fetch(
        """SELECT coalesce(pp.relname, c.relname) AS name, sum(pg_total_relation_size(c.oid)) AS bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           LEFT JOIN pg_inherits i ON i.inhrelid=c.oid LEFT JOIN pg_class pp ON pp.oid=i.inhparent
           WHERE c.relkind='r' AND n.nspname IN ('app','platform') GROUP BY 1 ORDER BY 2 DESC LIMIT 4""")
    cloud = type(evidence.store()).__name__ == "R2Store"
    return {"evidence_mb": float(last["evidence_mb"]) if last else None, "cloud": cloud, "database_mb": round(float(db_mb), 1),
            "biggest": [{"name": BUSY_TABLES.get(r["name"], r["name"].replace("_", " ").capitalize()), "mb": round(float(r["bytes"]) / 1e6, 1)} for r in tabs]}


async def trend(conn) -> dict:
    rows = await conn.fetch(
        """SELECT floor(extract(epoch FROM now() - ts) / 3600)::int AS h, avg(disk_pct) AS disk, avg(load_pct) AS load, avg(mem_pct) AS mem FROM platform.health_samples
           WHERE ts > now() - interval '24 hours' GROUP BY 1 ORDER BY 1 DESC""")
    pick = lambda k: [round(float(r[k]), 1) if r[k] is not None else None for r in rows]
    return {"disk": pick("disk"), "load": pick("load"), "mem": pick("mem")}


async def disk_days_left(conn, now_pct: float) -> float | None:
    """How many days until the disk is full, if it keeps filling at the pace of the last two days. None when it is steady or there is too little history."""
    rows = await conn.fetch("SELECT extract(epoch FROM ts)::float8 AS t, disk_pct::float8 AS d FROM platform.health_samples WHERE ts > now() - interval '48 hours' AND disk_pct IS NOT NULL ORDER BY ts")
    if len(rows) < 12 or rows[-1]["t"] - rows[0]["t"] < 3 * 3600:
        return None
    n = len(rows)
    mx, my = sum(r["t"] for r in rows) / n, sum(r["d"] for r in rows) / n
    den = sum((r["t"] - mx) ** 2 for r in rows)
    slope = sum((r["t"] - mx) * (r["d"] - my) for r in rows) / den if den else 0           # per cent per second
    per_day = slope * 86400
    if per_day < 0.05:
        return None
    return round(max(0.0, 100 - now_pct) / per_day, 1)


async def report(conn) -> dict:
    h = host()
    cams, svcs, msgs = await cameras(conn), await services(conn), await messages(conn)
    store, tr = await storage(conn), await trend(conn)
    days = await disk_days_left(conn, h["disk_pct"])
    load = h["load_pct"]
    room = None
    if load and cams["streaming"] and load >= 5:
        room = max(0, int(cams["streaming"] * LIMITS["load"][0] / load - cams["streaming"]))
    cap_level = level(load, "load") if load is not None else "ok"
    disk_level = level(h["disk_pct"], "disk")
    if days is not None and days < 7:
        disk_level = worst(disk_level, "warn")
    if days is not None and days < 2:
        disk_level = "crit"
    cam_level = "ok" if cams["streaming"] == cams["expected"] else ("crit" if cams["silent"] else "warn")
    host_level = worst(disk_level, level(h["mem_pct"], "mem") if h["mem_pct"] is not None else "ok", cap_level)

    todo: list[dict] = []
    for x in svcs:
        if x["level"] != "ok":
            todo.append({"level": x["level"], "text": f'{x["name"]}: {x["text"].lower()}. ' + ("Restart it on the server." if x["level"] == "crit" else "Watch it for a few minutes.")})
    if disk_level != "ok":
        left = f", about {days:g} days left at this pace" if days is not None else ""
        todo.append({"level": disk_level, "text": f'The disk is {h["disk_pct"]:g}% full{left}. Lower "Data kept" in Setup or add storage.'})
    if h["mem_pct"] is not None and level(h["mem_pct"], "mem") != "ok":
        todo.append({"level": level(h["mem_pct"], "mem"), "text": f'Memory is {h["mem_pct"]:g}% used. Restart the services if it keeps rising.'})
    if cap_level != "ok":
        todo.append({"level": cap_level, "text": f"The server is {load:g}% busy. Do not add cameras until this comes down."})
    if cams["silent"]:
        todo.append({"level": "crit", "text": f'{len(cams["silent"])} camera{"s have" if len(cams["silent"]) > 1 else " has"} sent nothing for 10 minutes: ' + ", ".join(f'{x["camera"]} ({x["customer"]})' for x in cams["silent"][:3]) + ("…" if len(cams["silent"]) > 3 else "") + "."})
    elif cams["streaming"] != cams["expected"]:
        todo.append({"level": "warn", "text": f'{cams["expected"] - cams["streaming"]} of {cams["expected"]} cameras are not being analysed right now.'})
    if msgs["level"] != "ok":
        todo.append({"level": msgs["level"], "text": (f'{msgs["waiting"]} messages are waiting, the oldest for {msgs["oldest_waiting_minutes"]} minutes. ' if msgs["oldest_waiting_minutes"] else f'{msgs["failed"]} messages failed in the last 24 hours. ') + "Check the WhatsApp connection under Setup."})
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "overall": worst(*(t["level"] for t in todo)), "todo": todo,
            "services": svcs, "server": {**h, "level": host_level, "disk_level": disk_level, "mem_level": level(h["mem_pct"], "mem") if h["mem_pct"] is not None else "ok", "load_level": cap_level, "disk_days_left": days},
            "cameras": {**cams, "level": cam_level}, "messages": msgs, "storage": store, "capacity": {"level": cap_level, "load_pct": load, "cameras": cams["streaming"], "room": room}, "trend": tr}


# ------------------------------------------------------------------------------------------------------- the saved samples
async def take_sample() -> None:
    h = host()
    evidence_mb = None
    if type(evidence.store()).__name__ != "R2Store":
        evidence_mb = await asyncio.to_thread(_folder_mb, get_settings().evidence_local_dir)
    async with db.service_tx() as conn:
        cams = await conn.fetchrow(
            """SELECT count(*) AS e, count(*) FILTER (WHERE c.stream_state='streaming' AND c.stream_seen_at > now() - interval '2 minutes') AS s
               FROM app.cameras c JOIN app.tenants t ON t.id=c.tenant_id WHERE c.enabled AND t.status NOT IN ('suspended','cancelled') AND (c.use_footfall OR c.use_anpr OR c.use_areas)""")
        db_mb = await conn.fetchval("SELECT pg_database_size(current_database())::float8 / 1e6")
        await conn.execute(
            "INSERT INTO platform.health_samples (disk_pct, mem_pct, load_pct, cameras_expected, cameras_streaming, evidence_mb, db_mb) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (ts) DO NOTHING",
            h["disk_pct"], h["mem_pct"], h["load_pct"], cams["e"], cams["s"], evidence_mb, float(db_mb))
        await conn.execute("DELETE FROM platform.health_samples WHERE ts < now() - make_interval(days => $1)", KEEP_DAYS)


async def run_forever() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await take_sample()
        except Exception:
            log.exception("could not save a health sample")
        await asyncio.sleep(SAMPLE_SECONDS)
