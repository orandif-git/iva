"""Marked areas: what a customer can set on one, and when what the worker saw becomes an alert.

An area is a shape drawn on a camera picture, with a name and its own limits. There are three kinds:
  area_count   people in the area        alert "Too many people in an area"      when the count stays at the limit or above for `minutes`
  queue_area   a queue                   alert "Queue too long"                  when the queue stays at `length` or more for `minutes`, or someone has waited `wait_minutes`
  staff_area   where staff work          alert "Possible phone use in a staff area"  when a phone has been in use for `seconds` without a break
The limits belong to the area, so two counters can have two limits. Whether the alert is on, and who is told, is the customer's alert setting.
An event is raised only when the condition holds, and the same area is not reported again for `repeat_minutes`."""
from datetime import datetime, timezone
from uuid import UUID

from ..errors import ApiError
from . import alert_catalog as cat
from .alerts import raise_alert

# `staff` (a queue only): how many of your own people normally stand inside the shape, for example the cashier. They are taken off the count, so a queue of
# "6" means six customers.
# name: (default, lowest, highest). A setting with default None must be given. `picture_minutes`: how often the worker takes another picture while a queue or a crowd stays
# over its limit (the first is taken when it gets there); 0 means no pictures.
SPEC = {
    "area_count": {"limit": (None, 1, 1000), "minutes": (2, 1, 60), "picture_minutes": (15, 0, 240), "repeat_minutes": (15, 5, 1440)},
    "queue_area": {"length": (5, 1, 1000), "minutes": (3, 1, 60), "wait_minutes": (0, 0, 240), "staff": (0, 0, 20), "picture_minutes": (15, 0, 240), "repeat_minutes": (15, 5, 1440)},
    "staff_area": {"seconds": (20, 5, 600), "repeat_minutes": (15, 5, 1440)},
}
# Activity in an area: what to look for, and when. `target`: "person" or "vehicle" (the camera's detector tells them apart; more kinds of thing can be added here later).
# `when`: "closed" = while the shop is closed (needs opening hours), "between" = every day between `from` and `to` (a night that crosses midnight is fine), "always".
SPEC["activity_area"] = {"minutes": (1, 1, 60), "picture_minutes": (10, 0, 240), "repeat_minutes": (30, 5, 1440)}
CHOICES = {"activity_area": {"target": ("person", ("person", "vehicle")), "when": ("closed", ("closed", "between", "always"))}}
TIMES = {"activity_area": ("from", "to")}                         # needed only when `when` is "between"
LABEL = {"area_count": "people in an area", "queue_area": "queue", "staff_area": "staff area", "activity_area": "activity in an area"}
WHAT = {"person": ("A person", "people"), "vehicle": ("A vehicle", "vehicles")}


def _hhmm(v, what: str) -> str:
    import re
    if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ApiError(422, "invalid_params", f"{what} must be a time like 21:30.")
    return v


def clean_params(kind: str, params: dict, *, previous: dict | None = None, user_id=None) -> dict:
    """The settings of one area, checked and with the defaults filled in. A staff area also needs the customer's confirmation that staff have been told."""
    spec = SPEC[kind]
    choices, times = CHOICES.get(kind, {}), TIMES.get(kind, ())
    extra = set(params) - set(spec) - set(choices) - set(times) - {"notice_confirmed"}
    if extra:
        raise ApiError(422, "invalid_params", f"Unknown setting: {', '.join(sorted(extra))}.")
    out = {}
    for k, (default, lo, hi) in spec.items():
        v = params.get(k, default)
        if v is None:
            raise ApiError(422, "invalid_params", f"Set {k.replace('_', ' ')}.")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            raise ApiError(422, "invalid_params", f"{k.replace('_', ' ')} must be a whole number.")
        if not lo <= v <= hi:
            raise ApiError(422, "invalid_params", f"{k.replace('_', ' ')} must be between {lo} and {hi}.")
        out[k] = int(v)
    for k, (default, options) in choices.items():
        v = params.get(k, default)
        if v not in options:
            raise ApiError(422, "invalid_params", f"{k.replace('_', ' ')} must be one of: {', '.join(options)}.")
        out[k] = v
    if kind == "activity_area" and out["when"] == "between":
        out["from"], out["to"] = _hhmm(params.get("from"), "The start time"), _hhmm(params.get("to"), "The end time")
        if out["from"] == out["to"]:
            raise ApiError(422, "invalid_params", "The start and the end time cannot be the same.")
    if kind == "staff_area":
        had = (previous or {}).get("notice_confirmed_at")
        if params.get("notice_confirmed") is not True:
            raise ApiError(422, "notice_required", "Please confirm that your staff have been told that this area is monitored.")
        if had:
            out["notice_confirmed_at"], out["notice_confirmed_by"] = had, (previous or {}).get("notice_confirmed_by")
        else:
            out["notice_confirmed_at"], out["notice_confirmed_by"] = datetime.now(timezone.utc).isoformat(), str(user_id) if user_id else None
        out["notice_confirmed"] = True
    return out


def alert_for_kind(kind: str) -> str:
    return cat.AREA_ALERTS[kind]


# ------------------------------------------------------------------------------------------------ the conditions
HELD_SHARE = 0.8        # this share of the readings must be at the limit: in a crowd people hide each other, so one low reading in five is not the crowd leaving


async def _held(conn, zone_id: UUID, minutes: int, minimum: int) -> bool:
    """Was the area at `minimum` people or more for the last `minutes`? Reports must cover the whole time (a gap or a late start does not count), the newest must
    be at the limit, and at least four in five of them must be. One reading that drops because someone was hidden behind someone else does not start the clock again."""
    r = await conn.fetchrow(
        """SELECT count(*)::int AS n, count(*) FILTER (WHERE people >= $3)::int AS at_limit, extract(epoch FROM (max(at) - min(at)))::float AS span,
                  (array_agg(people ORDER BY at DESC))[1] AS newest
           FROM app.zone_samples WHERE zone_id=$1 AND at > now() - make_interval(mins => $2)""", zone_id, minutes, minimum)
    return bool(r["n"] >= 2 and r["newest"] >= minimum and r["at_limit"] >= r["n"] * HELD_SHARE and r["span"] >= minutes * 60 - 20)


async def _picture(conn, zone_id: UUID, minutes: int, picture_minutes: int = 15):
    """The newest picture the worker took of this area while it was over its limit (it takes one when it gets there and another every `picture_minutes`)."""
    return await conn.fetchval(
        """SELECT evidence_id FROM app.zone_samples WHERE zone_id=$1 AND evidence_id IS NOT NULL AND at > now() - make_interval(mins => $2) ORDER BY at DESC LIMIT 1""",
        zone_id, max(minutes, picture_minutes) + 2)


async def evaluate(conn, zone_id: UUID) -> int:
    """Raises the alert for one area if its condition holds right now. Returns how many alerts were raised (0 or 1)."""
    return int(await evaluate_alert(conn, zone_id) is not None)


async def evaluate_alert(conn, zone_id: UUID):
    """The same, but returns the new alert's id (None when nothing was raised), so the caller can ask for a video of it."""
    z = await conn.fetchrow(
        """SELECT z.id, z.tenant_id, z.camera_id, z.kind::text AS kind, z.name, z.params, c.name AS camera FROM app.camera_zones z JOIN app.cameras c ON c.id = z.camera_id
           WHERE z.id=$1 AND z.kind::text = ANY($2::text[])""", zone_id, list(cat.AREA_ALERTS))
    if z is None:
        return None
    p, area, alert = z["params"] or {}, z["name"] or "an area", cat.AREA_ALERTS[z["kind"]]
    repeat = int(p.get("repeat_minutes", 15))
    common = dict(camera_id=z["camera_id"], camera_name=z["camera"], dedupe_key=f"area:{z['id']}", cooldown_minutes=repeat)
    if z["kind"] == "area_count":
        limit, minutes = int(p.get("limit", 0)), int(p.get("minutes", 2))
        if limit < 1 or not await _held(conn, z["id"], minutes, limit):
            return None
        now = await conn.fetchval("SELECT people FROM app.zone_samples WHERE zone_id=$1 ORDER BY at DESC LIMIT 1", z["id"])
        return await raise_alert(conn, z["tenant_id"], alert, title=f"Occupancy limit exceeded in {area}", body=f"{now} people are in {area}. Your limit is {limit}, and it has been at or above that for {minutes} minutes.",
                                     details={"area": area, "people": now, "limit": limit, "minutes": minutes}, evidence_id=await _picture(conn, z["id"], minutes, int(p.get("picture_minutes", 15))), expect_clip=True, **common)
    if z["kind"] == "queue_area":
        length, minutes, wait, staff = int(p.get("length", 5)), int(p.get("minutes", 3)), int(p.get("wait_minutes", 0)), int(p.get("staff", 0))
        seen = await conn.fetchrow("SELECT people, wait_seconds FROM app.zone_samples WHERE zone_id=$1 ORDER BY at DESC LIMIT 1", z["id"])
        now = {"people": max(0, (seen["people"] or 0) - staff), "wait_seconds": seen["wait_seconds"]}        # your own people inside the shape are not part of the queue
        long_queue = await _held(conn, z["id"], minutes, length + staff)
        # someone waiting: only when there is a queue at all beyond your own people (the cashier is always "waiting")
        waited = wait > 0 and now["people"] > 0 and (await conn.fetchval("SELECT max(wait_seconds) FROM app.zone_samples WHERE zone_id=$1 AND at > now() - interval '60 seconds' AND people > $2", z["id"], staff) or 0) >= wait * 60
        if not (long_queue or waited):
            return None
        why = f"{now['people']} people are waiting in {area}" + (f", {length} or more for {minutes} minutes" if long_queue else "") + (f". Someone has waited {wait} minutes or more" if waited else "") + "."
        return await raise_alert(conn, z["tenant_id"], alert, title=f"Long queue at {area}", body=why,
                                     details={"area": area, "length": now["people"], "limit": length, "minutes": minutes, "wait_minutes": wait}, evidence_id=await _picture(conn, z["id"], minutes, int(p.get("picture_minutes", 15))), expect_clip=True, **common)
    if z["kind"] == "activity_area":
        return await _activity(conn, z, p, area, alert, common)
    seconds = int(p.get("seconds", 20))
    r = await conn.fetchrow("SELECT max(phone_seconds) AS held, (array_agg(evidence_id ORDER BY at DESC) FILTER (WHERE evidence_id IS NOT NULL))[1] AS evidence "
                            "FROM app.zone_samples WHERE zone_id=$1 AND at > now() - interval '30 seconds'", z["id"])
    if not r["held"] or r["held"] < seconds:
        return None
    return await raise_alert(conn, z["tenant_id"], alert, title=f"Possible phone use in {area}", body=f"A phone seems to have been in use in {area} for {int(r['held'])} seconds. This is a possible case, please check the picture.",
                                 details={"area": area, "seconds": int(r["held"])}, evidence_id=r["evidence"], expect_clip=True, **common)


def in_window(p: dict, hours, local: datetime) -> bool:
    """Is `local` (the shop's own clock) inside the time the area is watched? `hours` is the shop's opening hours."""
    when = p.get("when", "closed")
    if when == "always":
        return True
    if when == "between":
        a, b = p["from"], p["to"]
        t = local.strftime("%H:%M")
        return a <= t < b if a < b else (t >= a or t < b)
    return hours.set and not hours.is_open(local)            # "closed": with no opening hours set there is no "closed", so nothing is raised


async def _activity(conn, z, p: dict, area: str, alert: str, common: dict) -> "UUID | None":
    """Someone (or a vehicle) in the area for `minutes`, at a time it is being watched."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from .opening_hours import Hours
    t = await conn.fetchrow("SELECT timezone, open_time, close_time, closed_days, day_hours FROM app.tenants WHERE id=$1", z["tenant_id"])
    local = _dt.now(ZoneInfo(t["timezone"])).replace(tzinfo=None)
    if not in_window(p, Hours.of(t), local):
        return None
    minutes = int(p.get("minutes", 1))
    if not await _held(conn, z["id"], minutes, 1):
        return None
    n = int(await conn.fetchval("SELECT people FROM app.zone_samples WHERE zone_id=$1 ORDER BY at DESC LIMIT 1", z["id"]) or 1)
    one, many = WHAT.get(p.get("target", "person"), WHAT["person"])
    when = {"closed": "while the shop is closed", "between": f"between {p.get('from')} and {p.get('to')}", "always": ""}[p.get("when", "closed")]
    seen = one if n == 1 else f"{n} {many}"
    return await raise_alert(conn, z["tenant_id"], alert, title=f"Activity in {area}", body=f"{seen} seen in {area}{' ' + when if when else ''}.",
                                 details={"area": area, "what": one.split()[-1], "when": when or "at any time", "count": n},
                                 evidence_id=await _picture(conn, z["id"], minutes, int(p.get("picture_minutes", 10))), expect_clip=True, **common)
