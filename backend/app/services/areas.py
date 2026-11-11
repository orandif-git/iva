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

# name: (default, lowest, highest). A setting with default None must be given. `picture_minutes`: how often the worker takes another picture while a queue or a crowd stays
# over its limit (the first is taken when it gets there); 0 means no pictures.
SPEC = {
    "area_count": {"limit": (None, 1, 1000), "minutes": (2, 1, 60), "picture_minutes": (15, 0, 240), "repeat_minutes": (15, 5, 1440)},
    "queue_area": {"length": (5, 1, 1000), "minutes": (3, 1, 60), "wait_minutes": (0, 0, 240), "picture_minutes": (15, 0, 240), "repeat_minutes": (15, 5, 1440)},
    "staff_area": {"seconds": (20, 5, 600), "repeat_minutes": (15, 5, 1440)},
}
LABEL = {"area_count": "people in an area", "queue_area": "queue", "staff_area": "staff area"}


def clean_params(kind: str, params: dict, *, previous: dict | None = None, user_id=None) -> dict:
    """The settings of one area, checked and with the defaults filled in. A staff area also needs the customer's confirmation that staff have been told."""
    spec = SPEC[kind]
    extra = set(params) - set(spec) - {"notice_confirmed"}
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
async def _held(conn, zone_id: UUID, minutes: int, minimum: int) -> bool:
    """Did every report in the last `minutes` show at least `minimum` people, with reports covering the whole time (a gap or a late start does not count)?"""
    r = await conn.fetchrow(
        """SELECT count(*)::int AS n, min(people) AS lowest, extract(epoch FROM (max(at) - min(at)))::float AS span FROM app.zone_samples
           WHERE zone_id=$1 AND at > now() - make_interval(mins => $2)""", zone_id, minutes)
    return bool(r["n"] >= 2 and r["lowest"] >= minimum and r["span"] >= minutes * 60 - 20)


async def _picture(conn, zone_id: UUID, minutes: int, picture_minutes: int = 15):
    """The newest picture the worker took of this area while it was over its limit (it takes one when it gets there and another every `picture_minutes`)."""
    return await conn.fetchval(
        """SELECT evidence_id FROM app.zone_samples WHERE zone_id=$1 AND evidence_id IS NOT NULL AND at > now() - make_interval(mins => $2) ORDER BY at DESC LIMIT 1""",
        zone_id, max(minutes, picture_minutes) + 2)


async def evaluate(conn, zone_id: UUID) -> int:
    """Raises the alert for one area if its condition holds right now. Returns how many alerts were raised (0 or 1)."""
    z = await conn.fetchrow(
        """SELECT z.id, z.tenant_id, z.camera_id, z.kind::text AS kind, z.name, z.params, c.name AS camera FROM app.camera_zones z JOIN app.cameras c ON c.id = z.camera_id
           WHERE z.id=$1 AND z.kind::text = ANY($2::text[])""", zone_id, list(cat.AREA_ALERTS))
    if z is None:
        return 0
    p, area, alert = z["params"] or {}, z["name"] or "an area", cat.AREA_ALERTS[z["kind"]]
    repeat = int(p.get("repeat_minutes", 15))
    common = dict(camera_id=z["camera_id"], camera_name=z["camera"], dedupe_key=f"area:{z['id']}", cooldown_minutes=repeat)
    if z["kind"] == "area_count":
        limit, minutes = int(p.get("limit", 0)), int(p.get("minutes", 2))
        if limit < 1 or not await _held(conn, z["id"], minutes, limit):
            return 0
        now = await conn.fetchval("SELECT people FROM app.zone_samples WHERE zone_id=$1 ORDER BY at DESC LIMIT 1", z["id"])
        return int(await raise_alert(conn, z["tenant_id"], alert, title=f"Too many people in {area}", body=f"{now} people are in {area}. Your limit is {limit}, and it has been at or above that for {minutes} minutes.",
                                     details={"area": area, "people": now, "limit": limit, "minutes": minutes}, evidence_id=await _picture(conn, z["id"], minutes, int(p.get("picture_minutes", 15))), **common) is not None)
    if z["kind"] == "queue_area":
        length, minutes, wait = int(p.get("length", 5)), int(p.get("minutes", 3)), int(p.get("wait_minutes", 0))
        now = await conn.fetchrow("SELECT people, wait_seconds FROM app.zone_samples WHERE zone_id=$1 ORDER BY at DESC LIMIT 1", z["id"])
        long_queue = await _held(conn, z["id"], minutes, length)
        waited = wait > 0 and (await conn.fetchval("SELECT max(wait_seconds) FROM app.zone_samples WHERE zone_id=$1 AND at > now() - interval '60 seconds'", z["id"]) or 0) >= wait * 60
        if not (long_queue or waited):
            return 0
        why = f"{now['people']} people are waiting in {area}" + (f", {length} or more for {minutes} minutes" if long_queue else "") + (f". Someone has waited {wait} minutes or more" if waited else "") + "."
        return int(await raise_alert(conn, z["tenant_id"], alert, title=f"Queue too long at {area}", body=why,
                                     details={"area": area, "length": now["people"], "limit": length, "minutes": minutes, "wait_minutes": wait}, evidence_id=await _picture(conn, z["id"], minutes, int(p.get("picture_minutes", 15))), **common) is not None)
    seconds = int(p.get("seconds", 20))
    r = await conn.fetchrow("SELECT max(phone_seconds) AS held, (array_agg(evidence_id ORDER BY at DESC) FILTER (WHERE evidence_id IS NOT NULL))[1] AS evidence "
                            "FROM app.zone_samples WHERE zone_id=$1 AND at > now() - interval '30 seconds'", z["id"])
    if not r["held"] or r["held"] < seconds:
        return 0
    return int(await raise_alert(conn, z["tenant_id"], alert, title=f"Possible phone use in {area}", body=f"A phone seems to have been in use in {area} for {int(r['held'])} seconds. This is a possible case, please check the picture.",
                                 details={"area": area, "seconds": int(r["held"])}, evidence_id=r["evidence"], **common) is not None)
