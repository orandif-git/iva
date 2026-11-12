"""What a recorder says about itself (disks, clock, recordings) and the four alerts that come from it."""
from datetime import datetime, timedelta, timezone

from app.services import recorder_health as rh
from tests.helpers import setup_cameras


def disk(num=0, total=1000, free=500, state=1):
    return {"num": num, "total_mb": total, "free_mb": free, "status": state, "state": state}


def test_how_full_the_disks_are_ignores_a_faulty_or_empty_disk():
    assert rh.percent_full([disk(total=1000, free=100), disk(1, total=1000, free=300)]) == 80
    assert rh.percent_full([disk(total=1000, free=100), disk(1, total=4000, free=0, state=2)]) == 90, "a faulty disk is a different alert"
    assert rh.percent_full([disk(total=0, free=0)]) is None and rh.percent_full([]) is None
    assert rh.percent_full([disk(total=1000, free=5000)]) == 0, "free space above the size is a misread, not a negative"


def test_the_clock_is_compared_by_utc_when_the_recorder_gives_it_and_by_local_time_otherwise():
    now = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    assert rh.minutes_out({"utc": int((now + timedelta(minutes=12)).timestamp()), "local": None}, "Asia/Kolkata", now) == 12
    local = (now + timedelta(hours=5, minutes=30, seconds=20)).strftime("%Y-%m-%dT%H:%M:%S")          # India is five and a half hours ahead of UTC
    assert rh.minutes_out({"utc": None, "local": local}, "Asia/Kolkata", now) == 0
    assert rh.minutes_out({"utc": 0, "local": "not a time"}, "Asia/Kolkata", now) is None and rh.minutes_out({}, "Asia/Kolkata", now) is None


async def report(client, make, device, body):
    r = await client.post(f"/internal/v1/recorders/{device}/health", json=body, headers=make.service())
    assert r.status_code == 200, r.text
    return r.json()["raised"]


async def recorder(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    return t, (await su.fetchval("SELECT device_id FROM app.recorders WHERE tenant_id=$1", t))


async def titles(su, t):
    return sorted(r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1", t))


async def test_a_healthy_recorder_raises_nothing_and_what_it_said_is_kept(client, make, su):
    t, dev = await recorder(client, make, su)
    now = datetime.now(timezone.utc)
    ok = {"disks": [disk(total=1000, free=600)], "clock": {"utc": int(now.timestamp()), "local": None}, "recording": {"1": {"recent": 4, "day": 30}}}
    assert await report(client, make, dev, ok) == [] and await titles(su, t) == []
    kept = await su.fetchrow("SELECT health, health_at FROM app.recorders WHERE device_id=$1", dev)
    assert kept["health_at"] is not None and "disks" in str(kept["health"])


async def test_a_faulty_or_missing_disk_is_a_critical_alert_said_once_a_day(client, make, su):
    t, dev = await recorder(client, make, su)
    assert await report(client, make, dev, {"disks": [disk(0, state=1), disk(1, state=2)]}) == ["disk_fault"]
    assert await report(client, make, dev, {"disks": [disk(0, state=1), disk(1, state=2)]}) == [], "not again within the day"
    a = await su.fetchrow("SELECT title, body, severity FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"] == "Recorder disk failure" and a["severity"] == "critical" and "disk 2" in a["body"]
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    assert await report(client, make, dev, {"disks": []}) == ["disk_fault"], "a recorder with no disk at all cannot save video"
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    assert await report(client, make, dev, {"disks": None}) == [], "not being able to read the disks is not a fault"


async def test_a_disk_that_is_full_is_not_an_alert_because_a_recorder_replaces_its_oldest_video(client, make, su):
    t, dev = await recorder(client, make, su)
    for free in (150, 80, 0):
        assert await report(client, make, dev, {"disks": [disk(total=1000, free=free)]}) == [], f"{free} of 1000 free: full by design"
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0
    kept = await su.fetchval("SELECT health->>'percent_full' FROM app.recorders WHERE device_id=$1", dev)
    assert kept == "100", "how full it is is still stored, for display"


async def test_a_clock_that_is_out_is_reported(client, make, su):
    t, dev = await recorder(client, make, su)
    now = datetime.now(timezone.utc)
    assert await report(client, make, dev, {"clock": {"utc": int((now + timedelta(minutes=3)).timestamp())}}) == []
    assert await report(client, make, dev, {"clock": {"utc": int((now - timedelta(minutes=40)).timestamp())}}) == ["clock_wrong"]
    a = await su.fetchrow("SELECT title, body FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"] == "Recorder time incorrect" and "minutes out" in a["body"]


async def test_recording_stopped_is_said_only_when_a_channel_recorded_earlier_but_not_in_two_hours(client, make, su):
    t, dev = await recorder(client, make, su)
    assert await report(client, make, dev, {"recording": {"1": {"recent": 0, "day": 0}, "2": {"recent": None, "day": None}}}) == [], "never recorded, or could not be asked: no claim"
    assert await report(client, make, dev, {"recording": {"1": {"recent": 5, "day": 30}, "2": {"recent": 0, "day": 22}}}) == ["recording_stopped"]
    a = await su.fetchrow("SELECT title, body, severity FROM app.alerts WHERE tenant_id=$1", t)
    assert a["title"] == "Recording stopped" and a["severity"] == "critical" and "1 camera" in a["body"]


async def test_the_readings_must_come_from_the_gateway_and_be_well_formed(client, make, su):
    t, dev = await recorder(client, make, su)
    bad = await client.post(f"/internal/v1/recorders/{dev}/health", json={"disks": [{"num": 0, "total_mb": -1, "free_mb": 0, "status": 1, "state": 1}]}, headers=make.service())
    assert bad.status_code == 422
    assert (await client.post(f"/internal/v1/recorders/{dev}/health", json={})).status_code in (401, 403)
    assert (await client.post("/internal/v1/recorders/NOPE/health", json={}, headers=make.service())).status_code == 404


async def test_the_health_tab_gets_one_line_per_camera_and_a_week_of_days(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    dev = await su.fetchval("SELECT device_id FROM app.recorders WHERE tenant_id=$1", t)
    now = datetime.now(timezone.utc)
    await report(client, make, dev, {"disks": [disk(total=1000, free=600)], "clock": {"utc": int(now.timestamp()), "local": None}, "recording_now": {"1": True, "2": False}})
    d = (await client.get("/v1/health-detail", headers=u["headers"])).json()
    assert len(d["days"]) == 7 and d["days"][-1]["problems"] == 0
    assert d["cameras"] and all({"name", "online", "recording", "picture", "seconds_since_picture"} <= set(c) for c in d["cameras"])
    assert {c["recording"] for c in d["cameras"]} <= {True, False, None}


def test_the_phone_gets_the_alert_name_and_one_plain_fact_without_limits_or_orders():
    from app.services.push_text import compose
    now = "11:42 PM"
    assert compose("crowding", "Occupancy limit exceeded", "Jas Office", {"people": 4, "limit": 3, "minutes": 2}, now, "t", "b") == ("Occupancy limit exceeded", "Jas Office: 4 people inside for the last 2 minutes.")
    assert compose("queue_long", "Long queue", "Cam 1", {"area": "Billing Counter", "length": 8, "limit": 5}, now, "t", "b")[1] == "Billing Counter: 8 people waiting."
    assert compose("disk_wear", "Recorder disk health low", "", {"recorder": "Warehouse", "disk": "Disk 2", "percent_left": 12}, now, "t", "b")[1] == "Warehouse: Disk 2 has about 12% life left."
    assert compose("recorder_restarted", "Recorder restarted", "", {"recorder": "Warehouse", "times": 1}, now, "t", "b")[1] == "Warehouse: restarted once."
    assert compose("blocked_plate", "Blocked vehicle detected", "Main Gate", {"plate": "GJ05AB1234"}, now, "t", "b")[1] == "Main Gate: GJ05AB1234 arrived at 11:42 PM."
    assert compose("daily_summary", "Daily system summary", "", {"summary": "7 cameras working, 1 needs a look."}, now, "t", "b")[1] == "7 cameras working, 1 needs a look."
    assert compose("clock_wrong", "Recorder time incorrect", "", {"recorder": "W"}, now, "t", "the long message") == ("Recorder time incorrect", "the long message"), "a missing number falls back to the full message"
    assert compose("report_ready", "Report ready", "", {}, now, "own title", "own body") == ("own title", "own body"), "alerts without a short form are unchanged"
    for rule in ("crowding", "people_in_area", "queue_long", "retention_low", "counting_stopped"):
        text = compose(rule, "n", "P", {"people": 4, "length": 4, "days": 14, "hours": 3, "limit": 3, "minutes": 2, "area": "A"}, now, "t", "b")[1].lower()
        assert "limit" not in text and "target" not in text and "please" not in text and "check" not in text
