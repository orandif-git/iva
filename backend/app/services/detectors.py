"""Alerts that come from looking at the data over time rather than from a single event: vehicles that stay too long, and crowding."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..db import db
from ..messaging import evidence
from . import snapshot, rules
from .opening_hours import Hours
from .alerts import raise_alert

log = logging.getLogger("gateinsight.detectors")


def stay_text(minutes: int) -> str:
    """"45 minutes", "3 hours", "1.5 hours": how long, in the words a person would use."""
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes / 60:g} hours"


def overstay_minutes(params: dict | None) -> int:
    """The limit in minutes. Older settings were saved in hours (`max_hours`); they still work."""
    p = params or {}
    if p.get("max_minutes") is not None:
        return max(1, int(float(p["max_minutes"])))
    return max(1, round(float(p.get("max_hours", 8)) * 60))


async def vehicle_overstay() -> int:
    """A vehicle that entered more than the set time ago and has not been seen since. One alert per visit.

    Not reported: a vehicle on the customer's allowed list (staff and the owner are expected to stay), and a vehicle that was read again later, in or out, since it
    plainly did not stay where it was (an exit the camera read badly, or a missed exit followed by the next visit)."""
    n = 0
    async with db.service_tx() as conn:
        for rule in await rules.for_type(conn, "vehicle_overstay"):
            minutes = overstay_minutes(rule["params"])
            rows = await conn.fetch(
                """SELECT i.id, i.plate_text, i.seen_at, i.camera_id, i.evidence_id, c.name AS camera FROM app.plate_events i JOIN app.cameras c ON c.id=i.camera_id
                   WHERE i.tenant_id=$1 AND i.direction='in' AND i.plate_text IS NOT NULL AND i.review_status <> 'needs_review'
                     AND i.seen_at < now() - make_interval(mins => $2) AND i.seen_at > now() - interval '7 days'
                     AND NOT EXISTS (SELECT 1 FROM app.plate_events o WHERE o.entry_event_id = i.id)
                     AND NOT EXISTS (SELECT 1 FROM app.plate_events l WHERE l.tenant_id = i.tenant_id AND l.plate_text = i.plate_text AND l.seen_at > i.seen_at
                                     AND l.seen_at > now() - interval '7 days' AND (l.direction = 'in' OR l.direction = 'out'))
                     AND NOT EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.tenant_id = i.tenant_id AND v.plate_text = i.plate_text AND v.list_type = 'allowed')
                     AND ($3::uuid IS NULL OR i.camera_id = $3)
                   ORDER BY i.seen_at LIMIT 50""", rule["tenant_id"], minutes, rule["camera_id"])
            for r in rows:
                if await raise_alert(conn, rule["tenant_id"], "vehicle_overstay", title=f"Vehicle overstay: {r['plate_text']}",
                                     body=f"Entered at {r['camera']} and has not left after {stay_text(minutes)}.", camera_id=r["camera_id"], camera_name=r["camera"],
                                     details={"plate": r["plate_text"], "minutes": str(minutes), "hours": f"{minutes / 60:g}", "stay": stay_text(minutes)},
                                     dedupe_key=f"overstay:{r['id']}", cooldown_minutes=60 * 24 * 7, evidence_id=r["evidence_id"], rule_id=rule["id"]):
                    n += 1
    return n


_crowd_pictures: dict = {}          # tenant -> (when it was taken, evidence id): kept in memory, so a restart simply takes a fresh one


def people_inside(buckets) -> int:
    """How many people are inside, from the day's counts in time order. The running number never goes below zero: when a few exits are counted before the first
    entries (staff leaving by the front door, a person missed on the way in), the entries that follow are not swallowed by that shortfall."""
    inside = 0
    for b in buckets:
        inside = max(0, inside + int(b["people_in"]) - int(b["people_out"]))
    return inside


async def _crowd_picture(conn, tenant_id, place_key: str, camera_ids: list, tz: str, minutes: int):
    """A picture for the occupancy alert: the camera of this place that has counted the most people today, taken now. While the place stays over its limit a new one is
    taken only every `minutes` (the customer sets it; 0 means no pictures), and the alert shows the newest. If the camera cannot be reached the alert goes out without one."""
    if minutes <= 0:
        return None
    now = time.monotonic()
    kept = _crowd_pictures.get((tenant_id, place_key))
    if kept and now - kept[0] < minutes * 60 and await conn.fetchval("SELECT expires_at > now() FROM app.evidence WHERE id=$1", kept[1]):
        return kept[1]
    cam = await conn.fetchrow(
        """SELECT c.id, c.source_ciphertext, c.channel_no, r.device_id, r.protocol::text AS protocol, t.retention_snapshots_days
           FROM app.footfall_counts f JOIN app.cameras c ON c.id = f.camera_id LEFT JOIN app.recorders r ON r.id = c.recorder_id JOIN app.tenants t ON t.id = c.tenant_id
           WHERE f.tenant_id=$1 AND c.enabled AND c.id = ANY($3::uuid[]) AND f.bucket_start >= date_trunc('day', now() AT TIME ZONE $2) AT TIME ZONE $2
           GROUP BY c.id, c.source_ciphertext, c.channel_no, r.device_id, r.protocol, t.retention_snapshots_days ORDER BY sum(f.people_in) DESC LIMIT 1""", tenant_id, tz, camera_ids)
    if not cam:
        return None
    try:
        def decrypt():
            from ..crypto import vault
            return vault().decrypt(bytes(cam["source_ciphertext"]), str(cam["id"]))
        url, fmt = await asyncio.wait_for(snapshot.source_for(cam, decrypt), 20)
        jpeg = await asyncio.wait_for(snapshot.grab(url, fmt), snapshot.GRAB_TIMEOUT + 5)
        eid = await evidence.save_image(conn, tenant_id, cam["id"], jpeg, cam["retention_snapshots_days"])
    except Exception as e:                                  # a picture is a bonus: never hold an alert back for it
        log.info("no picture for the occupancy alert: %s", type(e).__name__)
        return None
    _crowd_pictures[(tenant_id, place_key)] = (now, eid)
    return eid


async def crowding() -> int:
    """People counted in minus out today (the tenant's day), at or above the limit. At most one alert an hour for each place.

    A customer can have several places (a shop and a godown, or three shops on three recorders). Each place is counted on its own: the cameras of one site, or of one
    recorder when no site is set. Adding every place together said nothing true about any of them.
    It is still an estimate: a person missed at the door is never corrected, so the count drifts through the day. It is a warning, not a head count."""
    n = 0
    async with db.service_tx() as conn:
        for rule in await rules.for_type(conn, "crowding"):
            rule = {**rule, "timezone": await conn.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", rule["tenant_id"])}
            params = rule["params"] or {}
            limit = int(params.get("people", 50))
            rows = await conn.fetch(
                """SELECT coalesce(c.site_id::text, c.recorder_id::text, c.id::text) AS place, coalesce(s.name, r.name, r.device_id, c.name) AS place_name, c.id AS camera_id,
                          f.bucket_start, f.people_in, f.people_out
                   FROM app.footfall_counts f JOIN app.cameras c ON c.id = f.camera_id LEFT JOIN app.sites s ON s.id = c.site_id LEFT JOIN app.recorders r ON r.id = c.recorder_id
                   WHERE f.tenant_id=$1 AND f.bucket_start >= date_trunc('day', now() AT TIME ZONE $2) AT TIME ZONE $2
                     AND ($3::uuid IS NULL OR c.id = $3) ORDER BY f.bucket_start""", rule["tenant_id"], rule["timezone"], rule["camera_id"])
            places: dict = {}
            for r in rows:
                places.setdefault(r["place"], {"name": r["place_name"], "cams": set(), "buckets": {}})
                pl = places[r["place"]]
                pl["cams"].add(r["camera_id"])
                agg = pl["buckets"].setdefault(r["bucket_start"], {"people_in": 0, "people_out": 0})          # the doors of one place are added together, hour by hour
                agg["people_in"] += r["people_in"]; agg["people_out"] += r["people_out"]
            several = len(places) > 1
            for key, pl in places.items():
                inside = people_inside([pl["buckets"][k] for k in sorted(pl["buckets"])])
                if inside < limit:
                    _crowd_pictures.pop((rule["tenant_id"], key), None)           # back under the limit: the next time starts again
                    continue
                pic = await _crowd_picture(conn, rule["tenant_id"], key, list(pl["cams"]), rule["timezone"], int(params.get("picture_minutes", 15)))
                where = pl["name"] or "your premises"
                if await raise_alert(conn, rule["tenant_id"], "crowding", title=f"Occupancy limit exceeded at {where}" if several else None,
                                     body=f"About {inside} people are inside{' ' + where if several else ''}. Your limit is {limit}.",
                                     camera_name=where if several else "All cameras", details={"people": int(inside), "limit": limit},
                                     dedupe_key=f"crowding:{key}" if several else "crowding", cooldown_minutes=60, evidence_id=pic, rule_id=rule["id"]):
                    n += 1
    return n


# ------------------------------------------------------------------------------------------------ alerts that need the opening hours
async def _camera_picture(conn, tenant_id, camera_id):
    """A picture from one camera, taken now, kept as evidence. None when the camera cannot be reached: an alert is never held back for its picture."""
    cam = await conn.fetchrow(
        """SELECT c.id, c.source_ciphertext, c.channel_no, r.device_id, r.protocol::text AS protocol, t.retention_snapshots_days
           FROM app.cameras c LEFT JOIN app.recorders r ON r.id = c.recorder_id JOIN app.tenants t ON t.id = c.tenant_id WHERE c.id=$1 AND c.tenant_id=$2""", camera_id, tenant_id)
    if not cam:
        return None
    try:
        def decrypt():
            from ..crypto import vault
            return vault().decrypt(bytes(cam["source_ciphertext"]), str(cam["id"]))
        url, fmt = await asyncio.wait_for(snapshot.source_for(cam, decrypt), 20)
        jpeg = await asyncio.wait_for(snapshot.grab(url, fmt), snapshot.GRAB_TIMEOUT + 5)
        return await evidence.save_image(conn, tenant_id, cam["id"], jpeg, cam["retention_snapshots_days"])
    except Exception as e:
        log.info("no picture for the alert: %s", type(e).__name__)
        return None


def _local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)


async def counting_stopped() -> int:
    """A camera that counts people, is online and sending video, yet has counted nobody for `hours` (2 unless changed) while the place has been open all that time.
    Usually the counting line no longer sits on the doorway (the camera was moved) or the door is no longer used. Once a day for a camera.
    Needs the customer's opening hours: with none set, an empty shop at night cannot be told from a fault, so nothing is said. Nothing is said either while our own
    analysis is stopped: then nothing is being counted anywhere, and that is ours to fix."""
    n = 0
    async with db.service_tx() as conn:
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM platform.faults WHERE ended_at IS NULL)"):
            return 0
        tenants = await conn.fetch("SELECT id, timezone, open_time, close_time, closed_days FROM app.tenants WHERE open_time IS NOT NULL AND close_time IS NOT NULL AND status::text IN ('trial','active','past_due')")
        for t in tenants:
            hours, local = Hours.of(t), _local_now(t["timezone"])
            session = hours.session(local)
            if not session:
                continue
            need = int(await _param(conn, t["id"], "counting_stopped", "hours", 2))
            if local - session[0] < timedelta(hours=need):                 # not open long enough yet to say anything
                continue
            cams = await conn.fetch(
                """SELECT c.id, c.name FROM app.cameras c WHERE c.tenant_id=$1 AND c.enabled AND c.use_footfall AND c.stream_state='streaming'
                     AND c.stream_state_at < now() - make_interval(hours => $2) AND c.stream_seen_at > now() - interval '3 minutes'
                     AND NOT EXISTS (SELECT 1 FROM app.footfall_counts f WHERE f.camera_id = c.id AND f.bucket_start > now() - make_interval(hours => $2) AND (f.people_in > 0 OR f.people_out > 0))""",
                t["id"], need)
            for c in cams:
                if await raise_alert(conn, t["id"], "counting_stopped", body=f"{c['name']} is online but has counted nobody in the last {need} hours while you are open. Check that the camera still looks at the doorway.",
                                     camera_id=c["id"], camera_name=c["name"], details={"hours": need}, dedupe_key=f"nocount:{c['id']}", cooldown_minutes=20 * 60):
                    n += 1
    return n


async def after_hours() -> int:
    """People counted at a door while the place is closed. The first `grace_minutes` after closing (30 unless changed) and the last 30 minutes before opening are
    ignored: staff locking up and arriving. One alert for a camera in half an hour, with a picture taken at that moment. It is an urgent alert: always sent.
    It sees only what a counting camera sees, a person crossing its counting line. It is not a motion detector for the whole room."""
    n = 0
    async with db.service_tx() as conn:
        for rule in await rules.for_type(conn, "after_hours"):
            t = await conn.fetchrow("SELECT timezone, open_time, close_time, closed_days FROM app.tenants WHERE id=$1 AND open_time IS NOT NULL AND close_time IS NOT NULL "
                                    "AND status::text IN ('trial','active','past_due')", rule["tenant_id"])
            if t is None:
                continue
            rule = {**rule, **dict(t)}
            hours, local = Hours.of(rule), _local_now(rule["timezone"])
            closed_at = hours.closed_since(local)
            if closed_at is None:
                continue
            grace = int((rule["params"] or {}).get("grace_minutes", 30))
            opens = hours.opens_next(local)
            if local < closed_at + timedelta(minutes=grace) or (opens is not None and local > opens - timedelta(minutes=30)):
                continue
            # counts of the last ten minutes that fall after the grace time
            quiet_for = min(10, int((local - (closed_at + timedelta(minutes=grace))).total_seconds() // 60))
            if quiet_for < 1:
                continue
            rows = await conn.fetch(
                """SELECT c.id, c.name, sum(f.people_in + f.people_out)::int AS people FROM app.footfall_counts f JOIN app.cameras c ON c.id = f.camera_id
                   WHERE f.tenant_id=$1 AND f.bucket_start > now() - make_interval(mins => $2) AND ($3::uuid IS NULL OR c.id=$3)
                   GROUP BY c.id, c.name HAVING sum(f.people_in + f.people_out) > 0""", rule["tenant_id"], quiet_for, rule["camera_id"])
            for r in rows:
                if await conn.fetchval("SELECT 1 FROM app.alerts WHERE tenant_id=$1 AND dedupe_key=$2 AND created_at > now() - interval '30 minutes'", rule["tenant_id"], f"afterhours:{r['id']}"):
                    continue                                # already told: do not take another picture for nothing
                pic = await _camera_picture(conn, rule["tenant_id"], r["id"])
                if await raise_alert(conn, rule["tenant_id"], "after_hours", title=f"After-hours activity at {r['name']}",
                                     body=f"{r['people']} {'person was' if r['people'] == 1 else 'people were'} counted at {r['name']} while you are closed.",
                                     camera_id=r["id"], camera_name=r["name"], details={"people": r["people"]}, dedupe_key=f"afterhours:{r['id']}", cooldown_minutes=30, evidence_id=pic, rule_id=rule["id"]):
                    n += 1
    return n


# ------------------------------------------------------------------------------------------------ System Alerts that watch the account and the devices
async def _param(conn, tenant_id, rule_type: str, key: str, default):
    r = await rules.effective(conn, tenant_id, rule_type)
    return (r["params"] if r else {}).get(key, default)


async def connection_unstable() -> int:
    """A recorder that went offline `drops` times in the last hour (3 unless the customer changed it). At most one alert every six hours for it.
    A drop is only counted after 90 seconds of silence, so three in an hour is already a bad line."""
    n = 0
    async with db.service_tx() as conn:
        await conn.execute("DELETE FROM app.recorder_link_events WHERE at < now() - interval '14 days'")
        await conn.execute("DELETE FROM app.zone_samples WHERE at < now() - interval '3 days'")
        rows = await conn.fetch(
            """SELECT e.tenant_id, e.recorder_id, r.device_id, r.name, count(*)::int AS drops FROM app.recorder_link_events e JOIN app.recorders r ON r.id = e.recorder_id
               WHERE e.status='offline' AND e.at > now() - interval '1 hour' GROUP BY 1,2,3,4 HAVING count(*) >= 2""")
        for r in rows:
            if r["drops"] < int(await _param(conn, r["tenant_id"], "connection_unstable", "drops", 3)):
                continue
            name = r["name"] or r["device_id"]
            if await raise_alert(conn, r["tenant_id"], "connection_unstable", body=f"{name} went offline {r['drops']} times in the last hour. Check its internet connection and network cable.",
                                 camera_name=r["device_id"], details={"recorder": name, "drops": r["drops"]}, dedupe_key=f"unstable:{r['recorder_id']}", cooldown_minutes=360):
                n += 1
    return n


async def daily_summary() -> int:
    """The daily and the weekly summary, for every customer whose hour it is (see services/summaries.py)."""
    from . import summaries
    return await summaries.run()
