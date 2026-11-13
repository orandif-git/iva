"""The Jas iVision Control Center: four staff roles, the service end date, and setting up a shop (hours, people, events, areas) from the staff side."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services import service_end
from app.services.opening_hours import Hours
from tests.helpers import setup_cameras

SQUARE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]


async def test_roles_admin_runs_the_team_but_cannot_make_owners_and_only_the_owner_has_the_whatsapp_connection(client, make):
    owner, admin, onboarding, support = [await make.staff(r) for r in ("owner", "admin", "onboarding", "support")]
    new = {"name": "Karthik", "email": "k@example.com", "role": "onboarding"}
    assert (await client.post("/admin/v1/staff", json=new, headers=admin["headers"])).status_code == 201              # an Admin adds people
    assert (await client.post("/admin/v1/staff", json={**new, "email": "o@example.com", "role": "owner"}, headers=admin["headers"])).status_code == 403
    assert (await client.post("/admin/v1/staff", json={**new, "email": "x@example.com", "role": "admin"}, headers=onboarding["headers"])).status_code == 403
    assert (await client.patch(f"/admin/v1/staff/{owner['id']}", json={"role": "support"}, headers=admin["headers"])).status_code == 403
    assert (await client.get("/admin/v1/summaries", headers=owner["headers"])).status_code == 200
    for who in (admin, onboarding, support):
        assert (await client.get("/admin/v1/summaries", headers=who["headers"])).status_code == 403                     # the WhatsApp connection is the Owner's alone
    me = await client.get("/admin/v1/me", headers=admin["headers"])
    assert me.json()["role"] == "admin"


async def test_onboarding_creates_customers_support_pauses_them_and_neither_can_change_the_end_date(client, make):
    onboarding, support, admin = await make.staff("onboarding"), await make.staff("support"), await make.staff("admin")
    r = await client.post("/admin/v1/tenants", json={"name": "Lakshmi Jewellers", "plan_id": "full", "ends_on": "2027-06-13"}, headers=onboarding["headers"])
    assert r.status_code == 201 and r.json()["ends_on"] == "2027-06-13"
    tid = r.json()["id"]
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=onboarding["headers"])).status_code == 403
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=support["headers"])).status_code == 403
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=admin["headers"])).json()["ends_on"] == "2028-01-01"
    assert (await client.post(f"/admin/v1/tenants/{tid}/suspend", json={"reason": "Asked to pause"}, headers=support["headers"])).json()["status"] == "suspended"
    assert (await client.post(f"/admin/v1/tenants/{tid}/reactivate", json={"reason": "Back again"}, headers=support["headers"])).json()["status"] == "active"
    assert (await client.post("/admin/v1/tenants", json={"name": "Not Allowed", "plan_id": "full"}, headers=support["headers"])).status_code == 403


async def test_service_stops_the_day_after_the_end_date_and_the_shop_is_told_once(client, make, su):
    t = await make.tenant()
    u = await make.user(t)
    await su.execute("INSERT INTO app.recipients (tenant_id, name, phone, consent_confirmed_at) VALUES ($1,'Ramesh','+919876543210', now())", t)
    other = await make.tenant()
    await su.execute("UPDATE app.tenants SET ends_on=$2 WHERE id=$1", t, date.today() - timedelta(days=1))
    await su.execute("UPDATE app.tenants SET ends_on=$2 WHERE id=$1", other, date.today() + timedelta(days=30))
    assert await service_end.run() >= 1
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", t) == "suspended"
    assert await su.fetchval("SELECT status::text FROM app.tenants WHERE id=$1", other) == "active"
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='service_ended'", t) == 1
    assert (await client.get("/v1/me", headers=u["headers"])).json()["error"]["code"] == "tenant_suspended"
    assert await service_end.run() == 0                                                                              # nothing more to do, and no second message


def test_hours_can_differ_by_day():
    per = tuple((time(9), time(21)) if d < 5 else ((time(10), time(18)) if d == 5 else None) for d in range(7))
    h = Hours(time(9), time(21), (6,), per)
    mon, sat, sun = datetime(2026, 11, 9), datetime(2026, 11, 14), datetime(2026, 11, 15)
    assert h.is_open(mon.replace(hour=20)) and not h.is_open(mon.replace(hour=22))
    assert h.is_open(sat.replace(hour=10, minute=30)) and not h.is_open(sat.replace(hour=19))                        # Saturday closes at 6 pm, not 9 pm
    assert not h.is_open(sun.replace(hour=12))


async def test_set_up_a_shop_from_the_control_center(client, make, su):
    t, u, (cam,), _ = await setup_cameras(client, make, plan="full", anpr=1)
    onboarding, support = await make.staff("onboarding"), await make.staff("support")
    base = f"/admin/v1/tenants/{t}/shop"
    days = [{"open": d < 6, "from": "09:00" if d < 5 else "10:00", "to": "21:00" if d < 5 else "18:00"} for d in range(7)]
    r = await client.put(f"{base}/hours", json={"always_open": False, "days": days}, headers=onboarding["headers"])
    assert r.status_code == 200 and r.json()["days"][5] == {"open": True, "from": "10:00", "to": "18:00"} and r.json()["days"][6]["open"] is False
    row = await su.fetchrow("SELECT open_time, close_time, closed_days FROM app.tenants WHERE id=$1", t)
    assert row["open_time"] == time(9) and list(row["closed_days"]) == [6]                                           # the customer's own app still reads the hours
    assert (await client.get("/v1/opening-hours", headers=u["headers"])).json()["set"] is True
    assert (await client.put(f"{base}/hours", json={"always_open": False, "days": days[:3]}, headers=onboarding["headers"])).status_code == 422

    p = await client.post(f"{base}/people", json={"name": "Ramesh", "phone": "98765 43210"}, headers=onboarding["headers"])
    assert p.status_code == 201 and p.json()["phone"] == "+919876543210" and p.json()["consent_confirmed_at"]
    pid = p.json()["id"]
    assert [x["name"] for x in (await client.get(f"{base}/people", headers=support["headers"])).json()] == ["Ramesh"]

    ev = await client.get(f"{base}/events", headers=onboarding["headers"])
    assert ev.status_code == 200 and any(e["type"] == "queue_long" for e in ev.json())
    r = await client.put(f"{base}/events/camera_offline", json={"label": "Camera down", "enabled": True, "recipient_ids": [pid]}, headers=onboarding["headers"])
    assert r.status_code == 200 and r.json()["label"] == "Camera down" and r.json()["recipient_ids"] == [pid]
    assert (await client.put(f"{base}/events/camera_offline", json={"enabled": False}, headers=support["headers"])).status_code == 403   # Support helps with people and hours, not events
    saved = [e for e in (await client.get(f"{base}/events", headers=onboarding["headers"])).json() if e["type"] == "camera_offline"][0]
    assert saved["label"] == "Camera down" and saved["saved"] and saved["enabled"]

    area = {"kind": "area_count", "name": "Counter", "points": SQUARE, "params": {"limit": 5, "minutes": 2}}
    assert (await client.put(f"{base}/cameras/{cam}/areas", json={"areas": [area]}, headers=onboarding["headers"])).status_code == 200
    assert len((await client.get(f"{base}/cameras/{cam}/areas", headers=support["headers"])).json()) == 1
    assert (await client.put(f"{base}/cameras/{cam}/areas", json={"areas": []}, headers=support["headers"])).status_code == 403

    # What was done is on record, with who did it.
    who = await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE tenant_id=$1 AND actor_role='staff:onboarding'", t)
    assert who >= 4


async def test_one_shop_cannot_see_another_shops_people(client, make):
    a, b = await make.tenant(), await make.tenant()
    onboarding = await make.staff("onboarding")
    await client.post(f"/admin/v1/tenants/{a}/shop/people", json={"name": "Ramesh", "phone": "98765 43210"}, headers=onboarding["headers"])
    assert (await client.get(f"/admin/v1/tenants/{b}/shop/people", headers=onboarding["headers"])).json() == []
    assert (await client.get(f"/admin/v1/tenants/00000000-0000-0000-0000-000000000000/shop/people", headers=onboarding["headers"])).status_code == 404


async def test_channels_cameras_snapshot_and_deleting_a_customer(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", anpr=1)
    owner, admin, onboarding = await make.staff("owner"), await make.staff("admin"), await make.staff("onboarding")
    base = f"/admin/v1/tenants/{t}/shop"
    cams = await client.get(f"{base}/cameras", headers=onboarding["headers"])
    assert cams.status_code == 200 and len(cams.json()) >= 1
    assert (await client.patch(f"{base}/cameras/{cam}", json={"name": "Counter camera"}, headers=onboarding["headers"])).json()["name"] == "Counter camera"
    n = await make.tenant()
    url = f"/admin/v1/tenants/{n}"
    assert (await client.delete(url, params={"reason": "Test customer"}, headers=onboarding["headers"])).status_code == 403
    assert (await client.delete(url, params={"reason": "Test customer"}, headers=owner["headers"])).status_code == 204
    assert (await client.get(url, headers=owner["headers"])).status_code == 404
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='tenant.delete' AND target_id=$1", str(n)) == 1


async def test_every_customer_gets_everything_plates_move_the_shop_to_a_full_plan(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="quick", anpr=0)
    onboarding = await make.staff("onboarding")
    base = f"/admin/v1/tenants/{t}/shop"
    assert await su.fetchval("SELECT plan_id FROM app.tenants WHERE id=$1", t) == "quick"
    r = await client.patch(f"{base}/cameras/{cam}", json={"use_anpr": True, "stream": "main"}, headers=onboarding["headers"])
    assert r.status_code == 200 and r.json()["use_anpr"] is True
    assert await su.fetchval("SELECT plan_id FROM app.tenants WHERE id=$1", t) == "full"
    rid = await su.fetchval("SELECT id FROM app.recorders WHERE tenant_id=$1", t)
    assert (await client.get(f"{base}/recorders/{rid}/channels/1/snapshot", headers=onboarding["headers"])).status_code in (409, 502, 503, 504)    # no real recorder here


async def test_deleting_an_event_stops_it_for_this_shop_only(client, make, su):
    a, b = await make.tenant(), await make.tenant()
    staff = await make.staff("onboarding")
    for t in (a, b):
        r = await client.get(f"/admin/v1/tenants/{t}/shop/events", headers=staff["headers"])
        assert {e["type"]: e["default"] for e in r.json()}["camera_offline"] is True and {e["type"]: e["default"] for e in r.json()}["crowding"] is False
    base = f"/admin/v1/tenants/{a}/shop/events"
    assert (await client.put(f"{base}/crowding", json={"label": "Busy", "enabled": True, "params": {"people": 40}}, headers=staff["headers"])).status_code == 200
    assert (await client.delete(f"{base}/crowding", headers=staff["headers"])).status_code == 204                      # not a default: the rule is removed
    assert await su.fetchval("SELECT count(*) FROM app.alert_rules WHERE tenant_id=$1 AND rule_type='crowding'", a) == 0
    assert (await client.delete(f"{base}/camera_offline", headers=staff["headers"])).status_code == 204               # a default: kept as switched off, so it does not come back
    mine = {e["type"]: e for e in (await client.get(base, headers=staff["headers"])).json()}
    other = {e["type"]: e for e in (await client.get(f"/admin/v1/tenants/{b}/shop/events", headers=staff["headers"])).json()}
    assert mine["camera_offline"]["enabled"] is False and mine["camera_offline"]["saved"] is True
    assert other["camera_offline"]["enabled"] is True                                                                  # another shop is not touched
    support = await make.staff("support")
    assert (await client.delete(f"{base}/crowding", headers=support["headers"])).status_code == 403
