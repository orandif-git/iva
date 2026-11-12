"""Opening hours, and the two alerts that need them: Counting stopped (while open) and After-hours activity (while closed)."""
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services import alert_catalog as cat, detectors, maintenance
from app.services.opening_hours import Hours
from tests.helpers import setup_cameras


def test_open_and_closed_are_worked_out_from_two_times_and_the_closed_days():
    h = Hours(time(9), time(21), (6,))                                    # nine to nine, closed on Sunday
    mon = datetime(2026, 11, 9, 12, 0)                                    # a Monday
    assert h.is_open(mon) and not h.is_open(mon.replace(hour=22)) and not h.is_open(mon.replace(hour=8))
    sun = datetime(2026, 11, 15, 12, 0)
    assert not h.is_open(sun) and h.closed_since(sun) == datetime(2026, 11, 14, 21, 0), "closed since Saturday night"
    assert h.opens_next(sun) == datetime(2026, 11, 16, 9, 0) and h.closed_since(mon) is None
    assert h.session(mon) == (datetime(2026, 11, 9, 9, 0), datetime(2026, 11, 9, 21, 0))
    late = Hours(time(18), time(2))                                       # a place that closes after midnight
    assert late.is_open(datetime(2026, 11, 10, 1, 0)) and not late.is_open(datetime(2026, 11, 10, 3, 0)) and late.closed_since(datetime(2026, 11, 10, 3, 0)) == datetime(2026, 11, 10, 2, 0)
    none = Hours(None, None)
    assert not none.set and not none.is_open(mon) and none.closed_since(mon) is None


def test_both_alerts_are_built_and_are_checked_on_a_clock():
    assert cat.CATALOG["counting_stopped"]["built"] and cat.CATALOG["after_hours"]["built"] and "after_hours" in cat.CRITICAL
    assert {"counting_stopped", "after_hours"} <= set(maintenance.TIMED)


async def test_the_owner_sets_the_opening_hours(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    assert (await client.get("/v1/opening-hours", headers=u["headers"])).json() == {"open": None, "close": None, "closed_days": [], "set": False}
    r = await client.put("/v1/opening-hours", json={"open": "09:00", "close": "21:30", "closed_days": [6]}, headers=u["headers"])
    assert r.status_code == 200 and r.json() == {"open": "09:00", "close": "21:30", "closed_days": [6], "set": True}
    assert (await client.put("/v1/opening-hours", json={"open": "09:00"}, headers=u["headers"])).status_code == 422
    assert (await client.put("/v1/opening-hours", json={"open": "09:00", "close": "09:00"}, headers=u["headers"])).status_code == 422
    assert (await client.put("/v1/opening-hours", json={"open": "09:00", "close": "21:00", "closed_days": [7]}, headers=u["headers"])).status_code == 422
    viewer = await make.user(t, role="regular")
    assert (await client.put("/v1/opening-hours", json={"open": "10:00", "close": "20:00"}, headers=viewer["headers"])).status_code == 403
    warn = (await client.put("/v1/alert-types/counting_stopped", json={"enabled": True}, headers=u["headers"])).json()["warnings"]
    assert not any("opening hours" in w for w in warn)
    assert (await client.put("/v1/opening-hours", json={}, headers=u["headers"])).json()["set"] is False
    warn = (await client.put("/v1/alert-types/counting_stopped", json={"enabled": True}, headers=u["headers"])).json()["warnings"]
    assert any("opening hours" in w for w in warn)


async def hours_around_now(su, t, open_ago_h, close_in_h):
    """Sets the opening hours so that, in the customer's own time, the place opened `open_ago_h` hours ago and closes in `close_in_h` (negative: it closed that long ago)."""
    tz = await su.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", t)
    local = datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0)
    await su.execute("UPDATE app.tenants SET open_time=$2, close_time=$3, closed_days='{}' WHERE id=$1", t, (local - timedelta(hours=open_ago_h)).time(), (local + timedelta(hours=close_in_h)).time())


async def test_counting_stopped_is_said_once_for_an_online_camera_that_counts_nobody_while_open(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now(), stream_state_at = now() - interval '5 hours' WHERE id=$1", foot)
    await detectors.counting_stopped()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "no opening hours: nothing can be said"
    await hours_around_now(su, t, 1, 6)
    await detectors.counting_stopped()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "open for an hour only: too early"
    await hours_around_now(su, t, 3, 6)
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,date_trunc('minute', now() - interval '30 minutes'),1,0)", t, foot)
    await detectors.counting_stopped()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "it counted someone half an hour ago"
    await su.execute("DELETE FROM app.footfall_counts WHERE camera_id=$1", foot)
    await su.execute("INSERT INTO platform.faults (kind, cameras) VALUES ('analysis_stopped', 1)")
    await detectors.counting_stopped()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "our own analysis is stopped: not the customer's camera"
    await su.execute("TRUNCATE platform.faults")
    assert await detectors.counting_stopped() >= 1 and await detectors.counting_stopped() == 0
    a = await su.fetchrow("SELECT title, body, rule_type FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"] == "Counting stopped" and "counted nobody in the last 2 hours" in a["body"]
    await su.execute("UPDATE app.tenants SET open_time=NULL, close_time=NULL WHERE id=$1", t)
    await su.execute("UPDATE app.cameras SET stream_state='offline' WHERE id=$1", foot)


async def test_after_hours_activity_is_people_counted_while_closed_after_the_grace_time(client, make, su):
    await su.execute("DELETE FROM app.rules WHERE rule_type='after_hours'; DELETE FROM app.alert_rules WHERE rule_type='after_hours'")
    t, u, (foot,), _ = await setup_cameras(client, make)
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params) VALUES ($1,'after_hours',true,$2)", t, json.dumps({"grace_minutes": 30}))
    count = "INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,date_trunc('minute', now() - interval '2 minutes'),2,0) ON CONFLICT DO NOTHING"
    await su.execute(count, t, foot)
    await hours_around_now(su, t, 3, 6)                                   # open: people are expected
    assert await detectors.after_hours() == 0
    tz = await su.fetchval("SELECT timezone FROM app.tenants WHERE id=$1", t)
    local = datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0)
    just = lambda mins: (local - timedelta(minutes=mins)).time()
    await su.execute("UPDATE app.tenants SET open_time=$2, close_time=$3 WHERE id=$1", t, just(600), just(10))      # closed ten minutes ago: staff are still locking up
    assert await detectors.after_hours() == 0
    await su.execute("UPDATE app.tenants SET open_time=$2, close_time=$3 WHERE id=$1", t, just(600), just(120))     # closed two hours ago
    assert await detectors.after_hours() == 1 and await detectors.after_hours() == 0
    a = await su.fetchrow("SELECT title, body, severity FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"].startswith("After-hours activity at ") and "2 people were counted" in a["body"] and a["severity"] == "critical"
    await su.execute("DELETE FROM app.rules WHERE rule_type='after_hours'; DELETE FROM app.alert_rules WHERE rule_type='after_hours'")
    await su.execute("UPDATE app.tenants SET open_time=NULL, close_time=NULL WHERE id=$1", t)
