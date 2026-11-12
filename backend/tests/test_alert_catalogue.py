import pytest
"""Which alerts exist, who has them, and who can change that. System Alerts and Smart Alerts, plans, per-customer extras, the platform switch."""
import json
import uuid

from app.db import db
from app.services import alert_catalog as cat
from app.services.alerts import raise_alert


async def plan(su, alerts, anpr=False):
    pid = "p" + uuid.uuid4().hex[:8]
    await su.execute("INSERT INTO platform.plans (id, name, price_per_camera_paise, stream_mode, features, alert_types) VALUES ($1,$1,1000,'snapshots',$2,$3)", pid, json.dumps({"anpr": anpr}), alerts)
    return pid


async def seen(client, user):
    r = await client.get("/v1/alert-types", headers=user["headers"])
    assert r.status_code == 200, r.text
    return {a["type"]: a for a in r.json()}


async def fire(tenant_id, rule_type="camera_offline"):
    async with db.service_tx() as conn:
        return await raise_alert(conn, tenant_id, rule_type, body="x", camera_name="Gate", dedupe_key=f"t:{uuid.uuid4()}")


# ------------------------------------------------------------------------------------------ the catalogue
def test_there_are_two_families_and_smart_alerts_are_marked_as_ai():
    fams = {c["family"] for c in cat.CATALOG.values()}
    assert fams == {"system", "smart"}
    assert len(cat.LISTED) == 20 and len(cat.CATALOG) == 22 and [k for k, v in cat.CATALOG.items() if v["internal"]] == ["report_ready", "platform_fault"]       # system events, not customer alerts
    assert {k for k in cat.BUILT} == {"recorder_offline", "camera_offline", "connection_unstable", "back_to_normal", "counting_stopped", "after_hours", "clock_wrong", "storage_full", "disk_fault", "recording_stopped", "view_changed", "picture_poor",
                                                                  "daily_summary", "blocked_plate", "vehicle_overstay", "crowding", "low_confidence_plate", "people_in_area", "queue_long", "phone_use"}
    assert cat.event_name("camera_offline") == "system.camera_offline" and cat.event_name("blocked_plate") == "smart.blocked_plate"
    assert cat.alert_for_event("smart.crowding") == "crowding" and cat.alert_for_event("system.nothing") is None
    assert "plate" in cat.shortcodes_for("blocked_plate") and "image_url" in cat.shortcodes_for("blocked_plate") and "clip_url" in cat.shortcodes_for("blocked_plate")
    assert "clip_url" not in cat.shortcodes_for("low_confidence_plate")


@pytest.fixture
def unbuilt(monkeypatch):
    """Every alert in the catalogue is built now, so the rule that an unbuilt alert cannot be used is tested by marking one as not built for the length of a test."""
    monkeypatch.setitem(cat.CATALOG["view_changed"], "built", False)
    monkeypatch.setattr(cat, "BUILT", [k for k in cat.BUILT if k != "view_changed"])
    monkeypatch.setattr(cat, "WEBHOOK_TYPES", [k for k in cat.WEBHOOK_TYPES if k != "view_changed"])


async def test_staff_see_the_catalogue_with_what_is_built(client, make, unbuilt):
    r = await client.get("/admin/v1/alert-catalog", headers=(await make.staff("support"))["headers"])
    assert r.status_code == 200
    items = {i["type"]: i for i in r.json()["items"]}
    assert len(items) == len(cat.LISTED) and "report_ready" not in items and items["blocked_plate"]["ai"] is True and items["camera_offline"]["ai"] is False
    assert items["view_changed"]["built"] is False and items["view_changed"]["enabled"] is False
    assert items["camera_offline"]["enabled"] is True and items["camera_offline"]["event"] == "system.camera_offline"
    assert (await client.get("/admin/v1/alert-catalog")).status_code == 401


# ------------------------------------------------------------------------------------------ the platform switch
async def test_only_the_owner_switches_an_alert_and_needs_a_reason_which_is_audited(client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    body = {"enabled": False, "reason": "Paused while we fix messages"}
    assert (await client.put("/admin/v1/alert-catalog/crowding", json=body, headers=support["headers"])).status_code == 403
    assert (await client.put("/admin/v1/alert-catalog/crowding", json={"enabled": False, "reason": "no"}, headers=owner["headers"])).status_code == 422
    assert (await client.put("/admin/v1/alert-catalog/nothing", json=body, headers=owner["headers"])).status_code == 404
    r = await client.put("/admin/v1/alert-catalog/crowding", json=body, headers=owner["headers"])
    assert r.status_code == 200 and r.json()["enabled"] is False
    row = await su.fetchrow("SELECT reason, before, after FROM platform.audit_log WHERE action='alert.switch' AND target_id='crowding' ORDER BY id DESC LIMIT 1")
    assert row["reason"] == "Paused while we fix messages" and json.loads(row["before"])["enabled"] is True and json.loads(row["after"])["enabled"] is False
    await client.put("/admin/v1/alert-catalog/crowding", json={"enabled": True, "reason": "Fixed, back on"}, headers=owner["headers"])


async def test_an_alert_that_is_not_built_cannot_be_switched_on(client, make, unbuilt):
    owner = await make.staff("owner")
    r = await client.put("/admin/v1/alert-catalog/view_changed", json={"enabled": True, "reason": "try it"}, headers=owner["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "alert_not_built"


async def test_a_switched_off_alert_disappears_for_customers_and_raises_nothing(client, make, su):
    owner = await make.staff("owner")
    t = await make.tenant()
    u = await make.user(t)
    assert "recorder_offline" in await seen(client, u)
    await client.put("/admin/v1/alert-catalog/recorder_offline", json={"enabled": False, "reason": "paused for a test"}, headers=owner["headers"])
    try:
        assert "recorder_offline" not in await seen(client, u)
        assert await fire(t, "recorder_offline") is None
        assert (await client.put("/v1/alert-types/recorder_offline", json={"enabled": True}, headers=u["headers"])).status_code == 404
        assert await fire(t, "camera_offline") is not None                      # the others are not affected
    finally:
        await client.put("/admin/v1/alert-catalog/recorder_offline", json={"enabled": True, "reason": "back on again"}, headers=owner["headers"])
    assert "recorder_offline" in await seen(client, u)


# ------------------------------------------------------------------------------------------ plans decide what a customer has
async def test_a_customer_sees_only_the_alerts_in_their_plan_and_never_the_others(client, make, su):
    pid = await plan(su, ["camera_offline", "crowding"])
    t = await make.tenant(plan=pid)
    u = await make.user(t)
    got = await seen(client, u)
    assert set(got) == {"camera_offline", "crowding"}
    assert got["crowding"]["ai"] is True and got["crowding"]["family"] == "smart" and got["camera_offline"]["ai"] is False
    assert (await client.put("/v1/alert-types/blocked_plate", json={"enabled": True}, headers=u["headers"])).status_code == 404       # not theirs: same as not existing
    assert await fire(t, "crowding") is None                                  # theirs, but a Smart Alert is off until the customer turns it on
    await client.put("/v1/alert-types/crowding", json={"enabled": True}, headers=u["headers"])
    assert await fire(t, "blocked_plate") is None and await fire(t, "crowding") is not None


async def test_a_plan_without_a_list_includes_every_alert_that_exists(client, make, su):
    t = await make.tenant(plan="quick")
    u = await make.user(t)
    assert set(await seen(client, u)) == set(cat.BUILT)


async def test_system_alerts_are_on_from_the_start_and_smart_ones_are_off_except_blocked_vehicles(client, make, su):
    t = await make.tenant(plan="quick")
    got = await seen(client, await make.user(t))
    assert got["camera_offline"]["enabled"] and got["recorder_offline"]["enabled"] and got["blocked_plate"]["enabled"]
    assert not got["crowding"]["enabled"] and not got["vehicle_overstay"]["enabled"]


async def test_creating_and_editing_a_plan_chooses_its_alerts(client, make):
    owner = await make.staff("owner")
    h = owner["headers"]
    base = {"name": "Retail Plus " + uuid.uuid4().hex[:4], "price_per_camera_paise": 90000, "reason": "new plan for shops"}
    r = await client.post("/admin/v1/plans", json={**base, "alerts": ["camera_offline", "crowding"]}, headers=h)
    assert r.status_code == 201 and r.json()["alerts"] == ["camera_offline", "crowding"] and r.json()["alerts_chosen"] is True
    pid = r.json()["id"]
    r = await client.put(f"/admin/v1/plans/{pid}", json={"alerts": ["recorder_offline", "camera_offline", "blocked_plate"], "reason": "add vehicles"}, headers=h)
    assert r.status_code == 200 and r.json()["alerts"] == ["recorder_offline", "camera_offline", "blocked_plate"]
    assert (await client.put(f"/admin/v1/plans/{pid}", json={"alerts": ["nope"], "reason": "bad one"}, headers=h)).status_code == 422
    # left out: a sensible set (system alerts that exist, people counting, and vehicles only with plate reading)
    r = await client.post("/admin/v1/plans", json={**base, "name": base["name"] + " B"}, headers=h)
    assert "camera_offline" in r.json()["alerts"] and "crowding" in r.json()["alerts"] and "blocked_plate" not in r.json()["alerts"]
    r = await client.post("/admin/v1/plans", json={**base, "name": base["name"] + " C", "anpr": True}, headers=h)
    assert "blocked_plate" in r.json()["alerts"]
    listed = {p["id"]: p for p in (await client.get("/admin/v1/plans?include_retired=true", headers=h)).json()["items"]}
    assert listed[pid]["alerts"] == ["recorder_offline", "camera_offline", "blocked_plate"]


async def test_the_catalogue_shows_which_plans_include_each_alert(client, make, su):
    pid = await plan(su, ["crowding"])
    r = await client.get("/admin/v1/alert-catalog", headers=(await make.staff())["headers"])
    items = {i["type"]: i for i in r.json()["items"]}
    assert pid in {p["id"] for p in items["crowding"]["plans"]} and pid not in {p["id"] for p in items["camera_offline"]["plans"]}


# ------------------------------------------------------------------------------------------ one customer, alert by alert
async def test_an_alert_can_be_added_to_one_customer_and_removed_again(unbuilt, client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    pid = await plan(su, ["camera_offline"])
    t, other = await make.tenant(plan=pid), await make.tenant(plan=pid)
    u, ou = await make.user(t), await make.user(other)
    base = f"/admin/v1/tenants/{t}/alerts"
    assert (await client.put(f"{base}/blocked_plate", json={"reason": "bought it"}, headers=support["headers"])).status_code == 403
    assert (await client.put(f"{base}/blocked_plate", json={"reason": "no"}, headers=owner["headers"])).status_code == 422
    assert (await client.put(f"{base}/view_changed", json={"reason": "not built yet"}, headers=owner["headers"])).status_code == 409
    assert (await client.put(f"{base}/camera_offline", json={"reason": "already there"}, headers=owner["headers"])).json()["error"]["code"] == "already_in_plan"
    assert (await client.put(f"{base}/blocked_plate", json={"reason": "customer asked for it"}, headers=owner["headers"])).status_code == 201
    assert (await client.put(f"{base}/blocked_plate", json={"reason": "again please"}, headers=owner["headers"])).json()["error"]["code"] == "already_added"
    assert set(await seen(client, u)) == {"camera_offline", "blocked_plate"}
    assert set(await seen(client, ou)) == {"camera_offline"}                              # another customer on the same plan is unaffected
    assert await fire(t, "blocked_plate") is not None and await fire(other, "blocked_plate") is None
    r = await client.get(base, headers=support["headers"])
    src = {i["type"]: i["source"] for i in r.json()["items"]}
    assert r.json()["plan"] == pid and src == {"camera_offline": "plan", "blocked_plate": "added"}
    assert "crowding" in {a["type"] for a in r.json()["available"]} and "blocked_plate" not in {a["type"] for a in r.json()["available"]}
    assert (await client.delete(f"{base}/camera_offline?reason=remove it", headers=owner["headers"])).json()["error"]["code"] == "from_plan"
    assert (await client.delete(f"{base}/blocked_plate?reason=customer cancelled", headers=owner["headers"])).status_code == 204
    assert set(await seen(client, u)) == {"camera_offline"}
    assert (await client.delete(f"{base}/blocked_plate?reason=customer cancelled", headers=owner["headers"])).status_code == 404
    actions = {r["action"] for r in await su.fetch("SELECT action FROM platform.audit_log WHERE tenant_id=$1", t)}
    assert {"tenant.alert_add", "tenant.alert_remove"} <= actions


async def test_a_customers_settings_for_an_alert_are_kept_when_it_is_taken_away(client, make, su):
    owner = await make.staff("owner")
    pid = await plan(su, ["camera_offline"])
    t = await make.tenant(plan=pid)
    u = await make.user(t)
    await client.put(f"/admin/v1/tenants/{t}/alerts/crowding", json={"reason": "trial of people counting"}, headers=owner["headers"])
    r = await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 20}}, headers=u["headers"])
    assert r.status_code == 200
    await client.delete(f"/admin/v1/tenants/{t}/alerts/crowding?reason=trial is over", headers=owner["headers"])
    assert "crowding" not in await seen(client, u)
    await client.put(f"/admin/v1/tenants/{t}/alerts/crowding", json={"reason": "they signed up"}, headers=owner["headers"])
    got = (await seen(client, u))["crowding"]
    assert got["enabled"] is True and got["params"]["people"] == 20


async def test_a_customer_cannot_read_or_change_what_staff_added_to_others(client, make, su):
    owner = await make.staff("owner")
    a, b = await make.tenant(plan=await plan(su, [])), await make.tenant(plan=await plan(su, []))
    ua, ub = await make.user(a), await make.user(b)
    await client.put(f"/admin/v1/tenants/{a}/alerts/crowding", json={"reason": "gave it to a"}, headers=owner["headers"])
    assert "crowding" in await seen(client, ua) and "crowding" not in await seen(client, ub)
    assert (await client.get(f"/admin/v1/tenants/{a}/alerts", headers=ua["headers"])).status_code in (401, 403)         # the customer token does not open the staff API
    async with db.tenant_tx(b) as conn:
        assert await conn.fetch("SELECT * FROM app.tenant_alert_grants") == []                                                  # row-level security hides the other customer's rows
