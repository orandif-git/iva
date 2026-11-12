"""Marked areas: people in an area, a queue, possible phone use in a staff area. The limits live on the area; an alert is raised only when its condition is met."""
import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.messaging import delivery
from app.services import alert_catalog as cat, detectors
from tests.helpers import setup_cameras
from tests.test_alert_delivery import JPEG, bsp_server, customer, smtp_server, world  # noqa: F401  (fixtures)

SQUARE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]


def area(kind="area_count", name="Billing counter", **params):
    base = {"area_count": {"limit": 5, "minutes": 2}, "queue_area": {"length": 4, "minutes": 2, "wait_minutes": 0}, "staff_area": {"seconds": 20, "notice_confirmed": True}}[kind]
    return {"kind": kind, "name": name, "points": SQUARE, "params": {**base, **params}}


async def shop(client, make, su, plan="full", anpr=1):
    t, u, (foot,), _ = await setup_cameras(client, make, plan=plan, anpr=anpr)
    return t, u, foot


async def draw(client, u, cam, *areas):
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": list(areas)}, headers=u["headers"])
    assert r.status_code == 200, r.text
    return r.json()


async def report(client, make, cam, zone, rows, now=None):
    """rows: [(seconds_ago, people, wait, phone, evidence_id)]. Send the same `now` again to send the same reports again."""
    now = now or datetime.now(timezone.utc)
    body = {"events": [{"camera_id": cam, "zone_id": zone, "at": (now - timedelta(seconds=s)).isoformat(), "people": p, "wait_seconds": w, "phone_seconds": ph, "evidence_id": ev} for s, p, w, ph, ev in rows]}
    r = await client.post("/internal/v1/events/zones", json=body, headers=make.service())
    assert r.status_code == 200, r.text
    return r.json()


def steady(people, seconds=130, step=10):
    return [(s, people, None, None, None) for s in range(0, seconds + 1, step)]


async def alerts_of(su, t, rule):
    return await su.fetch("SELECT title, body, details::text AS details, evidence_id FROM app.alerts WHERE tenant_id=$1 AND rule_type=$2 ORDER BY created_at", t, rule)


async def turn_on(client, u, rule):
    r = await client.put(f"/v1/alert-types/{rule}", json={"enabled": True}, headers=u["headers"])
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------------------ the catalogue and the launch
def test_the_three_area_alerts_are_smart_alerts_that_are_built():
    for k, kind in (("people_in_area", "area_count"), ("queue_long", "queue_area"), ("phone_use", "staff_area")):
        assert cat.CATALOG[k]["family"] == "smart" and cat.CATALOG[k]["built"] and cat.AREA_ALERTS[kind] == k
    assert "image_url" in cat.shortcodes_for("phone_use") and {"area", "people", "limit", "minutes"} <= set(cat.shortcodes_for("people_in_area"))
    assert {"area", "length", "wait_minutes"} <= set(cat.shortcodes_for("queue_long")) and "image_url" in cat.shortcodes_for("queue_long") and "image_url" in cat.shortcodes_for("people_in_area")
    assert len(cat.LISTED) == 24 and len(cat.BUILT) == 24


def test_a_new_database_starts_with_the_area_alerts_switched_off():
    schema = (pathlib.Path(__file__).resolve().parents[2] / "schema.sql").read_text()
    assert "('people_in_area', false), ('queue_long', false), ('phone_use', false)" in schema
    mig = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "010_area_alerts.sql").read_text()
    assert "('people_in_area', false), ('queue_long', false), ('phone_use', false)" in mig


async def test_a_customer_can_draw_only_the_kinds_of_area_they_have(client, make, su):
    t, u, cam = await shop(client, make, su)
    kinds = (await client.get("/v1/area-kinds", headers=u["headers"])).json()
    assert {k["kind"] for k in kinds} == {"area_count", "queue_area", "staff_area"} and kinds[0]["settings"]
    owner = await make.staff("owner")
    await client.put("/admin/v1/alert-catalog/phone_use", json={"enabled": False, "reason": "not ready yet"}, headers=owner["headers"])
    try:
        assert {k["kind"] for k in (await client.get("/v1/area-kinds", headers=u["headers"])).json()} == {"area_count", "queue_area"}
        r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area("staff_area")]}, headers=u["headers"])
        assert r.status_code == 403 and r.json()["error"]["code"] == "not_available"
    finally:
        await client.put("/admin/v1/alert-catalog/phone_use", json={"enabled": True, "reason": "ready"}, headers=owner["headers"])
    # a plan without the alert does not offer the area either
    pid = "p" + uuid.uuid4().hex[:8]
    await su.execute("INSERT INTO platform.plans (id, name, price_per_camera_paise, stream_mode, features, alert_types) VALUES ($1,$1,1000,'snapshots','{}',$2)", pid, ["camera_offline", "people_in_area"])
    t2, u2, cam2 = await shop(client, make, su, plan=pid, anpr=0)
    assert [k["kind"] for k in (await client.get("/v1/area-kinds", headers=u2["headers"])).json()] == ["area_count"]


# ------------------------------------------------------------------------------------------ drawing and setting the limits
async def test_areas_are_saved_with_their_own_limits_and_defaults(client, make, su):
    t, u, cam = await shop(client, make, su)
    got = await draw(client, u, cam, area("area_count", "Counter", limit=8), area("queue_area", "Queue", length=6), area("staff_area", "Behind the counter"))
    by = {a["name"]: a for a in got}
    assert by["Counter"]["params"] == {"limit": 8, "minutes": 2, "picture_minutes": 15, "repeat_minutes": 15} and by["Counter"]["alert"] == "people_in_area"
    assert by["Queue"]["params"] == {"length": 6, "minutes": 2, "wait_minutes": 0, "staff": 0, "picture_minutes": 15, "repeat_minutes": 15}
    staff = by["Behind the counter"]["params"]
    assert staff["seconds"] == 20 and staff["notice_confirmed"] is True and staff["notice_confirmed_by"] == str(u["id"]) and staff["notice_confirmed_at"]
    assert await su.fetchval("SELECT use_areas FROM app.cameras WHERE id=$1", uuid.UUID(cam)) is True
    assert [a["name"] for a in (await client.get(f"/v1/cameras/{cam}/areas", headers=u["headers"])).json()] == ["Counter", "Queue", "Behind the counter"]


async def test_changing_an_area_keeps_its_history_and_leaving_it_out_removes_it(client, make, su):
    t, u, cam = await shop(client, make, su)
    a, q = await draw(client, u, cam, area("area_count", "Counter"), area("queue_area", "Queue"))
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area("area_count", "Counter", limit=9), "id": a["id"]}]}, headers=u["headers"])
    assert r.status_code == 200 and r.json()[0]["id"] == a["id"] and r.json()[0]["params"]["limit"] == 9 and len(r.json()) == 1
    assert await su.fetchval("SELECT count(*) FROM app.camera_zones WHERE id=$1", uuid.UUID(q["id"])) == 0
    staff = await draw(client, u, cam, area("staff_area", "Back office"))
    again = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area("staff_area", "Back office", seconds=40), "id": staff[0]["id"]}]}, headers=u["headers"])
    assert again.json()[0]["params"]["notice_confirmed_at"] == staff[0]["params"]["notice_confirmed_at"]            # confirmed once, the time is kept
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area("area_count"), "id": str(uuid.uuid4())}]}, headers=u["headers"])).status_code == 404


@pytest.mark.parametrize("bad,why", [
    ({"params": {"minutes": 2}}, "limit"), ({"params": {"limit": 0}}, "between"), ({"params": {"limit": 5000}}, "between"), ({"params": {"limit": "many"}}, "whole number"),
    ({"params": {"limit": 5, "colour": 1}}, "Unknown setting"), ({"params": {"limit": 5.5}}, "whole number"), ({"params": {"limit": True}}, "whole number"),
])
async def test_limits_are_checked(client, make, su, bad, why):
    t, u, cam = await shop(client, make, su)
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area("area_count"), **bad}]}, headers=u["headers"])
    assert r.status_code == 422 and why in r.json()["error"]["message"], r.text


async def test_the_shape_and_the_number_of_areas_are_checked(client, make, su):
    t, u, cam = await shop(client, make, su)
    h = u["headers"]
    for bad in ({"points": SQUARE[:2]}, {"points": [[0.1, 0.1], [1.5, 0.1], [0.5, 0.5]]}, {"name": "   "}, {"kind": "count_line"}):
        assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), **bad}]}, headers=h)).status_code == 422, bad
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area(name=f"A{i}") for i in range(7)]}, headers=h)).status_code == 422
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area(name=f"A{i}") for i in range(6)]}, headers=h)).status_code == 200


async def test_a_staff_area_needs_the_confirmation_that_staff_were_told(client, make, su):
    t, u, cam = await shop(client, make, su)
    for params in ({"seconds": 20}, {"seconds": 20, "notice_confirmed": False}):
        r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area("staff_area"), "params": params}]}, headers=u["headers"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "notice_required"


async def test_only_an_administrator_can_draw_and_a_staff_view_cannot(client, make, su):
    t, u, cam = await shop(client, make, su)
    reg = await make.user(t, "regular")
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area()]}, headers=reg["headers"])).status_code == 403
    assert (await client.get(f"/v1/cameras/{cam}/areas", headers=reg["headers"])).status_code == 200
    imp = await make.staff("support")
    tok = {"Authorization": "Bearer " + (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Checking the areas for the customer", "minutes": 15}, headers=imp["headers"])).json()["token"]}
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area()]}, headers=tok)).status_code == 403
    other, ou, ocam = await shop(client, make, su)
    assert (await client.get(f"/v1/cameras/{cam}/areas", headers=ou["headers"])).status_code == 404          # another customer's camera does not exist


async def test_the_older_counting_line_and_plate_zone_do_not_touch_the_areas(client, make, su):
    t, u, cam = await shop(client, make, su)
    await draw(client, u, cam, area("area_count", "Counter"))
    r = await client.put(f"/v1/cameras/{cam}/zones", json={"zones": [{"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}]}, headers=u["headers"])
    assert r.status_code == 200 and [z["kind"] for z in r.json()] == ["count_line"]
    assert [a["name"] for a in (await client.get(f"/v1/cameras/{cam}/areas", headers=u["headers"])).json()] == ["Counter"]
    assert [z["kind"] for z in (await client.get(f"/v1/cameras/{cam}/zones", headers=u["headers"])).json()] == ["count_line"]


async def test_the_last_area_cannot_be_removed_from_a_camera_that_is_used_for_nothing_else(client, make, su):
    t, u, cam = await shop(client, make, su)
    await draw(client, u, cam, area())
    await su.execute("UPDATE app.cameras SET use_footfall=false, use_anpr=false WHERE id=$1", uuid.UUID(cam))
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": []}, headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_needs_a_use"


# ------------------------------------------------------------------------------------------ what the worker is told
async def test_the_worker_is_told_about_the_areas_the_customer_has_and_nothing_else(client, make, su):
    t, u, cam = await shop(client, make, su)
    await draw(client, u, cam, area("area_count", "Counter"), area("staff_area", "Behind the counter"))
    cfg = (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()
    assert cfg["use_areas"] is True and {a["name"] for a in cfg["areas"]} == {"Counter", "Behind the counter"} and cfg["areas"][0]["params"] and cfg["areas"][0]["points"]
    owner = await make.staff("owner")
    await client.put("/admin/v1/alert-catalog/phone_use", json={"enabled": False, "reason": "paused for now"}, headers=owner["headers"])
    try:
        cfg = (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()
        assert [a["name"] for a in cfg["areas"]] == ["Counter"]                                  # no phone analysis while the alert is off
    finally:
        await client.put("/admin/v1/alert-catalog/phone_use", json={"enabled": True, "reason": "back on"}, headers=owner["headers"])
    assert [z["kind"] for z in (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["zones"]] == []         # areas are not mixed into the old zones


# ------------------------------------------------------------------------------------------ reports from the worker
async def test_reports_are_stored_once_and_checked(client, make, su):
    t, u, cam = await shop(client, make, su)
    (a,) = await draw(client, u, cam, area())
    at = datetime.now(timezone.utc)
    res = await report(client, make, cam, a["id"], steady(2, 30), now=at)
    assert res["accepted"] == 4 and res["duplicates"] == 0
    again = await report(client, make, cam, a["id"], steady(2, 30), now=at)
    assert again["accepted"] == 4 and again["duplicates"] == 4
    assert await su.fetchval("SELECT count(*) FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"])) == 4
    other, ou, ocam = await shop(client, make, su)
    (oa,) = await draw(client, ou, ocam, area())
    bad = await report(client, make, cam, oa["id"], steady(2, 10))                              # an area of someone else's camera
    assert [r["reason"] for r in bad["rejected"]] == ["unknown_area"] * 2
    assert [r["reason"] for r in (await report(client, make, cam, str(uuid.uuid4()), steady(1, 10)))["rejected"]] == ["unknown_area"] * 2
    assert (await client.post("/internal/v1/events/zones", json={"events": []}, headers=make.service())).status_code == 422
    assert (await client.post("/internal/v1/events/zones", json={"events": [{"camera_id": cam, "zone_id": a["id"], "at": datetime.now(timezone.utc).isoformat(), "people": 1}]}, headers=u["headers"])).status_code == 401
    await su.execute("UPDATE app.cameras SET use_areas=false WHERE id=$1", uuid.UUID(cam))
    assert [r["reason"] for r in (await report(client, make, cam, a["id"], steady(1, 10)))["rejected"]][0] == "areas_not_enabled"


# ------------------------------------------------------------------------------------------ the conditions
async def test_people_in_an_area_raise_an_alert_only_when_the_count_holds_at_the_limit_for_the_time_set(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "people_in_area")
    (a,) = await draw(client, u, cam, area("area_count", "Billing counter", limit=5, minutes=2))
    await report(client, make, cam, a["id"], steady(7, 30))                                       # over the limit, but only for thirty seconds
    assert await alerts_of(su, t, "people_in_area") == []
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"]))
    gone = steady(7)
    for i in range(3, 9):
        gone[i] = (gone[i][0], 3, None, None, None)                                              # under the limit for half of the two minutes: the crowd really left for a while
    await report(client, make, cam, a["id"], gone)
    assert await alerts_of(su, t, "people_in_area") == []
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"]))
    last = steady(7); last[0] = (last[0][0], 3, None, None, None)                                # over the limit until just now, but the newest report is under it
    await report(client, make, cam, a["id"], last)
    assert await alerts_of(su, t, "people_in_area") == []
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"]))
    dip = steady(7); dip[6] = (dip[6][0], 3, None, None, None)                                   # one low report in the two minutes: someone was hidden behind someone else
    await report(client, make, cam, a["id"], dip)
    assert len(await alerts_of(su, t, "people_in_area")) == 1, "one missed reading does not start the clock again"
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"]))
    await report(client, make, cam, a["id"], steady(7))                                           # at or above five for the whole two minutes
    (alert,) = await alerts_of(su, t, "people_in_area")
    assert alert["title"] == "Occupancy limit exceeded in Billing counter" and "Your limit is 5" in alert["body"]
    d = json.loads(alert["details"])
    assert d["area"] == "Billing counter" and d["people"] == 7 and d["limit"] == 5 and d["minutes"] == 2
    await report(client, make, cam, a["id"], [(0, 9, None, None, None)])
    assert len(await alerts_of(su, t, "people_in_area")) == 1                                     # not again inside the repeat time


async def test_an_alert_needs_the_customer_to_have_turned_it_on_and_each_area_has_its_own_limit(client, make, su):
    t, u, cam = await shop(client, make, su)
    small, big = await draw(client, u, cam, area("area_count", "Counter", limit=3), area("area_count", "Hall", limit=20))
    await report(client, make, cam, small["id"], steady(5)); await report(client, make, cam, big["id"], steady(5))
    assert await alerts_of(su, t, "people_in_area") == []                                          # Smart Alerts are off until the customer chooses them
    await turn_on(client, u, "people_in_area")
    await report(client, make, cam, small["id"], [(0, 5, None, None, None)]); await report(client, make, cam, big["id"], [(0, 5, None, None, None)])
    titles = [r["title"] for r in await alerts_of(su, t, "people_in_area")]
    assert titles == ["Occupancy limit exceeded in Counter"]                                                # five people is too many at the counter, not in the hall


async def test_a_queue_alerts_on_its_length_held_for_the_time_or_on_how_long_someone_waited(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "queue_long")
    qlen, qwait, qoff = await draw(client, u, cam, area("queue_area", "Front queue", length=4, minutes=2), area("queue_area", "Pharmacy queue", length=50, minutes=2, wait_minutes=5),
                                   area("queue_area", "Lab queue", length=50, minutes=2, wait_minutes=0))
    await report(client, make, cam, qlen["id"], steady(5))
    (a,) = await alerts_of(su, t, "queue_long")
    assert a["title"] == "Long queue at Front queue" and json.loads(a["details"])["length"] == 5
    await report(client, make, cam, qwait["id"], [(5, 2, 200.0, None, None), (0, 2, 330.0, None, None)])           # 5.5 minutes
    assert [r["title"] for r in await alerts_of(su, t, "queue_long")][-1] == "Long queue at Pharmacy queue"
    assert "waited 5 minutes" in (await alerts_of(su, t, "queue_long"))[-1]["body"]
    await report(client, make, cam, qoff["id"], [(5, 2, 9000.0, None, None), (0, 2, 9000.0, None, None)])          # waiting time is off for this one
    assert len(await alerts_of(su, t, "queue_long")) == 2


async def test_possible_phone_use_needs_the_time_set_and_carries_the_picture(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "phone_use")
    (a,) = await draw(client, u, cam, area("staff_area", "Behind the counter", seconds=20))
    await report(client, make, cam, a["id"], [(8, 1, None, 8.0, None), (0, 1, None, 15.0, None)])                  # a phone, but not for long enough
    assert await alerts_of(su, t, "phone_use") == []
    eid = (await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers={**make.service(), "Content-Type": "image/jpeg"})).json()["evidence_id"]
    await report(client, make, cam, a["id"], [(0, 1, None, 24.0, eid)])
    (alert,) = await alerts_of(su, t, "phone_use")
    assert alert["title"] == "Possible phone use in Behind the counter" and "possible" in alert["body"].lower() and str(alert["evidence_id"]) == eid
    assert json.loads(alert["details"]) | {"x": 1} and json.loads(alert["details"])["area"] == "Behind the counter"
    assert "person" not in alert["body"].lower().replace("possible", "")                         # it names the area, never a person


async def test_a_picture_from_another_customer_is_never_attached(client, make, su):
    t, u, cam = await shop(client, make, su)
    t2, u2, cam2 = await shop(client, make, su)
    await turn_on(client, u, "phone_use")
    (a,) = await draw(client, u, cam, area("staff_area", "Counter", seconds=10))
    foreign = (await client.post("/internal/v1/evidence", params={"camera_id": cam2}, content=JPEG, headers={**make.service(), "Content-Type": "image/jpeg"})).json()["evidence_id"]
    await report(client, make, cam, a["id"], [(0, 1, None, 30.0, foreign)])
    (alert,) = await alerts_of(su, t, "phone_use")
    assert alert["evidence_id"] is None


async def test_old_reports_are_cleared_away(client, make, su):
    t, u, cam = await shop(client, make, su)
    (a,) = await draw(client, u, cam, area())
    await su.execute("INSERT INTO app.zone_samples (zone_id, tenant_id, at, people) VALUES ($1,$2, now() - interval '4 days', 3), ($1,$2, now() - interval '1 hour', 3)", uuid.UUID(a["id"]), t)
    await detectors.connection_unstable()
    assert await su.fetchval("SELECT count(*) FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"])) == 1


# ------------------------------------------------------------------------------------------ the event reaches the webhook only when the condition is met
async def test_the_webhook_gets_an_event_only_when_the_condition_is_met(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="people_in_area")
    ep = next(e for e in (await client.get("/admin/v1/webhooks", headers=world["admin"])).json()["items"] if e["alert_type"] == "people_in_area")
    assert ep["event"] == "smart.people_in_area" and {"area", "people", "limit", "minutes"} <= set(ep["shortcodes"])
    await client.put(f"/admin/v1/webhooks/{ep['id']}", json={"payload_template": '{"to": "{to_phone}", "area": "{area}", "people": "{people}", "limit": "{limit}", "minutes": "{minutes}"}'}, headers=world["admin"])
    (a,) = await draw(client, u, cam, area("area_count", "Billing counter", limit=5, minutes=2))
    await report(client, make, cam, a["id"], steady(4))                                            # under the limit
    assert await delivery.run_once() == 0 and world["hook"].calls == []
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(a["id"]))
    await report(client, make, cam, a["id"], steady(6))
    assert await delivery.run_once() == 1
    call = world["hook"].calls[-1]
    assert call["headers"]["x-event"] == "smart.people_in_area" and call["body"] == {"to": "919876543210", "area": "Billing counter", "people": "6", "limit": "5", "minutes": "2"}
