"""Excel reports: what is in them, which data they use, who may make them, and how scheduled ones are queued and emailed."""
import asyncio
import io
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from app.messaging import delivery, evidence
from app.services import report_builders as rb, reports, retention
from tests.helpers import setup_cameras
from tests.test_alert_delivery import bsp_server, smtp_server, world  # noqa: F401  (fixtures: working email provider)

IST = ZoneInfo("Asia/Kolkata")


def at(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


async def plate(su, t, cam, when, text="GJ14AW1013", direction="in", review="auto", dwell=None, entry=None, vt="Truck", conf=95):
    return await su.fetchval(
        """INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, plate_raw, confidence, vehicle_type, direction, review_status, dwell_seconds, entry_event_id)
           VALUES ($1,$2,$3,$4,$4,$5,$6,$7::app.direction,$8::app.review_status,$9,$10) RETURNING id""", t, cam, when, text, conf, vt, direction, review, dwell, entry)


async def make_report(client, u, kind, d_from, d_to):
    r = await client.post("/v1/reports", json={"report_type": kind, "from": str(d_from), "to": str(d_to)}, headers=u["headers"])
    assert r.status_code == 202, r.text
    return r.json()


async def build(su=None):
    while await reports.build_one():
        pass


async def download(client, u, rid):
    r = await client.get(f"/v1/reports/{rid}/download", headers=u["headers"])
    assert r.status_code == 200, r.text
    return r, openpyxl.load_workbook(io.BytesIO(r.content))


def rows_of(ws):
    return [[c.value for c in row] for row in ws.iter_rows()]


@pytest.fixture(autouse=True)
async def clean_queue(su):
    """Reports left waiting by an earlier test would otherwise be picked up by this test's builder."""
    await su.execute("UPDATE app.reports SET status='failed' WHERE status IN ('queued','working')")
    await su.execute("UPDATE app.notifications SET status='cancelled' WHERE status='queued'")


@pytest.fixture
async def shop(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.cameras SET name='Main gate' WHERE id=$1", cam)
    return t, u, cam


# ------------------------------------------------------------------------- gate register
async def test_the_gate_register_shows_each_read_in_local_time_with_a_daily_summary(client, make, su, shop):
    t, u, cam = shop
    first = await plate(su, t, cam, at(2026, 11, 3, 9, 5), "GJ14AW1013", "in")
    await plate(su, t, cam, at(2026, 11, 3, 17, 40), "GJ14AW1013", "out", dwell=31500, entry=first)
    await plate(su, t, cam, at(2026, 11, 3, 11, 0), "MH12AB1234", "in", review="needs_review", conf=52)
    await su.execute("INSERT INTO app.vehicle_lists (tenant_id, plate_text, list_type) VALUES ($1,'KA05MN4321','blocked')", t)
    await plate(su, t, cam, at(2026, 11, 4, 8, 15), "KA05MN4321", "in")
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 4))
    assert rep["status"] == "queued"
    await build()
    r, wb = await download(client, u, rep["id"])
    assert r.headers["content-disposition"] == 'attachment; filename="gate_register_03-Nov-2026_to_04-Nov-2026.xlsx"' and r.headers["cache-control"] == "private, no-store"
    reg = rows_of(wb["Gate register"])
    assert reg[0][:6] == ["Date", "Time", "Plate", "Vehicle", "Gate", "Direction"]
    assert [x[2] for x in reg[1:]] == ["GJ14AW1013", "MH12AB1234", "GJ14AW1013", "KA05MN4321"]          # in time order
    assert reg[1][0] == datetime(2026, 11, 3) and reg[1][1] == time(9, 5) and reg[1][4] == "Main gate" and reg[1][5] == "In"       # the tenant's clock, not UTC
    assert reg[3][8] == "8:45" and reg[3][5] == "Out" and reg[2][7] == "Needs checking" and reg[4][9] == "Blocked"
    summ = rows_of(wb["Daily summary"])
    assert summ[1] == [datetime(2026, 11, 3), 2, 1, 2, 0, 1] and summ[2] == [datetime(2026, 11, 4), 1, 0, 1, 1, 0]
    done = (await client.get(f"/v1/reports/{rep['id']}", headers=u["headers"])).json()
    assert done["status"] == "ready" and done["rows"] == 4 and done["bytes"] > 1000 and done["truncated"] is False and done["title"] == "Gate register"


async def test_a_period_is_whole_local_days_with_the_end_excluded(client, make, su, shop):
    t, u, cam = shop
    await plate(su, t, cam, at(2026, 11, 2, 23, 59, 59), "AA11AA1111")                                  # one second before
    await plate(su, t, cam, at(2026, 11, 3, 0, 0, 0), "BB22BB2222")                                      # first second
    await plate(su, t, cam, at(2026, 11, 3, 23, 59, 59), "CC33CC3333")                                   # last second
    await plate(su, t, cam, at(2026, 11, 4, 0, 0, 0), "DD44DD4444")                                      # next day: out
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await build()
    _, wb = await download(client, u, rep["id"])
    assert [x[2] for x in rows_of(wb["Gate register"])[1:]] == ["BB22BB2222", "CC33CC3333"]


async def test_only_the_customers_own_data_is_in_their_report(client, make, su, shop):
    t, u, cam = shop
    t2, u2, _, (cam2,) = await setup_cameras(client, make)
    await plate(su, t, cam, at(2026, 11, 3, 9), "OWN1OWN1111")
    await plate(su, t2, cam2, at(2026, 11, 3, 9), "OTH2OTH2222")
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await build()
    _, wb = await download(client, u, rep["id"])
    assert [x[2] for x in rows_of(wb["Gate register"])[1:]] == ["OWN1OWN1111"]
    assert (await client.get(f"/v1/reports/{rep['id']}", headers=u2["headers"])).status_code == 404
    assert (await client.get(f"/v1/reports/{rep['id']}/download", headers=u2["headers"])).status_code == 404
    assert (await client.get("/v1/reports", headers=u2["headers"])).json() == [] or all(x["id"] != rep["id"] for x in (await client.get("/v1/reports", headers=u2["headers"])).json())


async def test_text_a_customer_typed_never_becomes_a_formula(client, make, su, shop):
    t, u, cam = shop
    await su.execute("UPDATE app.cameras SET name='=HYPERLINK(\"http://evil.example\",\"click\")' WHERE id=$1", cam)
    await plate(su, t, cam, at(2026, 11, 3, 9))
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await build()
    r, wb = await download(client, u, rep["id"])
    cell = wb["Gate register"]["E2"]
    assert cell.value.startswith("=HYPERLINK") and cell.data_type == "s"                                  # shown as text, not run
    assert b"<f>" not in __import__("zipfile").ZipFile(io.BytesIO(r.content)).read("xl/worksheets/sheet1.xml")


async def test_a_huge_period_is_cut_and_the_file_says_so(client, make, su, shop, monkeypatch):
    t, u, cam = shop
    monkeypatch.setattr(rb, "MAX_ROWS", 3)
    for i in range(5):
        await plate(su, t, cam, at(2026, 11, 3, 9, i), f"TN72AB10{i}0")
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await build()
    _, wb = await download(client, u, rep["id"])
    done = (await client.get(f"/v1/reports/{rep['id']}", headers=u["headers"])).json()
    assert done["truncated"] is True and done["rows"] == 3 and len(rows_of(wb["Gate register"])) == 4
    assert "first 3 reads" in rows_of(wb["Read me"])[0][0]


# ------------------------------------------------------------------------- footfall and audit
async def test_footfall_summary(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make, footfall=1, anpr=1)
    await su.execute("UPDATE app.cameras SET name='Entrance' WHERE id=$1", foot)
    for when, i, o in ((at(2026, 11, 3, 9, 10), 10, 2), (at(2026, 11, 3, 9, 40), 5, 1), (at(2026, 11, 3, 18, 0), 3, 12), (at(2026, 11, 4, 9, 5), 8, 0), (at(2026, 11, 5, 9, 5), 99, 99)):
        await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,$3,$4,$5)", t, foot, when, i, o)
    rep = await make_report(client, u, "footfall_summary", date(2026, 11, 3), date(2026, 11, 4))
    await build()
    _, wb = await download(client, u, rep["id"])
    assert rows_of(wb["By day and camera"])[1:] == [[datetime(2026, 11, 3), "Entrance", 18, 15, 3], [datetime(2026, 11, 4), "Entrance", 8, 0, 8]]
    assert rows_of(wb["By day"])[1:] == [[datetime(2026, 11, 3), 18, 15, 3], [datetime(2026, 11, 4), 8, 0, 8]]
    hours = {r[0]: r for r in rows_of(wb["By hour of day"])[1:]}
    assert len(hours) == 24 and hours["09:00"][1:] == [23, 3, 11.5] and hours["18:00"][1] == 3 and hours["03:00"][1] == 0           # average per day over a two-day period


async def test_vehicle_audit_lists_visits_and_who_is_still_inside(client, make, su, shop):
    t, u, cam = shop
    e1 = await plate(su, t, cam, at(2026, 11, 3, 9), "GJ14AW1013", "in")
    await plate(su, t, cam, at(2026, 11, 3, 13), "GJ14AW1013", "out", dwell=4 * 3600, entry=e1)
    e2 = await plate(su, t, cam, at(2026, 11, 4, 9), "GJ14AW1013", "in")
    await plate(su, t, cam, at(2026, 11, 4, 11), "GJ14AW1013", "out", dwell=2 * 3600, entry=e2)
    await plate(su, t, cam, at(2026, 11, 4, 10), "KA05MN4321", "in")                                      # never left
    await plate(su, t, cam, at(2026, 11, 4, 10, 30), "MH12AB1234", "in", review="needs_review", conf=40)  # doubtful: not audited
    rep = await make_report(client, u, "vehicle_audit", date(2026, 11, 3), date(2026, 11, 4))
    await build()
    _, wb = await download(client, u, rep["id"])
    v = {r[0]: r for r in rows_of(wb["Vehicles"])[1:]}
    assert set(v) == {"GJ14AW1013", "KA05MN4321"} and v["GJ14AW1013"][3:7] == [2, 2, 6.0, 3.0] and v["KA05MN4321"][3:5] == [1, 0]
    inside = rows_of(wb["Still inside"])[1:]
    assert [r[0] for r in inside] == ["KA05MN4321"] and inside[0][2] == "Main gate" and inside[0][4] > 24


# ------------------------------------------------------------------------- who may ask, and limits
async def test_asking_for_a_report_is_checked(client, make, su, shop):
    t, u, cam = shop
    h = u["headers"]
    reg = await make.user(t, "regular")
    body = lambda **k: {"report_type": "gate_register", "from": "2026-11-03", "to": "2026-11-03", **k}
    assert (await client.post("/v1/reports", json=body(), headers=reg["headers"])).status_code == 403          # only an administrator asks
    assert (await client.get("/v1/reports", headers=reg["headers"])).status_code == 200                         # anyone can read and download
    for b, code in [(body(to="2026-11-01"), "invalid_period"), (body(to="2026-12-31"), "period_too_long"), (body(report_type="vehicle_audit", **{"from": "2026-01-01", "to": "2026-11-04"}), "period_too_long"),
                    (body(**{"from": "2099-01-01", "to": "2099-01-02"}), "future_period")]:
        r = await client.post("/v1/reports", json=b, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (b, r.text)
    assert (await client.post("/v1/reports", json=body(report_type="payroll"), headers=h)).status_code == 422
    first = await client.post("/v1/reports", json=body(), headers=h)
    assert (await client.get(f"/v1/reports/{first.json()['id']}/download", headers=h)).json()["error"]["code"] == "not_ready"
    for _ in range(4):
        assert (await client.post("/v1/reports", json=body(), headers=h)).status_code == 202
    assert (await client.post("/v1/reports", json=body(), headers=h)).json()["error"]["code"] == "too_many_reports"          # five waiting is the limit
    await build()
    for _ in range(15):
        await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3)); await build()
    assert (await client.post("/v1/reports", json=body(), headers=h)).json()["error"]["code"] == "daily_limit"


async def test_a_report_that_cannot_be_built_says_so_and_is_not_retried(client, make, su, shop, monkeypatch):
    t, u, cam = shop
    monkeypatch.setattr(rb, "gate_register", lambda *a, **k: 1 / 0)
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    assert await reports.build_one() is True and await reports.build_one() is False
    done = (await client.get(f"/v1/reports/{rep['id']}", headers=u["headers"])).json()
    assert done["status"] == "failed" and "try again" in done["error"] and "division" not in done["error"]          # no internals shown
    assert (await client.get(f"/v1/reports/{rep['id']}/download", headers=u["headers"])).json()["error"]["code"] == "not_ready"


async def test_two_builders_never_build_the_same_report_and_a_crashed_one_is_replaced(client, make, su, shop):
    t, u, cam = shop
    ids = [(await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3)))["id"] for _ in range(3)]
    results = await asyncio.gather(reports.build_one(), reports.build_one(), reports.build_one(), reports.build_one())
    assert sorted(results) == [False, True, True, True]
    assert await su.fetchval("SELECT count(*) FROM app.reports WHERE tenant_id=$1 AND status='ready'", t) == 3
    stuck = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await su.execute("UPDATE app.reports SET status='working', locked_until = now() + interval '5 minutes' WHERE id=$1", uuid.UUID(stuck["id"]))
    assert await reports.build_one() is False                                                          # still being worked on
    await su.execute("UPDATE app.reports SET locked_until = now() - interval '1 second' WHERE id=$1", uuid.UUID(stuck["id"]))
    assert await reports.build_one() is True and (await client.get(f"/v1/reports/{stuck['id']}", headers=u["headers"])).json()["status"] == "ready"


async def test_expired_reports_and_their_files_are_removed(client, make, su, shop):
    from app.messaging import evidence
    t, u, cam = shop
    rep = await make_report(client, u, "gate_register", date(2026, 11, 3), date(2026, 11, 3))
    await build()
    key = await su.fetchval("SELECT file_key FROM app.reports WHERE id=$1", uuid.UUID(rep["id"]))
    assert await evidence.store().get(key)
    await su.execute("UPDATE app.reports SET expires_at = now() - interval '1 day' WHERE id=$1", uuid.UUID(rep["id"]))
    out = await retention.run_all()
    assert out["reports"] >= 1 and await evidence.store().get(key) is None
    assert (await client.get(f"/v1/reports/{rep['id']}", headers=u["headers"])).status_code == 404


# ------------------------------------------------------------------------- schedules
def sched(**k):
    return {"report_type": "gate_register", "frequency": "daily", "send_at": "07:00", "recipient_ids": [], **k}


async def person(client, u, email="owner@x.test", phone="98765 43210"):
    body = {"name": "Owner", "consent_confirmed": True}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    r = await client.post("/v1/recipients", json=body, headers=u["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_schedules_are_validated_and_isolated(client, make, su, shop):
    t, u, cam = shop
    h = u["headers"]
    rid = await person(client, u)
    no_phone = (await client.post("/v1/recipients", json={"name": "Mailer", "email": "mailer@x.test"}, headers=h)).json()["id"]
    ok = await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid]), headers=h)
    assert ok.status_code == 201 and ok.json()["send_at"] == "07:00" and ok.json()["recipient_ids"] == [rid]
    assert (await client.post("/v1/report-schedules", json=sched(), headers=h)).json()["error"]["code"] == "schedule_exists"
    assert (await client.post("/v1/report-schedules", json=sched(frequency="weekly"), headers=h)).json()["error"]["code"] == "weekday_needed"
    assert (await client.post("/v1/report-schedules", json=sched(frequency="monthly"), headers=h)).json()["error"]["code"] == "day_needed"
    assert (await client.post("/v1/report-schedules", json=sched(frequency="monthly", day_of_month=31), headers=h)).status_code == 422
    assert (await client.post("/v1/report-schedules", json=sched(frequency="weekly", weekday=0, recipient_ids=[no_phone]), headers=h)).json()["error"]["code"] == "no_mobile_number"
    t2, u2, _, _ = await setup_cameras(client, make)
    other = await person(client, u2, "other@x.test", "98765 43299")
    assert (await client.post("/v1/report-schedules", json=sched(frequency="weekly", weekday=1, recipient_ids=[other]), headers=h)).status_code == 422       # not their person
    reg = await make.user(t, "regular")
    assert (await client.post("/v1/report-schedules", json=sched(frequency="weekly", weekday=2), headers=reg["headers"])).status_code == 403
    assert len((await client.get("/v1/report-schedules", headers=reg["headers"])).json()) == 1
    assert (await client.get("/v1/report-schedules", headers=u2["headers"])).json() == []
    sid = ok.json()["id"]
    assert (await client.put(f"/v1/report-schedules/{sid}", json=sched(send_at="18:30", enabled=False), headers=u2["headers"])).status_code == 404
    up = await client.put(f"/v1/report-schedules/{sid}", json=sched(send_at="18:30", enabled=False, recipient_ids=[rid]), headers=h)
    assert up.json()["send_at"] == "18:30" and up.json()["enabled"] is False
    assert (await client.delete(f"/v1/report-schedules/{sid}", headers=u2["headers"])).status_code == 404
    assert (await client.delete(f"/v1/report-schedules/{sid}", headers=h)).status_code == 204


def S(freq, at_=time(7, 0), weekday=None, dom=None):
    return {"frequency": freq, "send_at": at_, "weekday": weekday, "day_of_month": dom}


def span(per):
    return tuple(x.astimezone(IST).strftime("%Y-%m-%d %H:%M") for x in per)


@pytest.mark.parametrize("sch,now,expected", [
    (S("daily"), at(2026, 11, 5, 8), ("2026-11-04 00:00", "2026-11-05 00:00")),                       # after 07:00: yesterday
    (S("daily"), at(2026, 11, 5, 6, 59), ("2026-11-03 00:00", "2026-11-04 00:00")),                   # before 07:00: that was yesterday's job
    (S("weekly", weekday=0), at(2026, 11, 4, 12), ("2026-10-26 00:00", "2026-11-02 00:00")),          # Wednesday: the week before this Monday
    (S("weekly", weekday=0), at(2026, 11, 2, 6), ("2026-10-19 00:00", "2026-10-26 00:00")),           # Monday before 07:00
    (S("weekly", weekday=0), at(2026, 11, 2, 7), ("2026-10-26 00:00", "2026-11-02 00:00")),           # Monday at 07:00
    (S("monthly", dom=5), at(2026, 11, 4, 12), ("2026-09-01 00:00", "2026-10-01 00:00")),             # before the 5th: October's job covered September
    (S("monthly", dom=5), at(2026, 11, 5, 8), ("2026-10-01 00:00", "2026-11-01 00:00")),              # the 5th: October
    (S("monthly", dom=1), at(2027, 1, 1, 9), ("2026-12-01 00:00", "2027-01-01 00:00")),               # across the new year
])
def test_which_period_a_schedule_covers(sch, now, expected):
    assert span(reports.latest_period(sch, now.astimezone(timezone.utc), "Asia/Kolkata")) == expected


async def test_due_reports_are_queued_once_per_period(client, make, su, shop):
    t, u, cam = shop
    await su.execute("UPDATE app.tenants SET retention_events_days=60 WHERE id=$1", t)
    sid = uuid.UUID((await client.post("/v1/report-schedules", json=sched(), headers=u["headers"])).json()["id"])
    now = datetime.now(timezone.utc)
    assert await reports.queue_due(now) == 0                                                          # a new schedule waits for its next time
    nxt = now + timedelta(days=1)
    assert await reports.queue_due(nxt.replace(hour=2, minute=30)) == 1                               # 08:00 the next day in India
    assert await reports.queue_due(nxt.replace(hour=2, minute=31)) == 0                               # the same period is not queued twice
    assert await reports.queue_due(nxt + timedelta(days=4)) == 1                                      # four missed days: only the newest is produced
    n = await su.fetchrow("SELECT count(*) AS n, bool_and(schedule_id IS NOT NULL) AS sched, bool_and(requested_by IS NULL) AS auto, max(expires_at) - min(created_at) AS keep FROM app.reports WHERE tenant_id=$1", t)
    assert n["n"] == 2 and n["sched"] and n["auto"] and n["keep"] > timedelta(days=59)
    await su.execute("UPDATE app.report_schedules SET enabled=false WHERE id=$1", sid)
    assert await reports.queue_due(nxt + timedelta(days=9)) == 0                                      # switched off
    await su.execute("UPDATE app.report_schedules SET enabled=true WHERE id=$1", sid)
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert await reports.queue_due(nxt + timedelta(days=9)) == 0                                      # a suspended account gets nothing
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_a_scheduled_report_is_announced_on_whatsapp_with_a_link_to_the_file(client, make, su, shop, world):
    t, u, cam = shop
    # The schedule is set for 07:00 India time, so test at 08:00 India time tomorrow whatever the time of day it is now (a fixed offset from now
    # failed whenever the test ran between midnight and 07:00 in India).
    when = (datetime.now(IST) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    await plate(su, t, cam, datetime.combine(when.astimezone(IST).date() - timedelta(days=1), time(9, 0), IST), "GJ14AW1013", "in")        # a read on the day it covers
    rid = await person(client, u, "boss@x.test", "98765 43211")
    off = await person(client, u, "away@x.test", "98765 43212")
    assert (await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid, off]), headers=u["headers"])).status_code == 201
    await su.execute("UPDATE app.recipients SET active=false WHERE id=$1", uuid.UUID(off))                   # switched off after being chosen
    await reports.queue_due(when)
    await build()
    ns = await su.fetch("SELECT n.status::text AS status, n.recipient, n.channel::text AS ch, n.alert_id, n.event FROM app.notifications n WHERE n.tenant_id=$1 AND n.report_id IS NOT NULL", t)
    assert [(n["recipient"], n["ch"], n["status"], n["alert_id"], n["event"]) for n in ns] == [("+919876543211", "whatsapp", "queued", None, "system.report_ready")]       # the inactive person is skipped
    world["hook"].calls.clear()
    assert await delivery.run_once() == 1
    assert await su.fetchval("SELECT status::text FROM app.notifications WHERE tenant_id=$1 AND report_id IS NOT NULL", t) == "sent"
    call = world["hook"].calls[-1]
    assert call["headers"]["x-event"] == "system.report_ready"
    b = call["body"]
    assert b["phone_number"] == "919876543211" and b["report"] == "Gate register" and b["period"] and "document_url" in b and b["template_name"] == "system_report_ready_v1"
    assert "email" not in json.dumps(b).lower()
    link = b["document_url"]
    assert link.endswith("/report.xlsx") and "/e/" in link
    got = await client.get("/e/" + link.split("/e/")[1])                                                   # the link works without signing in
    assert got.status_code == 200 and got.headers["content-type"].startswith("application/vnd.openxmlformats") and "attachment" in got.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(got.content))
    assert rows_of(wb["Gate register"])[1][2] == "GJ14AW1013"                                         # the file is the real report
    assert world["smtp"].messages == []                                                               # nothing is emailed any more


async def test_a_report_link_expires_and_cannot_be_forged(client, make, su, shop, world):
    t, u, cam = shop
    rid = await person(client, u, None, "98765 43213")
    await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid]), headers=u["headers"])
    await reports.queue_due(datetime.now(timezone.utc) + timedelta(days=1, hours=1)); await build()
    rep = await su.fetchrow("SELECT id FROM app.reports WHERE tenant_id=$1 AND status='ready'", t)
    good = evidence.make_token(rep["id"], 3600)
    assert (await client.get(f"/e/{good}/report.xlsx")).status_code == 200
    assert (await client.get(f"/e/{evidence.make_token(rep['id'], -10)}/report.xlsx")).status_code == 410            # expired
    assert (await client.get(f"/e/{good[:-3]}abc/report.xlsx")).status_code == 404                                  # not ours
    assert (await client.get(f"/e/{evidence.make_token(uuid.uuid4(), 3600)}/report.xlsx")).status_code == 404       # not a report
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await client.get(f"/e/{good}/report.xlsx")).status_code == 404                                           # a suspended account gets nothing
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_a_report_to_an_account_with_whatsapp_switched_off_is_not_sent(client, make, su, shop, world):
    t, u, cam = shop
    rid = await person(client, u, None, "98765 43214")
    await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid]), headers=u["headers"])
    await su.execute("UPDATE app.tenants SET msg_whatsapp=false WHERE id=$1", t)
    await reports.queue_due(datetime.now(timezone.utc) + timedelta(days=1, hours=1)); await build()
    n = await su.fetchrow("SELECT status::text AS s, error FROM app.notifications WHERE tenant_id=$1 AND report_id IS NOT NULL", t)
    assert n["s"] == "suppressed" and "switched off" in n["error"]
    assert (await client.get("/v1/reports", headers=u["headers"])).json()[0]["scheduled"] is True                 # still available in the app


async def test_a_report_without_a_webhook_is_recorded_as_not_sent(client, make, su, shop, world):
    t, u, cam = shop
    rid = await person(client, u, None, "98765 43215")
    await su.execute("DELETE FROM platform.webhook_endpoints WHERE alert_type='report_ready'")
    await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid]), headers=u["headers"])
    await reports.queue_due(datetime.now(timezone.utc) + timedelta(days=1, hours=1)); await build()
    n = await su.fetchrow("SELECT status::text AS s, error FROM app.notifications WHERE tenant_id=$1 AND report_id IS NOT NULL", t)
    assert n["s"] == "suppressed" and "No webhook" in n["error"]


async def test_deleting_a_person_cancels_their_waiting_report_message(client, make, su, shop, world):
    t, u, cam = shop
    rid = await person(client, u, None, "98765 43216")
    await client.post("/v1/report-schedules", json=sched(recipient_ids=[rid]), headers=u["headers"])
    await reports.queue_due(datetime.now(timezone.utc) + timedelta(days=1, hours=1)); await build()
    world["hook"].calls.clear()
    assert (await client.delete(f"/v1/recipients/{rid}", headers=u["headers"])).status_code == 204
    assert await delivery.run_once() == 0 and world["hook"].calls == []


async def test_report_ready_is_a_system_event_and_not_an_alert_customers_see(client, make, su, shop, world):
    t, u, cam = shop
    names = {a["type"] for a in (await client.get("/v1/alert-types", headers=u["headers"])).json()}
    assert "report_ready" not in names
    eps = {e["alert_type"]: e for e in (await client.get("/admin/v1/webhooks", headers=world["admin"])).json()["items"]}
    assert "report_ready" in eps and eps["report_ready"]["event"] == "system.report_ready"
    assert {"report", "period", "document_url"} <= set(eps["report_ready"]["shortcodes"])
    assert "{document_url}" in eps["report_ready"]["payload_template"]
    items = (await client.get("/admin/v1/alert-catalog", headers=world["admin"])).json()["items"]
    assert "report_ready" not in {i["type"] for i in items}
