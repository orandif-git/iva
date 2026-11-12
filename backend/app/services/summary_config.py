"""The settings of the daily and weekly summary, kept in one row (platform.summary_settings): how to reach the WhatsApp provider, when summaries go out and what they say.
Platform Admin changes them. Anything missing from what is stored takes the value below, so a new setting never needs a migration."""
import copy
import json

from ..crypto import vault

KEY_AAD = "summary-direct-send"

DEFAULT_LINES = {
    "daily": [
        {"g": "business", "id": "visitors", "on": True, "req": "counting", "t": "{visitors} visitors yesterday ({change} on last {weekday})"},
        {"g": "business", "id": "busiest", "on": True, "req": "counting", "t": "Busiest hour: {busy_hour} ({busy_people} people)"},
        {"g": "business", "id": "peak", "on": True, "req": "counting", "t": "Peak inside at once: {peak} people{limit}"},
        {"g": "business", "id": "veh", "on": True, "req": "plates", "t": "Vehicles: {veh_in} in, {veh_parked} still parked"},
        {"g": "business", "id": "queue", "on": True, "req": "queue", "t": "{queue_area}: longest queue {queue} people"},
        {"g": "system", "id": "dev", "on": True, "req": "", "t": "{cams_ok} of {cams} cameras working · {recs_ok} of {recs} recorders online"},
        {"g": "system", "id": "kept", "on": True, "req": "", "t": "Video kept: {days} days"},
        {"g": "system", "id": "health", "on": True, "req": "", "t": "Disks {disks} · Clocks {clocks}"},
        {"g": "system", "id": "alerts", "on": True, "req": "", "t": "{alerts} alerts in the last 24 hours, {unseen} not yet seen"},
        {"g": "system", "id": "hours", "on": True, "req": "", "t": "{hours}"},
    ],
    "weekly": [
        {"g": "business", "id": "visitors", "on": True, "req": "counting", "t": "{visitors_w} visitors ({change_w} on the week before)"},
        {"g": "business", "id": "bday", "on": True, "req": "counting", "t": "Busiest day: {busy_day} ({busy_day_n})"},
        {"g": "business", "id": "busiest", "on": True, "req": "counting", "t": "Busiest hour: {busy_hour}"},
        {"g": "business", "id": "veh", "on": True, "req": "plates", "t": "Vehicles: {veh_w} in"},
        {"g": "system", "id": "dev", "on": True, "req": "", "t": "{cams_ok} of {cams} cameras working · {recs_ok} of {recs} recorders online"},
        {"g": "system", "id": "prob", "on": True, "req": "", "t": "{problems_n} problems this week"},
        {"g": "system", "id": "kept", "on": True, "req": "", "t": "Video kept: {days} days"},
        {"g": "system", "id": "hours", "on": True, "req": "", "t": "{hours}"},
    ],
}

DEFAULTS = {
    # the connection
    "url": "", "source": "ivision-summary", "category": "utility", "timeout": 15, "retries": 3, "gap": 1.0, "maxchars": 1000, "enabled": True,
    # when
    "daily_on": True, "weekly_on": True, "daily_hour": "08:00", "weekly_day": 0, "weekly_hour": "08:00", "tz": "customer", "replace_daily": True, "cust_edit": True, "default_on": True,
    # who
    "recips": "owner", "skip_inactive": True, "skip_no_number": True,
    # what
    "title": "📊 {kind} summary · {date} · {customer}", "date_style": "num", "heads": ["BUSINESS", "SYSTEM", "NEEDS A LOOK"], "bullet": "- ",
    "max_problems": 3, "all_fine": "Everything is fine.", "closing": "Open the Jas iVision app for details.", "show_limit": True,
    "push_on": True, "push_ta": True, "lines": DEFAULT_LINES,
}


def merged(stored: dict | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for k, v in (stored or {}).items():
        if k in cfg and k != "lines":
            cfg[k] = v
    # a stored list of lines replaces the default one, but a line the code now has and the stored list lacks is added, so a new line is never lost
    got = (stored or {}).get("lines") or {}
    for kind in ("daily", "weekly"):
        have = {l.get("id") + l.get("g", "") for l in got.get(kind, []) if isinstance(l, dict)}
        lines = [l for l in got.get(kind, []) if isinstance(l, dict) and l.get("t")]
        lines += [copy.deepcopy(l) for l in DEFAULT_LINES[kind] if l["id"] + l["g"] not in have]
        cfg["lines"][kind] = lines or copy.deepcopy(DEFAULT_LINES[kind])
    return cfg


async def load(conn) -> dict:
    row = await conn.fetchrow("SELECT config FROM platform.summary_settings WHERE id")
    stored = row["config"] if row else {}
    return merged(json.loads(stored) if isinstance(stored, str) else stored)


async def key(conn) -> str | None:
    row = await conn.fetchrow("SELECT key_ciphertext FROM platform.summary_settings WHERE id")
    return vault().decrypt(bytes(row["key_ciphertext"]), KEY_AAD) if row and row["key_ciphertext"] else None


async def key_hint(conn) -> dict:
    row = await conn.fetchrow("SELECT key_ciphertext IS NOT NULL AS has, key_hint, key_changed_at, key_changed_by FROM platform.summary_settings WHERE id")
    return {"set": bool(row and row["has"]), "hint": row["key_hint"] if row else None, "changed_at": row["key_changed_at"] if row else None, "changed_by": row["key_changed_by"] if row else None}


def ready(cfg: dict, has_key: bool, kind: str) -> bool:
    """Can a summary of this kind be sent straight to WhatsApp right now: the connection is complete and this kind is switched on."""
    return bool(cfg["enabled"] and cfg["url"] and has_key and cfg["daily_on" if kind == "daily" else "weekly_on"])
