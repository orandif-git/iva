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
