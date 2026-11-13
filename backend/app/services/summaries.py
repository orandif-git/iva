"""The daily and weekly summary: one message that tells the owner how the business did and how the system is, written for each customer from what they have.

BUSINESS         visitors, busiest hour, most inside at once, vehicles, longest queue. A line appears only when the customer has the feature behind it, set up and switched on.
SYSTEM           cameras, recorders, days of video kept, disks, clocks, alerts, opening hours. Everyone gets this.
NEEDS A LOOK     problems from the alerts the customer has switched on. When there are none the message says everything is fine.

The text is written here and sent straight to WhatsApp (Direct Send, see messaging/direct.py and the delivery service). What the message says, and when it goes, is set in Platform Admin (summary_config.py)."""
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..db import db
from . import alert_catalog, summary_config
from .alerts import DEFAULT_ENABLED, raise_alert
from .opening_hours import Hours

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# the alerts whose problems are listed under "Needs a look"
NEEDS_LOOK = ("camera_offline", "recorder_offline", "connection_unstable", "disk_fault", "disk_wear", "recording_stopped", "clock_wrong", "recorder_restarted", "hardware_fault",
              "storage_full", "view_changed", "picture_poor", "counting_stopped", "retention_low")
ALLOW_EMPTY = {"limit"}


# ------------------------------------------------------------------------------------------------ small helpers
def clock12(t) -> str:
    h = t.hour % 12 or 12
    return f"{h}{'' if t.minute == 0 else f':{t.minute:02d}'} {'AM' if t.hour < 12 else 'PM'}"


def hour_span(h: int) -> str:
    def lab(x):
        x %= 24
        return x % 12 or 12, "AM" if x < 12 else "PM"
    a, am = lab(h)
    b, bm = lab(h + 1)
    return f"{a} to {b} {bm}" if am == bm else f"{a} {am} to {b} {bm}"


def change_text(now: int, before: int) -> str:
    if not before:
        return ""
    pct = round((now - before) * 100 / before)
    return f"{pct:+d}%" if pct else "no change"


def hours_text(t) -> str:
    h = Hours.of(t)
    if not h.set:
        return ""
    days = [WEEKDAYS[d] + "s" for d in sorted(h.closed_days)]
    shut = "open every day" if not days else "closed " + (days[0] if len(days) == 1 else ", ".join(days[:-1]) + " and " + days[-1])
    return f"Open {clock12(h.open)} to {clock12(h.close)} · {shut}"


def fill(template: str, v: dict) -> str | None:
    """The template with its {fill-ins} replaced. A bracket that holds a missing figure is dropped ("(+8% on last Wednesday)" when there is nothing to compare with),
    and a line that is missing a figure it needs is left out altogether (None)."""
    def drop_empty_group(m):
        keys = re.findall(r"\{(\w+)\}", m.group(2))
        return "" if any(str(v.get(k, "")) == "" and k not in ALLOW_EMPTY for k in keys) else m.group(0)
    out = re.sub(r"(\s*)(\([^()]*\{\w+\}[^()]*\))", drop_empty_group, template)
    for k in re.findall(r"\{(\w+)\}", out):
        if str(v.get(k, "")) == "" and k not in ALLOW_EMPTY:
            return None
    out = re.sub(r"\{(\w+)\}", lambda m: str(v.get(m.group(1), "")), out)
    return re.sub(r"\s{2,}", " ", out).strip()


async def enabled_types(conn, tenant_id) -> set[str]:
    """The alerts this customer can have and has switched on (an alert they never touched is on when it is on by default)."""
    vis = set(await alert_catalog.visible(conn, tenant_id))
    rules = {r["rule_type"]: r["enabled"] for r in await conn.fetch("SELECT rule_type, enabled FROM app.alert_rules WHERE tenant_id=$1", tenant_id)}
    return {k for k in vis if rules.get(k, k in DEFAULT_ENABLED)}


# ------------------------------------------------------------------------------------------------ gathering
async def _footfall(conn, tid, tz: str, a: datetime, b: datetime) -> dict:
    rows = await conn.fetch("SELECT bucket_start, sum(people_in)::int AS i, sum(people_out)::int AS o FROM app.footfall_counts WHERE tenant_id=$1 AND bucket_start >= $2 AND bucket_start < $3 "
                            "GROUP BY bucket_start ORDER BY bucket_start", tid, a, b)
    zone = ZoneInfo(tz)
    by_hour, by_day, run, peak = {}, {}, 0, 0
    for r in rows:
        local = r["bucket_start"].astimezone(zone)
        by_hour[local.hour] = by_hour.get(local.hour, 0) + r["i"]
        by_day[local.date()] = by_day.get(local.date(), 0) + r["i"]
        run = max(0, run + r["i"] - r["o"])
        peak = max(peak, run)
    return {"total": sum(by_day.values()), "by_hour": by_hour, "by_day": by_day, "peak": peak}


async def gather(conn, t, kind: str, today: date) -> dict:
    """Everything a summary can say, for one customer, with the features they have. `t` is their tenant row."""
    tid, tz = t["id"], t["timezone"]
    zone = ZoneInfo(tz)
    first = today - timedelta(days=1 if kind == "daily" else 7)
    start, end = datetime.combine(first, datetime.min.time(), zone), datetime.combine(today, datetime.min.time(), zone)
    span = (end - start)
    on = await enabled_types(conn, tid)
    v: dict = {}
    caps = {"counting": False, "plates": False, "queue": False, "limit": False}

    # ---- business
    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM app.cameras WHERE tenant_id=$1 AND enabled AND use_footfall)", tid):
        now = await _footfall(conn, tid, tz, start, end)
        before = await _footfall(conn, tid, tz, start - span if kind == "weekly" else start - timedelta(days=7), end - span if kind == "weekly" else end - timedelta(days=7))
        if kind == "daily":
            caps["counting"] = now["total"] > 0 or before["total"] > 0
            v.update(visitors=now["total"], change=change_text(now["total"], before["total"]), weekday=WEEKDAYS[first.weekday()], peak=now["peak"])
        else:
            caps["counting"] = now["total"] > 0
            v.update(visitors_w=f"{now['total']:,}", change_w=change_text(now["total"], before["total"]))
            if now["by_day"]:
                day, n = max(now["by_day"].items(), key=lambda kv: kv[1])
                v.update(busy_day=WEEKDAYS[day.weekday()], busy_day_n=n)
        if now["by_hour"]:
            h, n = max(now["by_hour"].items(), key=lambda kv: kv[1])
            v.update(busy_hour=hour_span(h), busy_people=n)
        crowd = await conn.fetchrow("SELECT enabled, params FROM app.alert_rules WHERE tenant_id=$1 AND rule_type='crowding'", tid)
        if "crowding" in on and crowd and crowd["enabled"]:
            lim = (crowd["params"] if isinstance(crowd["params"], dict) else json.loads(crowd["params"] or "{}")).get("people")
            caps["limit"] = bool(lim)
            v["limit_n"] = lim
    anpr = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM app.cameras WHERE tenant_id=$1 AND enabled AND use_anpr)", tid)
    if anpr and ({"blocked_plate", "vehicle_overstay", "low_confidence_plate"} & on):
        n_in = await conn.fetchval("SELECT count(*)::int FROM app.plate_events WHERE tenant_id=$1 AND seen_at >= $2 AND seen_at < $3 AND direction='in' AND plate_text IS NOT NULL", tid, start, end)
        parked = await conn.fetchval(
            """SELECT count(*)::int FROM app.plate_events e WHERE e.tenant_id=$1 AND e.direction='in' AND e.plate_text IS NOT NULL AND e.seen_at > now() - interval '2 days'
               AND NOT EXISTS (SELECT 1 FROM app.plate_events o WHERE o.tenant_id=e.tenant_id AND o.entry_event_id = e.id)""", tid)
        caps["plates"] = n_in > 0
        v.update(veh_in=n_in, veh_w=n_in, veh_parked=parked)
    if "queue_long" in on:
        q = await conn.fetchrow(
            """SELECT z.name, max(s.people)::int AS n FROM app.zone_samples s JOIN app.camera_zones z ON z.id = s.zone_id
               WHERE s.tenant_id=$1 AND z.kind='queue_area' AND s.at >= $2 AND s.at < $3 GROUP BY z.id, z.name ORDER BY max(s.people) DESC LIMIT 1""", tid, start, end)
        if q and q["n"]:
            caps["queue"] = True
            v.update(queue_area=q["name"] or "Queue", queue=q["n"])

    # ---- system
    s = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled)::int AS cams,
                  (SELECT count(*) FROM app.cameras WHERE tenant_id=$1 AND enabled AND stream_state='streaming')::int AS ok,
                  (SELECT count(*) FROM app.recorders WHERE tenant_id=$1)::int AS recs, (SELECT count(*) FROM app.recorders WHERE tenant_id=$1 AND status='online')::int AS online,
                  (SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type NOT IN ('daily_summary','weekly_summary') AND created_at > now() - interval '24 hours')::int AS alerts,
                  (SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type NOT IN ('daily_summary','weekly_summary') AND acknowledged_at IS NULL)::int AS unseen""", tid)
    v.update(cams=s["cams"], cams_ok=s["ok"], recs=s["recs"], recs_ok=s["online"], alerts=s["alerts"], unseen=s["unseen"])
    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM platform.faults WHERE ended_at IS NULL)"):       # our own analysis is down: cameras are not the customer's problem
        v["cams_ok"] = v["cams"]
    hs = [h if isinstance(h, dict) else json.loads(h) for h in (r["health"] for r in await conn.fetch(
        "SELECT health FROM app.recorders WHERE tenant_id=$1 AND health IS NOT NULL AND health_at > now() - interval '24 hours'", tid))]
    kept = [h["days_held"] for h in hs if h.get("days_held") is not None]
    if kept:
        v["days"] = min(kept)
    disks = [d for h in hs for d in (h.get("disks") or [])]
    if disks:
        bad = any(d.get("state") == 2 for d in disks)
        worn = sum(1 for d in disks if d.get("life_percent") is not None and d["life_percent"] <= 20)
        v["disks"] = "faulty" if bad else (f"{worn} wearing out" if worn else "healthy")
    outs = [h["clock_out_minutes"] for h in hs if h.get("clock_out_minutes") is not None]
    if outs:
        v["clocks"] = "wrong" if max(outs) >= 5 else "right"
    v["hours"] = hours_text(t)
    return {"v": v, "caps": caps, "on": on, "start": start, "end": end}


async def problems(conn, t, on: set, kind: str) -> tuple[list[str], int]:
    """The problems to list under "Needs a look" (worst and newest first, one for each camera or recorder) and how many there were in all."""
    from . import alert_line
    zone = ZoneInfo(t["timezone"])
    since = timedelta(hours=24) if kind == "daily" else timedelta(days=7)
    rows = await conn.fetch(
        """SELECT a.id, a.rule_type, a.title, a.body, a.severity, a.details, a.created_at, c.name AS camera FROM app.alerts a LEFT JOIN app.cameras c ON c.id = a.camera_id
           WHERE a.tenant_id=$1 AND a.rule_type = ANY($2) AND a.severity IN ('warning','critical') AND a.created_at > now() - $3::interval ORDER BY a.created_at DESC""",
        t["id"], [k for k in NEEDS_LOOK if k in on], since)
    fixed = {r["name"] for r in await conn.fetch(
        "SELECT details->>'what_recovered' AS name FROM app.alerts WHERE tenant_id=$1 AND rule_type='back_to_normal' AND created_at > now() - $2::interval", t["id"], since)}
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: (r["severity"] != "critical", -r["created_at"].timestamp())):
        d = r["details"] if isinstance(r["details"], dict) else json.loads(r["details"] or "{}")
        who = r["camera"] or d.get("recorder") or d.get("location") or ""
        if kind == "daily" and r["rule_type"] in ("camera_offline", "recorder_offline") and who in fixed:
            continue
        key = (r["rule_type"], who, d.get("disk", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(alert_line.compose(r["rule_type"], alert_catalog.CATALOG[r["rule_type"]]["title"], who if not d.get("recorder") else "", d, clock12(r["created_at"].astimezone(zone).time()),
                                     r["title"], r["body"] or "")[1])
    return out, len(rows)


# ------------------------------------------------------------------------------------------------ writing
def _cams_part(v: dict) -> str:
    return (f"All {v['cams']} cameras working." if v["cams"] and v["cams_ok"] == v["cams"] else
            (f"{v['cams'] - v['cams_ok']} camera{'s' if v['cams'] - v['cams_ok'] != 1 else ''} need{'' if v['cams'] - v['cams_ok'] != 1 else 's'} a look." if v["cams"] else ""))


def headlines(v: dict, caps: dict, kind: str) -> tuple[str, str]:
    """The short version for the phone, in English and in Tamil."""
    ch_key, n_key = ("change", "visitors") if kind == "daily" else ("change_w", "visitors_w")
    if caps["counting"]:
        bit = f" ({v[ch_key]})" if v.get(ch_key) else ""
        when_en, when_ta = ("yesterday", "") if kind == "daily" else ("this week", "")
        en = f"{v[n_key]} visitors {when_en}{bit}. {_cams_part(v)}".strip()
        ta = f"{v[n_key]} வருகையாளர்கள்{bit}. " + (f"{v['cams']} கேமராவும் வேல செய்யுது." if v["cams"] and v["cams_ok"] == v["cams"] else (f"{v['cams'] - v['cams_ok']} கேமராவை பாக்கணும்." if v["cams"] else ""))
        return en, ta.strip()
    en = f"{v['cams_ok']} of {v['cams']} cameras working. {v['recs_ok']} of {v['recs']} recorders online."
    return en, f"{v['cams_ok']}/{v['cams']} கேமரா வேல செய்யுது, {v['recs_ok']}/{v['recs']} ரெக்கார்டர் ஆன்லைன்ல இருக்கு."


def date_text(cfg: dict, kind: str, today: date) -> str:
    if kind == "daily":
        return today.strftime("%d-%m-%Y") if cfg["date_style"] == "num" else f"{today.strftime('%a')} {today.day} {today.strftime('%b')}"
    a, b = today - timedelta(days=7), today - timedelta(days=1)
    return f"{a.strftime('%d-%m')} to {b.strftime('%d-%m-%Y')}" if cfg["date_style"] == "num" else f"{a.day} to {b.day} {b.strftime('%b')}"


async def compose(conn, t, kind: str, cfg: dict, today: date) -> dict:
    """The summary of one customer: {"text", "headline", "headline_ta", "dropped"}. Lines that do not fit the length limit are dropped from the end, business first."""
    g = await gather(conn, t, kind, today)
    v, caps = dict(g["v"]), g["caps"]
    v["limit"] = f" (limit {v['limit_n']})" if cfg["show_limit"] and caps["limit"] else ""
    bul = cfg["bullet"]
    pick = lambda grp: [bul + x for x in (fill(l["t"], v) if l.get("on") and (not l.get("req") or caps.get(l["req"])) else None for l in cfg["lines"][kind] if l["g"] == grp) if x]
    biz = pick("business")
    probs, n_probs = await problems(conn, t, g["on"], kind)
    v["problems_n"] = n_probs
    sys_ = [bul + x for x in (fill(l["t"], v) if l.get("on") else None for l in cfg["lines"][kind] if l["g"] == "system") if x]
    shown = [bul + p for p in probs[:cfg["max_problems"]]]
    if len(probs) > cfg["max_problems"]:
        shown.append(f"{bul}+{len(probs) - cfg['max_problems']} more")
    title = cfg["title"].replace("{kind}", "Daily" if kind == "daily" else "Weekly").replace("{date}", date_text(cfg, kind, today)).replace("{customer}", t["name"])
    heads = (list(cfg["heads"]) + ["BUSINESS", "SYSTEM", "NEEDS A LOOK"])[:3]

    def assemble(b, s, p):
        out = [title, ""]
        if b:
            out += [heads[0], *b, ""]
        if s:
            out += [heads[1], *s, ""]
        out += [heads[2], *p, ""] if p else [cfg["all_fine"], ""]
        if cfg["closing"].strip():
            out.append(cfg["closing"].strip())
        return "\n".join(out).strip()

    b, s, dropped = biz[:], sys_[:], 0
    text = assemble(b, s, shown)
    while len(text) > int(cfg["maxchars"]) and (len(b) > 1 or len(s) > 1):
        (b if len(b) > 1 else s).pop()
        dropped += 1
        text = assemble(b, s, shown)
    en, ta = headlines(v, caps, kind)
    return {"text": text, "headline": en, "headline_ta": ta, "dropped": dropped, "problems": len(probs)}


# ------------------------------------------------------------------------------------------------ recipients and the clock
async def default_recipients(conn, tid, mode: str) -> list:
    """The customer's own choice wins (the alert's recipients). With none chosen: the owner (the first owner number set on the customer), or all of the owner's numbers,
    as Platform Admin says. The owner is found among the people who get messages by the last ten digits of the number."""
    if mode == "none":
        return []
    nums = await conn.fetch("SELECT phone FROM platform.tenant_owner_phones WHERE tenant_id=$1 ORDER BY position", tid)
    tails = [re.sub(r"\D", "", n["phone"])[-10:] for n in nums if len(re.sub(r"\D", "", n["phone"])) >= 10]
    if mode == "owner":
        tails = tails[:1]
    if not tails:
        return []
    return [r["id"] for r in await conn.fetch(
        "SELECT id FROM app.recipients WHERE tenant_id=$1 AND active AND phone IS NOT NULL AND right(regexp_replace(phone, '\\D', '', 'g'), 10) = ANY($2)", tid, tails)]


def _hour(s: str) -> int:
    return int(str(s).split(":")[0])


async def send_one(conn, t, kind: str, cfg: dict, has_key: bool, today: date, *, force: bool = False) -> bool:
    """Writes one customer's summary as an alert (the app shows it, the phone is told) and queues it for WhatsApp. Returns False when nothing was raised."""
    rtype = f"{kind}_summary"
    built = await compose(conn, t, kind, cfg, today)
    rule = await conn.fetchrow("SELECT recipients FROM app.alert_rules WHERE tenant_id=$1 AND rule_type=$2", t["id"], rtype)
    who = list(rule["recipients"]) if rule and rule["recipients"] else await default_recipients(conn, t["id"], cfg["recips"])
    return bool(await raise_alert(conn, t["id"], rtype, title=f"{'Daily' if kind == 'daily' else 'Weekly'} system summary", body=built["text"], camera_name="All cameras",
                                  details={"summary": built["text"], "headline": built["headline"], "headline_ta": built["headline_ta"], "kind": kind},
                                  dedupe_key=f"summary:{kind}:{today.isoformat()}", cooldown_minutes=0 if force else 20 * 60, recipient_ids=who,
                                  direct_ready=summary_config.ready(cfg, has_key, kind)))


async def run() -> int:
    """Called every minute by the maintenance loop. Sends each customer their summary at their hour: the weekly one on the weekly day (instead of the daily one, if that is
    how Platform Admin has it), the daily one on every other day. Returns how many were written."""
    n = 0
    async with db.service_tx() as conn:
        cfg = await summary_config.load(conn)
        if not (cfg["daily_on"] or cfg["weekly_on"]):
            return 0
        has_key = await summary_config.key(conn) is not None
        statuses = ("trial", "active", "past_due") if cfg["skip_inactive"] else ("trial", "active", "past_due", "suspended")
        tenants = await conn.fetch("SELECT id, name, timezone, open_time, close_time, closed_days, day_hours FROM app.tenants WHERE status::text = ANY($1)", list(statuses))
        for t in tenants:
            local = datetime.now(ZoneInfo(t["timezone"] if cfg["tz"] == "customer" else cfg["tz"]))
            rules = {r["rule_type"]: r for r in await conn.fetch("SELECT rule_type, enabled, params FROM app.alert_rules WHERE tenant_id=$1 AND rule_type IN ('daily_summary','weekly_summary')", t["id"])}

            def on(kind):
                r = rules.get(f"{kind}_summary")
                return (r["enabled"] if r else (cfg["default_on"] and f"{kind}_summary" in DEFAULT_ENABLED)) and cfg[f"{kind}_on"]

            def hour(kind):
                r = rules.get(f"{kind}_summary")
                p = (r["params"] if r and isinstance(r["params"], dict) else (json.loads(r["params"]) if r and r["params"] else {})) if r else {}
                return int(p["hour"]) if cfg["cust_edit"] and "hour" in p else _hour(cfg[f"{kind}_hour"])

            weekly_today = on("weekly") and local.weekday() == int(cfg["weekly_day"])
            for kind in ("weekly", "daily"):
                if not on(kind) or local.hour != hour(kind):
                    continue
                if kind == "weekly" and not weekly_today:
                    continue
                if kind == "daily" and weekly_today and cfg["replace_daily"]:
                    continue
                key = f"summary:{kind}:{local.date().isoformat()}"
                if await conn.fetchval("SELECT 1 FROM app.alerts WHERE tenant_id=$1 AND dedupe_key=$2", t["id"], key):
                    continue
                if await send_one(conn, t, kind, cfg, has_key, local.date()):
                    n += 1
    return n
