"""The Jas iVision Control Center: four staff roles, the service end date, and setting up a shop (hours, people, events, areas) from the staff side."""
import uuid
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
    r = await client.post("/admin/v1/tenants", json={"owner_name": "Owner", "owner_phones": ["9876500007"], "city": "Salem", "name": "Lakshmi Jewellers", "plan_id": "full", "ends_on": "2027-06-13"}, headers=onboarding["headers"])
    assert r.status_code == 201 and r.json()["ends_on"] == "2027-06-13"
    tid = r.json()["id"]
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=onboarding["headers"])).status_code == 403
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=support["headers"])).status_code == 403
    assert (await client.patch(f"/admin/v1/tenants/{tid}", json={"ends_on": "2028-01-01"}, headers=admin["headers"])).json()["ends_on"] == "2028-01-01"
    assert (await client.post(f"/admin/v1/tenants/{tid}/suspend", json={"reason": "Asked to pause"}, headers=support["headers"])).json()["status"] == "suspended"
    assert (await client.post(f"/admin/v1/tenants/{tid}/reactivate", json={"reason": "Back again"}, headers=support["headers"])).json()["status"] == "active"
    assert (await client.post("/admin/v1/tenants", json={"owner_name": "Owner", "owner_phones": ["9876500008"], "city": "Salem", "name": "Not Allowed", "plan_id": "full"}, headers=support["headers"])).status_code == 403


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


async def test_the_counting_line_and_the_plate_area_are_marked_from_the_control_center(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", anpr=1)
    staff, support = await make.staff("onboarding"), await make.staff("support")
    base = f"/admin/v1/tenants/{t}/shop/cameras/{cam}/zones"
    assert (await client.get(base, headers=support["headers"])).json() == []
    line = {"kind": "count_line", "points": [[0.2, 0.5], [0.8, 0.5]], "in_direction": [0.0, 1.0]}
    r = await client.put(base, json={"zones": [line]}, headers=staff["headers"])
    assert r.status_code == 200 and [z["kind"] for z in r.json()] == ["count_line"]
    both = await client.put(base, json={"zones": [line, {"kind": "anpr_zone", "points": [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6]]}]}, headers=staff["headers"])
    assert [z["kind"] for z in both.json()] == ["anpr_zone", "count_line"] or {z["kind"] for z in both.json()} == {"count_line", "anpr_zone"}
    assert (await client.put(base, json={"zones": [{"kind": "count_line", "points": [[0.2, 0.5], [0.4, 0.5], [0.8, 0.5]]}]}, headers=staff["headers"])).status_code == 422
    assert (await client.put(base, json={"zones": [line]}, headers=support["headers"])).status_code == 403
    cfg = (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()
    assert {z["kind"] for z in cfg["zones"]} == {"count_line", "anpr_zone"}                                           # the worker is told about the line and the plate area


async def test_every_picture_taken_from_a_camera_is_logged_with_how_long_it_took(caplog, monkeypatch):
    import logging, uuid
    from app.errors import ApiError
    from app.services import snapshot
    async def source(row, decrypt):
        return "x", None
    async def ok(url, fmt):
        return b"\xff\xd8jpeg"
    async def bad(url, fmt):
        raise ApiError(504, "snapshot_timeout", "too slow")
    monkeypatch.setattr(snapshot, "source_for", source)
    cam = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="gateinsight.snapshot"):
        monkeypatch.setattr(snapshot, "grab", ok)
        await snapshot.get(cam, {}, lambda: "")
        monkeypatch.setattr(snapshot, "grab", bad)
        try:
            await snapshot.get(uuid.uuid4(), {}, lambda: "")
        except ApiError:
            pass
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith(f"snapshot {cam} took ") and m.endswith(" s") for m in msgs)
    assert any("failed after" in m and "snapshot_timeout" in m for m in msgs)


async def test_the_message_log_can_be_read_for_a_range_of_days_and_searched(client, make, su):
    t = await make.tenant(name="Kavin Super Market")
    staff, support = await make.staff("support"), await make.staff("support")
    rows = [("+919876543210", "sent", "0 days"), ("+919876543211", "failed", "2 days"), ("+919876543212", "suppressed", "10 days"), ("+919876543213", "queued", "40 days")]
    for phone, status, ago in rows:
        await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status, created_at, error) VALUES ($1,'whatsapp',$2,$3::app.delivery_status, now() - $4::interval, $5)", t, phone, status, timedelta(days=int(ago.split()[0])), "No webhook" if status == "suppressed" else None)
    get = lambda **p: client.get("/admin/v1/messaging/log", params={"tenant_id": str(t), **p}, headers=staff["headers"])
    assert (await get()).json()["total"] == 4
    last7 = (await get(**{"from": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")})).json()
    assert last7["total"] == 2 and [m["status"] for m in last7["items"]] == ["sent", "failed"]                      # newest first
    window = (await get(**{"from": (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d"), "to": (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")})).json()
    assert window["total"] == 1 and window["items"][0]["status"] == "suppressed"
    assert (await get(group="Held back")).json()["total"] == 1 and (await get(group="Waiting")).json()["total"] == 1 and (await get(group="Sent")).json()["total"] == 1
    assert (await get(q="webhook")).json()["total"] == 1 and (await get(q="Kavin")).json()["total"] == 4 and (await get(q="9876543212")).json()["total"] == 1
    assert (await get(q="100%")).json()["total"] == 0                                                              # a % in the search is a plain character, not "everything"
    page = (await get(limit=2, offset=2)).json()
    assert page["total"] == 4 and len(page["items"]) == 2


async def test_one_setting_says_how_long_everything_is_kept_and_applies_to_every_customer(client, make, su):
    owner, admin, support = await make.staff("owner"), await make.staff("admin"), await make.staff("support")
    a, b = await make.tenant(), await make.tenant()
    assert (await client.get("/admin/v1/maintenance/keep", headers=owner["headers"])).json()["days"] == 30
    r = await client.put("/admin/v1/maintenance/keep", json={"days": 90}, headers=admin["headers"])
    assert r.status_code == 200 and r.json()["days"] == 90
    for t in (a, b):
        row = await su.fetchrow("SELECT retention_events_days, retention_snapshots_days FROM app.tenants WHERE id=$1", t)
        assert (row["retention_events_days"], row["retention_snapshots_days"]) == (90, 90)
    new = (await client.post("/admin/v1/tenants", json={"owner_name": "Owner", "owner_phones": ["9876500009"], "city": "Salem", "name": "Fresh Shop", "plan_id": "full"}, headers=owner["headers"])).json()["id"]
    assert await su.fetchval("SELECT retention_events_days FROM app.tenants WHERE id=$1", uuid.UUID(new)) == 90         # a customer added later starts with the same
    assert (await client.put("/admin/v1/maintenance/keep", json={"days": 3}, headers=owner["headers"])).status_code == 422       # not shorter than a week
    assert (await client.put("/admin/v1/maintenance/keep", json={"days": 30}, headers=support["headers"])).status_code == 403
    await su.execute("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, created_at) VALUES ($1,'camera_offline','warning','Old','x', now() - interval '40 days'), ($1,'camera_offline','warning','New','x', now())", a)
    p = (await client.get("/admin/v1/maintenance/keep/preview", params={"days": 30}, headers=owner["headers"])).json()
    assert p["alerts"] >= 1 and set(p) == {"alerts", "messages", "people_counts", "plate_reads", "pictures_and_clips"}
    await client.put("/admin/v1/maintenance/keep", json={"days": 30}, headers=owner["headers"])


async def test_an_alert_that_is_still_being_sent_is_never_cleaned_up(client, make, su):
    from app.services import retention
    t = await make.tenant()
    await su.execute("UPDATE app.tenants SET retention_events_days=7 WHERE id=$1", t)
    old = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, created_at) VALUES ($1,'camera_offline','warning','Old','x', now() - interval '20 days') RETURNING id", t)
    waiting = await su.fetchval("INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, created_at) VALUES ($1,'camera_offline','warning','Waiting','x', now() - interval '20 days') RETURNING id", t)
    await su.execute("INSERT INTO app.notifications (tenant_id, alert_id, channel, recipient, status) VALUES ($1,$2,'whatsapp','+919876543210','queued')", t, waiting)
    import time
    await retention.purge_alerts(time.monotonic() + 30)
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE id=$1", old) == 0
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE id=$1", waiting) == 1


async def test_staff_can_rename_a_recorder_change_its_id_get_a_new_password_and_delete_it_with_its_events(client, make, su):
    t, u, (cam,), rec = await setup_cameras(client, make, plan="full", anpr=1)
    owner, support = await make.staff("owner"), await make.staff("support")
    rid = await su.fetchval("SELECT id FROM app.recorders WHERE tenant_id=$1", t)
    old = await su.fetchval("SELECT device_id FROM app.recorders WHERE id=$1", rid)
    url = f"/admin/v1/recorders/{rid}"
    assert (await client.patch(url, json={"name": "Godown"}, headers=support["headers"])).status_code == 403
    r = await client.patch(url, json={"name": "  Godown  "}, headers=owner["headers"])
    assert r.status_code == 200 and r.json()["name"] == "Godown" and r.json()["id_changed"] is False
    assert (await client.patch(url, json={"device_id": "ab"}, headers=owner["headers"])).status_code == 422
    assert (await client.patch(url, json={"device_id": "has space"}, headers=owner["headers"])).status_code == 422
    other = await su.fetchval("SELECT device_id FROM app.recorders WHERE id<>$1 LIMIT 1", rid)
    if other:
        assert (await client.patch(url, json={"device_id": other}, headers=owner["headers"])).status_code == 409
    from app.crypto import vault
    key = vault().decrypt(bytes(await su.fetchval("SELECT key_ciphertext FROM app.recorders WHERE id=$1", rid)), old)
    r = await client.patch(url, json={"device_id": "GODOWN-01"}, headers=owner["headers"])
    assert r.status_code == 200 and r.json()["id_changed"] is True
    row = await su.fetchrow("SELECT device_id, status::text AS status, key_ciphertext FROM app.recorders WHERE id=$1", rid)
    assert row["device_id"] == "GODOWN-01" and row["status"] == "never"
    assert vault().decrypt(bytes(row["key_ciphertext"]), "GODOWN-01") == key, "the same password still works under the new id"
    p = await client.post(f"{url}/new-password", headers=owner["headers"])
    assert p.status_code == 200 and p.json()["key"] and p.headers["cache-control"] == "no-store"
    assert (await client.post(f"{url}/new-password", headers=support["headers"])).status_code == 403
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action IN ('recorder.edit','recorder.new_password') AND target_id=$1", str(rid)) == 3
    assert p.json()["key"] not in str(await su.fetch("SELECT after FROM platform.audit_log WHERE target_id=$1", str(rid)))
    # events: a plate event has no camera left to watch once this recorder is gone
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params) VALUES ($1,'blocked_plate',true,'{}'), ($1,'camera_offline',true,'{}')", t)
    pre = (await client.get(f"{url}/delete-preview", headers=owner["headers"])).json()
    assert pre["name"] == "Godown" and pre["cameras"] >= 1 and [e["type"] for e in pre["events"]] == ["blocked_plate"]
    assert (await client.delete(url, headers=support["headers"])).status_code == 403
    assert (await client.delete(url, headers=owner["headers"])).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM app.recorders WHERE id=$1", rid) == 0
    assert await su.fetchval("SELECT count(*) FROM app.cameras WHERE id=$1", cam) == 0
    assert [r["rule_type"] for r in await su.fetch("SELECT rule_type FROM app.alert_rules WHERE tenant_id=$1", t)] == ["camera_offline"]
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='recorder.delete' AND target_id=$1", str(rid)) == 1


async def test_a_customer_needs_an_owner_and_an_owner_number_belongs_to_one_customer_only(client, make, su):
    onboarding, owner, support = await make.staff("onboarding"), await make.staff("owner"), await make.staff("support")
    body = {"name": "Sri Textiles", "city": "Erode", "plan_id": "full", "owner_name": "Mr Rao", "owner_phones": ["98765 43210", "09123456789"], "owner_email": "rao@example.com"}
    for missing in ("owner_name", "owner_phones", "city"):
        assert (await client.post("/admin/v1/tenants", json={k: v for k, v in body.items() if k != missing}, headers=onboarding["headers"])).status_code == 422
    assert (await client.post("/admin/v1/tenants", json={**body, "owner_phones": ["12345"]}, headers=onboarding["headers"])).status_code == 422
    assert (await client.post("/admin/v1/tenants", json={**body, "owner_phones": ["98765 43210", "+919876543210"]}, headers=onboarding["headers"])).status_code == 422      # the same number twice
    assert (await client.post("/admin/v1/tenants", json={**body, "owner_phones": list("1234")}, headers=onboarding["headers"])).status_code == 422                      # at most three
    r = await client.post("/admin/v1/tenants", json=body, headers=onboarding["headers"])
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["owner_name"] == "Mr Rao" and t["owner_phones"] == ["+919876543210", "+919123456789"] and t["owner_email"] == "rao@example.com"
    clash = await client.post("/admin/v1/tenants", json={**body, "name": "Other Shop", "owner_phones": ["+91 98765 43210"]}, headers=onboarding["headers"])
    assert clash.status_code == 409 and "Sri Textiles" in clash.json()["error"]["message"] if "error" in clash.json() else "Sri Textiles" in clash.text
    assert await su.fetchval("SELECT count(*) FROM app.tenants WHERE name='Other Shop'") == 0, "nothing half-made"
    # a person who gets messages may share a number: only owner numbers are unique
    # editing
    url = f"/admin/v1/tenants/{t['id']}"
    assert (await client.patch(url, json={"name": "Sri Textiles Pvt"}, headers=support["headers"])).status_code == 403
    assert (await client.patch(url, json={"ends_on": "2027-01-01"}, headers=onboarding["headers"])).status_code == 403
    e = await client.patch(url, json={"name": "Sri Textiles Pvt", "city": "Salem", "owner_name": "Mrs Rao", "owner_phones": ["9988776655"], "owner_email": ""}, headers=onboarding["headers"])
    assert e.status_code == 200, e.text
    assert e.json()["name"] == "Sri Textiles Pvt" and e.json()["owner_phones"] == ["+919988776655"] and e.json()["owner_email"] is None
    other = (await client.post("/admin/v1/tenants", json={**body, "name": "Third", "owner_phones": ["9000000001"]}, headers=onboarding["headers"])).json()
    taken = await client.patch(f"/admin/v1/tenants/{other['id']}", json={"owner_phones": ["9988776655"]}, headers=owner["headers"])
    assert taken.status_code == 409 and "Sri Textiles Pvt" in taken.text
    assert (await client.patch(f"/admin/v1/tenants/{t['id']}", json={"owner_phones": ["98765 43210"]}, headers=owner["headers"])).status_code == 200      # a number that was released can be used again
    audit = str(await su.fetch("SELECT before, after FROM platform.audit_log WHERE action='tenant.update' AND target_id=$1", t["id"]))
    assert "9988776655" not in audit and "6655" in audit, "the activity log keeps only the last four digits"
    assert (await client.delete(url, params={"reason": "Test customer"}, headers=owner["headers"])).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM platform.tenant_owner_phones WHERE tenant_id=$1", uuid.UUID(t["id"])) == 0
