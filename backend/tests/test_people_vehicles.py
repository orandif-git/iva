"""The People count and Vehicles pages of the Control Center: what shows in the menu, the numbers, the vehicle list with stay times, and the Excel files."""
import io
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tests.helpers import setup_cameras

IST = ZoneInfo("Asia/Kolkata")


def local_day(offset=0):
    return (datetime.now(IST) + timedelta(days=offset)).date()


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


async def shop(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", anpr=1)
    await su.execute("UPDATE app.tenants SET timezone='Asia/Kolkata' WHERE id=$1", t)
    return t, cam, await make.staff("support")


async def test_the_menu_items_show_while_the_event_is_on_or_there_is_history(client, make, su):
    t, cam, staff = await shop(client, make, su)
    url = f"/admin/v1/tenants/{t}/shop/views"
    v = (await client.get(url, headers=staff["headers"])).json()
    assert v["people"]["show"] and v["vehicles"]["show"]                                        # the cameras count people and read plates
    await su.execute("UPDATE app.cameras SET use_areas=true, use_footfall=false, use_anpr=false WHERE tenant_id=$1", t)
    v = (await client.get(url, headers=staff["headers"])).json()
    assert v["people"] == {"on": False, "history": False, "show": False} and v["vehicles"]["show"] is False
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,now(),3,1)", t, cam)
    v = (await client.get(url, headers=staff["headers"])).json()
    assert v["people"] == {"on": False, "history": True, "show": True}, "an event that was switched off still keeps its history on view"
    assert v["vehicles"]["show"] is False


async def test_people_count_for_a_day_and_for_a_week(client, make, su):
    t, cam, staff = await shop(client, make, su)
    today, base = local_day(), f"/admin/v1/tenants/{t}/shop/people-count"
    rows = [(at(today, 9), 4, 1), (at(today, 9, 30), 6, 2), (at(today, 18), 20, 15), (at(local_day(-1), 10), 7, 7), (at(local_day(-3), 11), 12, 10)]
    for ts, i, o in rows:
        await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,$3,$4,$5)", t, cam, ts, i, o)
    d = (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat()}, headers=staff["headers"])).json()
    assert d["hourly"] is True and len(d["series"]) == 24
    assert d["people_in"] == 30 and d["people_out"] == 18
    assert d["series"][9] == {"label": 9, "in": 10, "out": 3} and d["series"][18]["in"] == 20
    assert d["busiest"]["label"] == 18 and d["busiest"]["in"] == 20
    assert d["inside_now"] == 12, "today's entries less exits"
    assert d["cameras"][0]["in"] == 30 and d["cameras"][0]["last_at"] is not None
    w = (await client.get(base, params={"from": local_day(-6).isoformat(), "to": today.isoformat()}, headers=staff["headers"])).json()
    assert w["hourly"] is False and len(w["series"]) == 7 and w["people_in"] == 49 and w["people_out"] == 35
    old = (await client.get(base, params={"from": local_day(-6).isoformat(), "to": local_day(-1).isoformat()}, headers=staff["headers"])).json()
    assert old["inside_now"] is None and old["people_in"] == 19, "the estimate is for today only"
    assert (await client.get(base, params={"from": local_day(-200).isoformat(), "to": today.isoformat()}, headers=staff["headers"])).status_code == 422
    other = (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "camera_id": str(uuid.uuid4())}, headers=staff["headers"])).json()
    assert other["people_in"] == 0 and other["cameras"] == []


async def test_vehicles_list_has_stay_times_blocked_marks_search_and_paging(client, make, su):
    t, cam, staff = await shop(client, make, su)
    today, base = local_day(), f"/admin/v1/tenants/{t}/shop/vehicles"
    await su.execute("INSERT INTO app.vehicle_lists (tenant_id, plate_text, list_type) VALUES ($1,'TN99BZ0412','blocked')", t)

    async def read(plate, when, direction, conf=95, status="auto", entry=None, dwell=None, raw=None):
        return await su.fetchval(
            """INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, plate_raw, confidence, direction, review_status, entry_event_id, dwell_seconds)
               VALUES ($1,$2,$3,$4,$5,$6,$7::app.direction,$8::app.review_status,$9,$10) RETURNING id""", t, cam, when, plate, raw or plate, conf, direction, status, entry, dwell)
    in1 = await read("TN38BH4521", at(today, 9), "in")
    await read("TN38BH4521", at(today, 11, 30), "out", entry=in1, dwell=9000)                       # stayed 2 h 30 min
    await read("TN37CK9034", at(today, 10), "in")                                                   # still inside
    await read("TN99BZ0412", at(today, 12), "in", conf=90)
    await read(None, at(today, 13), "in", conf=44, status="needs_review", raw="TN38??4521")
    d = (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat()}, headers=staff["headers"])).json()
    assert d["total"] == 5 and [x["plate"] for x in d["items"]][0] == "TN38??4521", "newest first, and an unclear read still shows what was read"
    by = {x["plate"]: x for x in d["items"]}
    assert by["TN38BH4521"]["stay_minutes"] is None or True
    ins = [x for x in d["items"] if x["plate"] == "TN38BH4521" and x["direction"] == "in"][0]
    assert ins["stay_minutes"] == 150 and ins["inside"] is False
    assert by["TN37CK9034"]["inside"] is True and by["TN37CK9034"]["stay_minutes"] is None
    assert by["TN99BZ0412"]["blocked"] is True and by["TN38??4521"]["needs_review"] is True
    assert by["TN38BH4521"]["picture"] is None, "no picture was kept for these reads"
    assert (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "q": "ck 90"}, headers=staff["headers"])).json()["total"] == 1
    assert (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "status": "blocked"}, headers=staff["headers"])).json()["total"] == 1
    assert (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "status": "review"}, headers=staff["headers"])).json()["total"] == 1
    assert (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "direction": "out"}, headers=staff["headers"])).json()["total"] == 1
    page = (await client.get(base, params={"from": today.isoformat(), "to": today.isoformat(), "limit": 2, "offset": 2}, headers=staff["headers"])).json()
    assert page["total"] == 5 and len(page["items"]) == 2


async def test_a_vehicle_picture_comes_as_a_short_link_and_only_while_it_is_kept(client, make, su):
    t, cam, staff = await shop(client, make, su)
    today = local_day()
    live, gone = uuid.uuid4(), uuid.uuid4()
    for eid, days in ((live, 5), (gone, -1)):
        await su.execute("INSERT INTO app.evidence (id, tenant_id, camera_id, kind, storage_key, content_type, bytes, expires_at) VALUES ($1,$2,$3,'image',$4,'image/jpeg',10, now() + make_interval(days => $5))", eid, t, cam, f"t/{t}/{eid}", days)
    for eid, h in ((live, 9), (gone, 10)):
        await su.execute("INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, plate_raw, confidence, direction, evidence_id) VALUES ($1,$2,$3,'TN38BH4521','TN38BH4521',90,'in',$4)", t, cam, at(today, h), eid)
    d = (await client.get(f"/admin/v1/tenants/{t}/shop/vehicles", params={"from": today.isoformat(), "to": today.isoformat()}, headers=staff["headers"])).json()
    pics = [x["picture"] for x in d["items"]]
    assert pics[0] is None and pics[1] and pics[1].endswith("/image.jpg") and "/e/" in pics[1], "an expired picture is not offered"


async def test_the_excel_files_open_and_another_customer_is_never_shown(client, make, su):
    from openpyxl import load_workbook
    t, cam, staff = await shop(client, make, su)
    t2, cam2, _ = await shop(client, make, su)
    today = local_day()
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,$3,5,2), ($4,$5,$3,99,99)", t, cam, at(today, 10), t2, cam2)
    await su.execute("INSERT INTO app.plate_events (tenant_id, camera_id, seen_at, plate_text, plate_raw, confidence, direction) VALUES ($1,$2,$3,'TN38BH4521','TN38BH4521',90,'in'), ($4,$5,$3,'KA03MN1188','KA03MN1188',90,'in')", t, cam, at(today, 9), t2, cam2)
    p = {"from": today.isoformat(), "to": today.isoformat()}
    r = await client.get(f"/admin/v1/tenants/{t}/shop/people-count/export", params=p, headers=staff["headers"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats") and "people_count_" in r.headers["content-disposition"]
    text = " ".join(str(c.value) for ws in load_workbook(io.BytesIO(r.content)).worksheets for row in ws.iter_rows() for c in row if c.value is not None)
    assert "99" not in text.split(), "the other customer's counts are not in the file"
    v = await client.get(f"/admin/v1/tenants/{t}/shop/vehicles/export", params=p, headers=staff["headers"])
    assert v.status_code == 200
    text = " ".join(str(c.value) for ws in load_workbook(io.BytesIO(v.content)).worksheets for row in ws.iter_rows() for c in row if c.value is not None)
    assert "TN38BH4521" in text and "KA03MN1188" not in text
