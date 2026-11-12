"""Rules, after review: a rule for one camera must not silence the others, a switched-off camera rule must really keep its camera quiet (in the background
detectors too), the account-wide setting cannot be deleted into a state the screens and the sender disagree about, critical alerts stay account-wide."""
from app.db import db
from app.services import detectors
from app.services.alerts import raise_alert
from tests.helpers import setup_cameras


async def shop(client, make, cameras=2):
    t, u, foot, _ = await setup_cameras(client, make, footfall=cameras, anpr=0)
    return t, u, foot


async def alerts_of(su, t, rule):
    return await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type=$2", t, rule)


async def test_a_rule_for_one_camera_does_not_silence_an_alert_that_is_on_by_default_for_the_others(client, make, su):
    t, u, foot = await shop(client, make)
    r = await client.post("/v1/rules", json={"rule_type": "camera_offline", "camera_id": foot[0], "enabled": True, "condition": {}}, headers=u["headers"])
    assert r.status_code == 201, r.text
    async with db.service_tx() as conn:
        other = await raise_alert(conn, t, "camera_offline", camera_id=foot[1], camera_name="Cam 2", dedupe_key="x1", cooldown_minutes=0)
        same = await raise_alert(conn, t, "camera_offline", camera_id=foot[0], camera_name="Cam 1", dedupe_key="x2", cooldown_minutes=0)
    assert other is not None and same is not None
    shown = next(x for x in (await client.get("/v1/alert-types", headers=u["headers"])).json() if x["type"] == "camera_offline")
    assert shown["enabled"] is True, "the list says what really happens"
    # an alert that is off unless asked for stays off for the cameras without a rule
    await client.post("/v1/rules", json={"rule_type": "queue_long", "camera_id": foot[0], "enabled": True, "condition": {"length": 6, "minutes": 5}}, headers=u["headers"])
    async with db.service_tx() as conn:
        assert await raise_alert(conn, t, "queue_long", camera_id=foot[1], title="no") is None


async def test_a_switched_off_camera_rule_keeps_that_camera_quiet_in_the_crowding_check_while_the_account_rule_covers_the_rest(client, make, su):
    t, u, foot = await shop(client, make)
    await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 2, "picture_minutes": 0}}, headers=u["headers"])
    await client.post("/v1/rules", json={"rule_type": "crowding", "camera_id": foot[0], "enabled": False, "condition": {"people": 2, "picture_minutes": 0}}, headers=u["headers"])
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,now(),10,0)", t, foot[0])
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await detectors.crowding()
    assert await alerts_of(su, t, "crowding") == 0, "that camera has its own rule, and it is off"
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,now(),10,0)", t, foot[1])
    await detectors.crowding()
    assert await alerts_of(su, t, "crowding") == 1, "the other camera still follows the account-wide rule"


async def test_a_camera_rule_with_its_own_limit_is_the_only_one_that_counts_that_camera(client, make, su):
    t, u, foot = await shop(client, make)
    await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 50, "picture_minutes": 0}}, headers=u["headers"])
    await client.post("/v1/rules", json={"rule_type": "crowding", "camera_id": foot[0], "enabled": True, "condition": {"people": 3, "picture_minutes": 0}}, headers=u["headers"])
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,now(),5,0)", t, foot[0])
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await detectors.crowding()
    body = await su.fetchval("SELECT body FROM app.alerts WHERE tenant_id=$1 AND rule_type='crowding'", t)
    assert body and "Your limit is 3" in body, "the camera's own limit (3), not the account-wide 50, raised it"


async def test_the_account_wide_setting_cannot_be_deleted_and_a_change_to_it_reaches_the_old_setting_too(client, make, su):
    t, u, foot = await shop(client, make)
    await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 9, "picture_minutes": 5}}, headers=u["headers"])
    default = next(r for r in (await client.get("/v1/rules", params={"rule_type": "crowding"}, headers=u["headers"])).json() if r["is_default"])
    gone = await client.delete(f"/v1/rules/{default['id']}", headers=u["headers"])
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "default_cannot_be_deleted"
    r = await client.patch(f"/v1/rules/{default['id']}", json={"enabled": False}, headers=u["headers"])
    assert r.status_code == 200
    assert await su.fetchval("SELECT enabled FROM app.alert_rules WHERE tenant_id=$1 AND rule_type='crowding'", t) is False
    limited = await client.patch(f"/v1/rules/{default['id']}", json={"camera_id": foot[0]}, headers=u["headers"])
    assert limited.status_code == 422 and limited.json()["error"]["code"] == "default_is_account_wide"


async def test_critical_alerts_and_account_alerts_cannot_be_limited_to_one_camera_and_only_area_alerts_can_have_an_area(client, make):
    t, u, foot = await shop(client, make)
    for kind in ("disk_fault", "recording_stopped", "recorder_offline", "daily_summary"):
        r = await client.post("/v1/rules", json={"rule_type": kind, "camera_id": foot[0], "enabled": True, "condition": {}}, headers=u["headers"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "whole_account_only", kind
    ok = await client.post("/v1/rules", json={"rule_type": "crowding", "camera_id": foot[0], "enabled": True, "condition": {"people": 5, "picture_minutes": 0}}, headers=u["headers"])
    assert ok.status_code == 201
    area = await client.post("/v1/rules", json={"rule_type": "crowding", "camera_id": foot[0], "zone_id": "00000000-0000-0000-0000-000000000000", "condition": {}}, headers=u["headers"])
    assert area.status_code == 422 and area.json()["error"]["code"] == "no_area"


async def test_a_rule_can_only_send_so_many_test_messages_an_hour(client, make, su):
    t, u, foot = await shop(client, make)
    person = (await client.post("/v1/recipients", json={"name": "Ravi", "phone": "+919876543210", "consent_confirmed": True}, headers=u["headers"])).json()
    r = (await client.post("/v1/rules", json={"rule_type": "crowding", "camera_id": foot[0], "enabled": True, "condition": {"people": 5, "picture_minutes": 0}, "recipient_ids": [person["id"]]},
                           headers=u["headers"])).json()
    codes = [(await client.post(f"/v1/rules/{r['id']}/test", headers=u["headers"])).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


async def test_a_typed_percent_or_underscore_in_the_console_search_is_a_letter_not_a_wildcard(client, make, su):
    staff = await make.staff("support")
    await make.tenant(plan="full", limit=5)
    every = await client.get("/admin/v1/search", params={"q": "%"}, headers=staff["headers"])
    assert every.status_code == 200 and every.json()["customers"] == [] and every.json()["recorders"] == []
    some = await client.get("/admin/v1/search", params={"q": "e"}, headers=staff["headers"])
    assert some.status_code == 200


async def test_a_live_view_offers_smooth_video_only_when_the_server_has_it(client, make, su, monkeypatch):
    from app.config import get_settings
    t, u, foot = await shop(client, make)
    first = await client.post(f"/v1/cameras/{foot[0]}/live", json={"mode": "live"}, headers=u["headers"])
    assert first.status_code == 201 and first.json()["smooth"] is False and first.json()["ice"], first.text
    await client.delete(f"/v1/live/{first.json()['id']}", headers=u["headers"])
    monkeypatch.setattr(get_settings(), "go2rtc_url", "http://127.0.0.1:1984")
    second = await client.post(f"/v1/cameras/{foot[0]}/live", json={"mode": "live"}, headers=u["headers"])
    assert second.status_code == 201 and second.json()["smooth"] is True
    await client.delete(f"/v1/live/{second.json()['id']}", headers=u["headers"])
    ai = await client.post(f"/v1/cameras/{foot[0]}/live", json={"mode": "ai"}, headers=u["headers"])
    assert ai.status_code == 201 and ai.json()["smooth"] is False, "AI view is drawn picture by picture"
