"""Evidence for alerts: the pictures the worker and the server keep, and how the customer's app fetches them."""
import json
import uuid
from datetime import timedelta

import pytest

from app.messaging import evidence
from app.services import detectors
from tests.helpers import iso, now, setup_cameras
from tests.test_alert_delivery import JPEG, bsp_server, customer, smtp_server, world  # noqa: F401  (fixtures)
from tests.test_area_alerts import alerts_of, area, draw, report, shop, steady, turn_on
from tests.test_storage import read_with_picture


@pytest.fixture(autouse=True)
async def clean(su):
    await su.execute("DELETE FROM platform.storage_settings")
    evidence.configure(None)
    evidence.set_store(None)
    yield
    evidence.set_store(None)


async def upload(client, make, cam):
    r = await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers={**make.service(), "Content-Type": "image/jpeg"})
    assert r.status_code == 201, r.text
    return r.json()["evidence_id"]


# ------------------------------------------------------------------------------------------ the queue and the crowd carry a picture
async def test_a_queue_alert_carries_the_picture_the_worker_took_while_the_queue_was_long(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "queue_long")
    (q,) = await draw(client, u, cam, area("queue_area", "Front queue", length=4, minutes=2))
    eid = await upload(client, make, cam)
    rows = steady(5)
    rows[3] = (rows[3][0], 5, None, None, eid)                       # the worker took a picture when the queue got long
    await report(client, make, cam, q["id"], rows)
    (a,) = await alerts_of(su, t, "queue_long")
    assert str(a["evidence_id"]) == eid


async def test_a_crowded_area_alert_carries_a_picture_and_one_without_stays_without(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "people_in_area")
    (z,) = await draw(client, u, cam, area("area_count", "Aisle 1", limit=5, minutes=2))
    await report(client, make, cam, z["id"], steady(6))
    (a,) = await alerts_of(su, t, "people_in_area")
    assert a["evidence_id"] is None, "no picture was taken: the alert still goes out, without one"
    (z2,) = await draw(client, u, cam, area("area_count", "Aisle 2", limit=5, minutes=2))
    eid = await upload(client, make, cam)
    rows = steady(6)
    rows[0] = (rows[0][0], 6, None, None, eid)
    await report(client, make, cam, z2["id"], rows)
    got = [x for x in await alerts_of(su, t, "people_in_area") if "Aisle 2" in x["title"]]
    assert str(got[0]["evidence_id"]) == eid


# ------------------------------------------------------------------------------------------ overstay shows the picture from the way in
async def test_a_vehicle_that_stayed_too_long_shows_the_picture_taken_as_it_came_in(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="vehicle_overstay", params={"max_minutes": 30})
    from tests.test_events_plates import pe as pev
    eid = await upload(client, make, cam)
    e = pev(cam, "KA05MN4321", ago=timedelta(minutes=50), direction="in")
    e["evidence_id"] = eid
    r = await client.post("/internal/v1/events/plates", json={"events": [e]}, headers=make.service())
    assert r.status_code == 200, r.text
    await detectors.vehicle_overstay()
    row = await su.fetchrow("SELECT id, evidence_id FROM app.alerts WHERE tenant_id=$1 AND rule_type='vehicle_overstay'", t)
    assert str(row["evidence_id"]) == eid


# ------------------------------------------------------------------------------------------ the app fetches it
async def alert_with_picture(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "phone_use")
    (z,) = await draw(client, u, cam, area("staff_area", "Counter", seconds=20))
    eid = await upload(client, make, cam)
    await report(client, make, cam, z["id"], [(0, 1, None, 24.0, eid)])
    alert = (await client.get("/v1/alerts", headers=u["headers"])).json()[0]
    return t, u, cam, alert, eid


async def test_a_signed_in_customer_gets_the_alerts_picture_and_nobody_else_does(client, make, su):
    t, u, cam, alert, eid = await alert_with_picture(client, make, su)
    assert alert["has_evidence"] is True
    r = await client.get(f"/v1/alerts/{alert['id']}/image", headers=u["headers"])
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg" and "private" in r.headers["cache-control"]
    other = await make.user(await make.tenant())
    assert (await client.get(f"/v1/alerts/{alert['id']}/image", headers=other["headers"])).status_code == 404
    assert (await client.post(f"/v1/alerts/{alert['id']}/evidence-link", headers=other["headers"])).status_code == 404
    assert (await client.get(f"/v1/alerts/{alert['id']}/image")).status_code == 401
    assert (await client.get(f"/v1/alerts/{uuid.uuid4()}/image", headers=u["headers"])).json()["error"]["code"] == "alert_not_found"


async def test_the_link_made_in_the_app_opens_the_evidence_page_for_an_hour_and_is_logged(client, make, su):
    t, u, cam, alert, eid = await alert_with_picture(client, make, su)
    r = await client.post(f"/v1/alerts/{alert['id']}/evidence-link", headers=u["headers"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["expires_in_minutes"] == 60 and j["has_image"] is True and j["url"].startswith("https://gi.test/e/")
    page = await client.get(j["url"].replace("https://gi.test", ""))
    assert page.status_code == 200 and "image.jpg" in page.text
    token = j["url"].rsplit("/", 1)[1]
    parsed, expired = evidence.read_token(token)
    assert str(parsed) == eid and not expired
    assert evidence.read_token(evidence.make_token(parsed, 60 * 60, now=0))[1] is True, "and it does lapse"
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE tenant_id=$1 AND action='alert.evidence_link'", t) == 1


async def test_after_the_retention_period_the_picture_says_it_is_gone(client, make, su):
    t, u, cam, alert, eid = await alert_with_picture(client, make, su)
    await su.execute("UPDATE app.evidence SET expires_at = now() - interval '1 minute' WHERE id=$1", uuid.UUID(eid))
    r = await client.get(f"/v1/alerts/{alert['id']}/image", headers=u["headers"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "picture_not_found"
    assert (await client.post(f"/v1/alerts/{alert['id']}/evidence-link", headers=u["headers"])).status_code == 404


async def test_the_picture_interval_is_a_setting_of_the_area_and_is_checked(client, make, su):
    t, u, cam = await shop(client, make, su)
    kinds = {k["kind"]: k["settings"] for k in (await client.get("/v1/area-kinds", headers=u["headers"])).json()}
    assert kinds["queue_area"]["picture_minutes"] == {"default": 15, "min": 0, "max": 240} and kinds["area_count"]["picture_minutes"]["default"] == 15
    assert "picture_minutes" not in kinds["staff_area"], "a phone picture is taken once per episode; nothing to set"
    (a,) = await draw(client, u, cam, area("queue_area", "Queue", picture_minutes=0))
    assert a["params"]["picture_minutes"] == 0
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area("queue_area", "Queue", picture_minutes=241)]}, headers=u["headers"])
    assert r.status_code == 422
    (b,) = await draw(client, u, cam, area("queue_area", "Queue"))
    assert b["params"]["picture_minutes"] == 15, "the default when the customer does not set it"


# ------------------------------------------------------------------------------------------ "Too many people" (the count) carries a picture too
async def crowd_world(client, make, su, monkeypatch, picture_minutes=None):
    """A customer whose people-count is over its limit, with a camera that can be photographed (the picture is a stand-in)."""
    from app.services import snapshot
    await su.execute("DELETE FROM app.alert_rules WHERE rule_type='crowding'")           # the detector looks at every customer's rule: leave only this one
    t, u, (foot,), _ = await setup_cameras(client, make, footfall=1, anpr=0)
    params = {"people": 10, **({"picture_minutes": picture_minutes} if picture_minutes is not None else {})}
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params) VALUES ($1,'crowding',true,$2)", t, json.dumps(params))
    await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,date_trunc('minute', now()),30,5)", t, foot)
    taken = []

    async def source(row, decrypt):
        return "test://camera", None

    async def grab(url, fmt):
        taken.append(1)
        return JPEG
    monkeypatch.setattr(snapshot, "source_for", source)
    monkeypatch.setattr(snapshot, "grab", grab)
    detectors._crowd_pictures.clear()
    return t, u, foot, taken


async def test_the_people_count_alert_shows_a_picture_of_the_busiest_camera(client, make, su, monkeypatch):
    t, u, foot, taken = await crowd_world(client, make, su, monkeypatch)
    assert await detectors.crowding() == 1
    row = await su.fetchrow("SELECT evidence_id FROM app.alerts WHERE tenant_id=$1 AND rule_type='crowding'", t)
    assert row["evidence_id"] is not None and len(taken) == 1
    assert await su.fetchval("SELECT camera_id FROM app.evidence WHERE id=$1", row["evidence_id"]) == uuid.UUID(foot)


async def test_a_new_crowd_picture_is_taken_only_after_the_chosen_minutes_and_zero_means_none(client, make, su, monkeypatch):
    t, u, foot, taken = await crowd_world(client, make, su, monkeypatch, picture_minutes=15)
    await detectors.crowding()
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)                          # the hour is up: the alert may be sent again
    await detectors.crowding()
    assert len(taken) == 1, "the picture from a few minutes ago is still fresh, so it is shown again"
    (key,) = [k for k in detectors._crowd_pictures if k[0] == t]                                # one place: this customer's recorder
    first = detectors._crowd_pictures[key]
    detectors._crowd_pictures[key] = (first[0] - 16 * 60, first[1])                             # 16 minutes pass
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await detectors.crowding()
    assert len(taken) == 2
    await su.execute("UPDATE app.alert_rules SET params = '{\"people\": 10, \"picture_minutes\": 0}'::jsonb WHERE tenant_id=$1 AND rule_type='crowding'", t)
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    detectors._crowd_pictures.clear()
    await detectors.crowding()
    assert len(taken) == 2 and (await su.fetchrow("SELECT evidence_id FROM app.alerts WHERE tenant_id=$1", t))["evidence_id"] is None


async def test_if_the_camera_cannot_be_photographed_the_crowd_alert_still_goes_out(client, make, su, monkeypatch):
    from app.services import snapshot
    t, u, foot, taken = await crowd_world(client, make, su, monkeypatch)

    async def broken(url, fmt):
        raise RuntimeError("camera offline")
    monkeypatch.setattr(snapshot, "grab", broken)
    assert await detectors.crowding() == 1
    assert (await su.fetchrow("SELECT evidence_id FROM app.alerts WHERE tenant_id=$1 AND rule_type='crowding'", t))["evidence_id"] is None
