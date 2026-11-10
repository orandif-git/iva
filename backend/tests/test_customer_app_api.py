"""What the redesigned customer app needs from the server: drawn areas, the alert inbox (done / snooze), the health summary and the test message."""
from datetime import timedelta

import pytest

from tests.helpers import setup_cameras
from tests.test_alert_delivery import bsp_server, customer, smtp_server, world  # noqa: F401  (fixtures)
from tests.test_area_alerts import area, shop

L_SHAPE = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.3], [0.3, 0.3], [0.3, 0.7], [0.1, 0.7]]


# ----------------------------------------------------------------------------------------------- drawn areas
async def test_an_area_can_be_any_shape_up_to_twelve_corners(client, make, su):
    t, u, cam = await shop(client, make, su)
    h = u["headers"]
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), "points": L_SHAPE}]}, headers=h)
    assert r.status_code == 200 and r.json()[0]["points"] == L_SHAPE
    twelve = [[0.5 + 0.4 * __import__("math").cos(i * 2 * 3.14159265 / 12), 0.5 + 0.4 * __import__("math").sin(i * 2 * 3.14159265 / 12)] for i in range(12)]
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), "points": twelve}]}, headers=h)).status_code == 200
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), "points": twelve + [[0.5, 0.5]]}]}, headers=h)).status_code == 422


@pytest.mark.parametrize("points", [
    [[0.1, 0.1], [0.6, 0.6], [0.6, 0.1], [0.1, 0.6]],                    # a bow-tie: the edges cross
    [[0.1, 0.1], [0.1, 0.1], [0.6, 0.6]],                                 # two corners on top of each other
    [[0.1, 0.1], [0.5, 0.1], [0.5, 0.12]],                                # a sliver
    [[0.1, 0.1], [0.3, 0.3], [0.5, 0.5]],                                 # all in a line
])
async def test_a_shape_that_is_not_a_real_area_is_refused(client, make, su, points):
    t, u, cam = await shop(client, make, su)
    r = await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), "points": points}]}, headers=u["headers"])
    assert r.status_code == 422, r.text


async def test_redrawing_an_area_keeps_its_id_and_history(client, make, su):
    t, u, cam = await shop(client, make, su)
    h = u["headers"]
    first = (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area()]}, headers=h)).json()[0]
    again = (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [{**area(), "id": first["id"], "points": L_SHAPE}]}, headers=h)).json()[0]
    assert again["id"] == first["id"] and again["points"] == L_SHAPE


# ----------------------------------------------------------------------------------------------- the inbox
async def raise_one(su, t, title="Queue is long", rule="queue_long", mins_ago=5):
    return await su.fetchval(
        "INSERT INTO app.alerts (tenant_id, rule_type, severity, title, body, created_at) VALUES ($1,$2,'warning',$3,'body', now() - make_interval(mins => $4)) RETURNING id", t, rule, title, mins_ago)


async def test_alerts_are_open_snoozed_or_done_and_can_be_filtered(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    h = u["headers"]
    a, b, c = await raise_one(su, t, "A", mins_ago=3), await raise_one(su, t, "B", mins_ago=2), await raise_one(su, t, "C", mins_ago=1)
    assert (await client.post(f"/v1/alerts/{b}/snooze", json={"minutes": 30}, headers=h)).status_code == 200
    assert (await client.post(f"/v1/alerts/{c}/acknowledge", headers=h)).status_code == 200
    states = {x["title"]: x["state"] for x in (await client.get("/v1/alerts", headers=h)).json()}
    assert states == {"A": "open", "B": "snoozed", "C": "done"}
    only = (await client.get("/v1/alerts", params={"state": "open"}, headers=h)).json()
    assert [x["title"] for x in only] == ["A"]


async def test_snooze_defaults_to_an_hour_and_ends_by_itself(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    h = u["headers"]
    a = await raise_one(su, t)
    r = await client.post(f"/v1/alerts/{a}/snooze", headers=h)                    # no body: one hour
    assert r.status_code == 200
    mins = await su.fetchval("SELECT extract(epoch FROM snoozed_until - now())/60 FROM app.alerts WHERE id=$1", a)
    assert 58 < mins <= 60
    await su.execute("UPDATE app.alerts SET snoozed_until = now() - interval '1 minute' WHERE id=$1", a)
    assert (await client.get("/v1/alerts", headers=h)).json()[0]["state"] == "open"
    for bad in (1, 5000):
        assert (await client.post(f"/v1/alerts/{a}/snooze", json={"minutes": bad}, headers=h)).status_code == 422


async def test_a_done_alert_cannot_be_snoozed_and_done_clears_a_snooze(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    h = u["headers"]
    a = await raise_one(su, t)
    await client.post(f"/v1/alerts/{a}/snooze", json={"minutes": 60}, headers=h)
    assert (await client.post(f"/v1/alerts/{a}/acknowledge", headers=h)).status_code == 200
    assert (await su.fetchval("SELECT snoozed_until FROM app.alerts WHERE id=$1", a)) is None
    assert (await client.post(f"/v1/alerts/{a}/snooze", headers=h)).status_code == 409
    assert (await client.post("/v1/alerts/00000000-0000-0000-0000-000000000000/snooze", headers=h)).status_code == 404


async def test_one_customer_cannot_snooze_anothers_alert(client, make, su):
    t1, u1, _, _ = await setup_cameras(client, make)
    t2, u2, _, _ = await setup_cameras(client, make)
    a = await raise_one(su, t1)
    assert (await client.post(f"/v1/alerts/{a}/snooze", headers=u2["headers"])).status_code == 404
    assert (await client.post(f"/v1/alerts/{a}/acknowledge", headers=u2["headers"])).status_code == 404


# ----------------------------------------------------------------------------------------------- health summary
async def test_health_summary_is_ok_when_everything_works(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    await su.execute("UPDATE app.cameras SET stream_state='streaming', stream_seen_at=now() WHERE tenant_id=$1", t)
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at=now() WHERE tenant_id=$1", t)
    h = (await client.get("/v1/health-summary", headers=u["headers"])).json()
    assert h["status"] == "ok" and h["issues"] == []
    assert h["cameras"]["down"] == [] and h["cameras"]["working"] == h["cameras"]["total"] >= 1
    assert h["recorders"]["offline"] == [] and h["data"]["newest_seconds_ago"] is not None and h["data"]["newest_seconds_ago"] < 60


async def test_health_summary_names_what_is_wrong(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at=now() WHERE tenant_id=$1", t)
    await su.execute("UPDATE app.cameras SET stream_state='offline', stream_seen_at=now() WHERE id=$1", cam)
    h = (await client.get("/v1/health-summary", headers=u["headers"])).json()
    assert h["status"] == "attention" and [i["kind"] for i in h["issues"]] == ["camera_down"]
    rec = await su.fetchval("SELECT id FROM app.recorders WHERE tenant_id=$1", t)
    for _ in range(3):
        await su.execute("INSERT INTO app.recorder_link_events (tenant_id, recorder_id, status) VALUES ($1,$2,'offline')", t, rec)
    kinds = sorted(i["kind"] for i in (await client.get("/v1/health-summary", headers=u["headers"])).json()["issues"])
    assert kinds == ["camera_down", "recorder_unstable"]
    await su.execute("UPDATE app.recorders SET status='offline' WHERE id=$1", rec)
    kinds = sorted(i["kind"] for i in (await client.get("/v1/health-summary", headers=u["headers"])).json()["issues"])
    assert "recorder_offline" in kinds and "recorder_unstable" not in kinds


async def test_health_summary_only_shows_the_customers_own_things(client, make, su):
    t1, u1, _, _ = await setup_cameras(client, make)
    t2, u2, _, (cam2,) = await setup_cameras(client, make)
    await su.execute("UPDATE app.cameras SET stream_state='offline', stream_seen_at=now() WHERE id=$1", cam2)
    assert (await client.get("/v1/health-summary", headers=u1["headers"])).json()["cameras"]["down"] == []
    assert len((await client.get("/v1/health-summary", headers=u2["headers"])).json()["cameras"]["down"]) == 1
    assert (await client.get("/v1/health-summary")).status_code == 401


# ----------------------------------------------------------------------------------------------- test message
async def test_a_test_message_goes_to_one_person_and_stays_out_of_the_inbox(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="queue_long")
    h = u["headers"]
    r = await client.post("/v1/alert-types/queue_long/test", json={"recipient_id": rid}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["to"] == "Ravi" and r.json()["status"] in ("queued", "suppressed")
    row = await su.fetchrow("SELECT n.status::text AS status, n.payload FROM app.notifications n JOIN app.alerts a ON a.id=n.alert_id WHERE a.tenant_id=$1 AND (a.details->>'test')::boolean", t)
    assert row and "Test: " in row["payload"]
    assert (await client.get("/v1/alerts", headers=h)).json() == []
    assert (await client.get("/v1/health-summary", headers=h)).json()["open_alerts"] == 0


async def test_a_test_message_needs_a_real_person_with_a_number(client, make, su, world):
    t, u, cam, rid = await customer(client, make, world, rule="queue_long")
    h = u["headers"]
    assert (await client.post("/v1/alert-types/queue_long/test", json={"recipient_id": "00000000-0000-0000-0000-000000000000"}, headers=h)).status_code == 404
    assert (await client.post("/v1/alert-types/not_an_alert/test", json={"recipient_id": rid}, headers=h)).status_code == 404
    other = (await client.post("/v1/recipients", json={"name": "Email only", "email": "a@x.test", "consent_confirmed": True}, headers=h)).json()["id"]
    assert (await client.post("/v1/alert-types/queue_long/test", json={"recipient_id": other}, headers=h)).status_code == 422
