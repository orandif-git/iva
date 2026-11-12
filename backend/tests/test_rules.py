"""Customer-built rules: several rules of one alert kind, each for its own camera or marked area."""
import pathlib
import uuid

import pytest

from app.db import db
from app.services import rules as rule_service
from app.services.alerts import raise_alert
from tests.helpers import setup_cameras

SQUARE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]


async def shop(client, make, cameras=2):
    t, u, foot, _ = await setup_cameras(client, make, footfall=cameras, anpr=0)
    return t, u, foot


def camera_rule(camera_id, **extra):
    return {"name": f"Queue at {camera_id[-6:]}", "rule_type": "queue_long", "camera_id": camera_id, "enabled": True,
            "condition": {"length": 6, "minutes": 5}, **extra}


# ------------------------------------------------------------------------------------------------ the schema
def test_the_rules_table_is_in_the_schema_and_has_a_migration():
    root = pathlib.Path(__file__).resolve().parents[2]
    schema = (root / "schema.sql").read_text()
    assert "CREATE TABLE app.rules" in schema and "rules_one_default_idx" in schema and "rules_zone_needs_camera" in schema
    mig = (root / "backend" / "migrations" / "020_rules.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS app.rules" in mig and "INSERT INTO app.rules" in mig and "FROM app.alert_rules" in mig
    assert "DROP CONSTRAINT IF EXISTS alerts_rule_id_fkey" in mig, "an alert may now point at either kind of rule"


# ------------------------------------------------------------------------------------------------ several rules of one kind
async def test_a_customer_can_have_two_rules_of_the_same_kind_on_two_cameras(client, make):
    t, u, foot = await shop(client, make)
    a = await client.post("/v1/rules", json=camera_rule(foot[0]), headers=u["headers"])
    b = await client.post("/v1/rules", json=camera_rule(foot[1], condition={"length": 12, "minutes": 2}), headers=u["headers"])
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    assert a.json()["id"] != b.json()["id"]
    listed = (await client.get("/v1/rules", params={"rule_type": "queue_long"}, headers=u["headers"])).json()
    assert {r["camera_id"] for r in listed} == {foot[0], foot[1]}
    assert {r["condition"]["length"] for r in listed} == {6, 12}
    # Each camera resolves to its own numbers, and a third camera resolves to nothing (no rule for it, no account-wide rule).
    async with db.service_tx() as conn:
        one = await rule_service.effective(conn, t, "queue_long", camera_id=foot[0])
        two = await rule_service.effective(conn, t, "queue_long", camera_id=foot[1])
        other = await rule_service.effective(conn, t, "queue_long", camera_id=uuid.uuid4())
    assert one["params"]["length"] == 6 and two["params"]["length"] == 12 and other is None


async def test_a_rule_for_another_camera_does_not_make_this_camera_alert(client, make, su):
    t, u, foot = await shop(client, make)
    one = (await client.post("/v1/rules", json=camera_rule(foot[0]), headers=u["headers"])).json()
    assert one["enabled"]
    async with db.service_tx() as conn:
        off = await raise_alert(conn, t, "queue_long", camera_id=foot[1], title="Should not fire")
    assert off is None
    async with db.service_tx() as conn:
        fired = await raise_alert(conn, t, "queue_long", camera_id=foot[0], title="Should fire")
    assert fired is not None
    row = await su.fetchrow("SELECT rule_id FROM app.alerts WHERE id=$1", fired)
    assert str(row["rule_id"]) == one["id"], "the alert remembers which rule raised it"


async def test_a_camera_rule_beats_the_account_wide_default_and_a_disabled_rule_raises_nothing(client, make):
    t, u, foot = await shop(client, make)
    # The old alert-types screen writes the account-wide default (nine people); a camera rule is stricter (three).
    await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 9, "picture_minutes": 5}}, headers=u["headers"])
    r = (await client.post("/v1/rules", json={"name": "Front door crowd", "rule_type": "crowding", "camera_id": foot[0], "enabled": True,
                                              "condition": {"people": 3, "picture_minutes": 5}}, headers=u["headers"])).json()
    async with db.service_tx() as conn:
        here = await rule_service.effective(conn, t, "crowding", camera_id=foot[0])
        elsewhere = await rule_service.effective(conn, t, "crowding", camera_id=foot[1])
    assert here["params"]["people"] == 3 and elsewhere["params"]["people"] == 9
    await client.patch(f"/v1/rules/{r['id']}", json={"enabled": False}, headers=u["headers"])
    async with db.service_tx() as conn:
        assert await raise_alert(conn, t, "crowding", camera_id=foot[0], title="Off") is None
        # The old default still applies to the other camera.
        assert await raise_alert(conn, t, "crowding", camera_id=foot[1], title="On") is not None


# ------------------------------------------------------------------------------------------------ the old screen stays in step
async def test_the_old_alert_types_screen_writes_the_account_wide_default_rule(client, make):
    t, u, foot = await shop(client, make)
    r = await client.put("/v1/alert-types/crowding", json={"enabled": True, "params": {"people": 42, "picture_minutes": 5}}, headers=u["headers"])
    assert r.status_code == 200, r.text
    listed = (await client.get("/v1/rules", params={"rule_type": "crowding"}, headers=u["headers"])).json()
    assert len(listed) == 1 and listed[0]["is_default"] and listed[0]["condition"]["people"] == 42
    types = (await client.get("/v1/alert-types", headers=u["headers"])).json()
    crowding = next(x for x in types if x["type"] == "crowding")
    assert crowding["enabled"] is True and crowding["params"]["people"] == 42 and crowding["rule_id"] == listed[0]["id"]


# ------------------------------------------------------------------------------------------------ marked areas
async def test_an_area_rule_remembers_its_camera_and_area_and_keeps_the_area_limits(client, make):
    t, u, foot = await shop(client, make)
    drawn = await client.put(f"/v1/cameras/{foot[0]}/areas", json={"areas": [
        {"kind": "area_count", "name": "Billing", "points": SQUARE, "params": {"limit": 5, "minutes": 2}}]}, headers=u["headers"])
    assert drawn.status_code == 200, drawn.text
    zone = drawn.json()[0]["id"]
    r = await client.post("/v1/rules", json={"name": "Billing crowd", "rule_type": "people_in_area", "camera_id": foot[0], "zone_id": zone,
                                             "condition": {"limit": 8}, "enabled": True}, headers=u["headers"])
    assert r.status_code == 201, r.text
    assert r.json()["area_kind"] == "area_count" and r.json()["camera_id"] == foot[0] and r.json()["zone_id"] == zone
    async with db.service_tx() as conn:
        hit = await rule_service.resolve(conn, t, "people_in_area", camera_id=foot[0], zone_id=zone)
        miss = await rule_service.resolve(conn, t, "people_in_area", camera_id=foot[0], zone_id=uuid.uuid4())
    assert hit is not None and hit["zone_id"] == uuid.UUID(zone) and miss is None


async def test_a_zone_must_be_on_the_camera_given(client, make):
    t, u, foot = await shop(client, make)
    drawn = await client.put(f"/v1/cameras/{foot[0]}/areas", json={"areas": [
        {"kind": "queue_area", "name": "Till", "points": SQUARE, "params": {"length": 4, "minutes": 2}}]}, headers=u["headers"])
    assert drawn.status_code == 200, drawn.text
    zone = drawn.json()[0]["id"]
    r = await client.post("/v1/rules", json={"name": "Wrong camera", "rule_type": "queue_long", "camera_id": foot[1], "zone_id": zone, "enabled": True},
                          headers=u["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "area_not_found"
    r = await client.post("/v1/rules", json={"name": "No camera", "rule_type": "queue_long", "zone_id": zone, "enabled": True}, headers=u["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "camera_required"


# ------------------------------------------------------------------------------------------------ checks
async def test_bad_numbers_quiet_hours_and_people_are_refused(client, make):
    t, u, foot = await shop(client, make)
    bad = await client.post("/v1/rules", json={**camera_rule(foot[0]), "condition": {"length": 0}}, headers=u["headers"])
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_params"
    one_sided = await client.post("/v1/rules", json={**camera_rule(foot[0]), "quiet_start": "22:00"}, headers=u["headers"])
    assert one_sided.status_code == 422 and one_sided.json()["error"]["code"] == "invalid_quiet_hours"
    stranger = await client.post("/v1/rules", json={**camera_rule(foot[0]), "recipient_ids": [str(uuid.uuid4())]}, headers=u["headers"])
    assert stranger.status_code == 422 and stranger.json()["error"]["code"] == "recipient_not_found"
    unknown = await client.post("/v1/rules", json={**camera_rule(foot[0]), "condition": {"nonsense": 1}}, headers=u["headers"])
    assert unknown.status_code == 422


async def test_a_change_must_carry_the_version_the_person_saw(client, make):
    t, u, foot = await shop(client, make)
    r = (await client.post("/v1/rules", json=camera_rule(foot[0]), headers=u["headers"])).json()
    first = await client.patch(f"/v1/rules/{r['id']}", json={"condition": {"length": 7, "minutes": 5}, "version": r["version"]}, headers=u["headers"])
    assert first.status_code == 200 and first.json()["version"] == r["version"] + 1
    stale = await client.patch(f"/v1/rules/{r['id']}", json={"condition": {"length": 8, "minutes": 5}, "version": r["version"]}, headers=u["headers"])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "rule_changed"


async def test_deleting_a_rule(client, make):
    t, u, foot = await shop(client, make)
    r = (await client.post("/v1/rules", json=camera_rule(foot[0]), headers=u["headers"])).json()
    assert (await client.delete(f"/v1/rules/{r['id']}", headers=u["headers"])).status_code == 204
    assert (await client.get(f"/v1/rules/{r['id']}", headers=u["headers"])).status_code == 404
    assert all(x["id"] != r["id"] for x in (await client.get("/v1/rules", headers=u["headers"])).json())


async def test_another_customers_rule_is_not_visible(client, make):
    t, u, foot = await shop(client, make)
    other = await make.tenant()
    other_user = await make.user(other)
    mine = (await client.post("/v1/rules", json=camera_rule(foot[0]), headers=u["headers"])).json()
    assert (await client.get(f"/v1/rules/{mine['id']}", headers=other_user["headers"])).status_code == 404
    assert (await client.get("/v1/rules", headers=other_user["headers"])).json() == []
    assert (await client.delete(f"/v1/rules/{mine['id']}", headers=other_user["headers"])).status_code == 404
