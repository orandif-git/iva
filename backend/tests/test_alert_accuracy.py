"""Accuracy of the alerts that already existed (stage 2 of the alert review): each one is closer to what is really happening."""
import json
from datetime import timedelta

from app.services import detectors, sweeper
from tests.helpers import setup_cameras
from tests.test_area_alerts import alerts_of, area, draw, report, shop, steady, turn_on
from tests.test_events_plates import pe, post


def test_the_people_inside_never_go_below_zero_part_way_through_the_day():
    b = lambda i, o: {"people_in": i, "people_out": o}
    assert detectors.people_inside([b(10, 2), b(5, 3)]) == 10
    assert detectors.people_inside([b(0, 6), b(20, 0)]) == 20, "six exits counted before anyone came in do not swallow the next twenty entries"
    assert detectors.people_inside([]) == 0 and detectors.people_inside([b(3, 9)]) == 0


async def test_occupancy_is_counted_for_each_place_not_all_places_added_together(client, make, su):
    await su.execute("DELETE FROM app.rules WHERE rule_type='crowding'; DELETE FROM app.alert_rules WHERE rule_type='crowding'")
    t, u, (a, b), _ = await setup_cameras(client, make, footfall=2, anpr=0)
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params) VALUES ($1,'crowding',true,$2)", t, json.dumps({"people": 30, "picture_minutes": 0}))
    # a second place: the second camera belongs to another recorder
    rec2 = await su.fetchval("""INSERT INTO app.recorders (tenant_id, device_id, name, brand, protocol, status, last_heartbeat_at, key_ciphertext, key_kms_id)
                                VALUES ($1,'SECOND-SHOP','Second shop','dahua','auto_register','online',now(),'x','local') RETURNING id""", t)
    await su.execute("UPDATE app.cameras SET recorder_id=$2 WHERE id=$1", b, rec2)
    for cam in (a, b):
        await su.execute("INSERT INTO app.footfall_counts (tenant_id, camera_id, bucket_start, people_in, people_out) VALUES ($1,$2,date_trunc('minute', now()),20,0)", t, cam)
    await detectors.crowding()
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0, "twenty in each of two shops is not forty in one"
    await su.execute("UPDATE app.footfall_counts SET people_in=35 WHERE camera_id=$1", b)
    await detectors.crowding()
    rows = await su.fetch("SELECT title, body FROM app.alerts WHERE tenant_id=$1", t)
    assert [r["title"] for r in rows] == ["Occupancy limit exceeded at Second shop"] and "About 35 people" in rows[0]["body"]
    await su.execute("DELETE FROM app.rules WHERE rule_type='crowding'; DELETE FROM app.alert_rules WHERE rule_type='crowding'")


async def test_an_overstay_is_not_reported_for_an_allowed_vehicle_or_one_that_was_seen_again(client, make, su):
    await su.execute("DELETE FROM app.rules WHERE rule_type='vehicle_overstay'; DELETE FROM app.alert_rules WHERE rule_type='vehicle_overstay'")
    t, u, _, (cam,) = await setup_cameras(client, make)
    await su.execute("INSERT INTO app.alert_rules (tenant_id, rule_type, enabled, params) VALUES ($1,'vehicle_overstay',true,$2)", t, json.dumps({"max_minutes": 60}))
    assert (await client.post("/v1/vehicle-lists", json={"plate_text": "KA05MN1000", "list_type": "allowed", "note": "Owner"}, headers=u["headers"])).status_code == 201
    await post(client, make, [
        pe(cam, "KA05MN1000", ago=timedelta(hours=5), direction="in", track="o1"),                    # the owner's car: on the allowed list
        pe(cam, "KA05MN2000", ago=timedelta(hours=5), direction="in", track="o2"),                    # came in, exit missed, came in again later: it did not stay
        pe(cam, "KA05MN2000", ago=timedelta(hours=2), direction="in", track="o3"),
        pe(cam, "KA05MN3000", ago=timedelta(hours=5), direction="in", track="o4"),                    # really still here
    ])
    await detectors.vehicle_overstay()
    titles = sorted(r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1 AND rule_type='vehicle_overstay'", t))
    assert titles == ["Vehicle overstay: KA05MN2000", "Vehicle overstay: KA05MN3000"], "the second visit of 2000 is itself over an hour; its first visit is not reported"
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND title LIKE '%KA05MN2000'", t) == 1
    await su.execute("DELETE FROM app.rules WHERE rule_type='vehicle_overstay'; DELETE FROM app.alert_rules WHERE rule_type='vehicle_overstay'")


async def test_your_own_people_inside_a_queue_area_are_not_counted_as_the_queue(client, make, su):
    t, u, cam = await shop(client, make, su)
    await turn_on(client, u, "queue_long")
    (a,) = await draw(client, u, cam, area("queue_area", "Billing queue", length=4, minutes=2, staff=2))
    assert a["params"]["staff"] == 2
    await report(client, make, cam, a["id"], steady(5))                                           # five seen, two of them work there: a queue of three
    assert await alerts_of(su, t, "queue_long") == []
    await su.execute("DELETE FROM app.zone_samples WHERE zone_id=$1", __import__("uuid").UUID(a["id"]))
    await report(client, make, cam, a["id"], steady(7))                                           # seven seen: a queue of five
    (alert,) = await alerts_of(su, t, "queue_long")
    assert alert["title"] == "Long queue at Billing queue" and alert["body"].startswith("5 people are waiting")
    assert (await client.put(f"/v1/cameras/{cam}/areas", json={"areas": [area("queue_area", "Q", staff=50)]}, headers=u["headers"])).status_code == 422


async def test_a_recorder_that_stays_offline_is_said_again_after_six_hours_and_its_return_is_told_days_later(client, make, su):
    t, u, _, _ = await setup_cameras(client, make)
    rec = await su.fetchrow("SELECT id, device_id FROM app.recorders WHERE tenant_id=$1", t)
    await su.execute("UPDATE app.recorders SET status='online', last_heartbeat_at = now() - interval '10 minutes' WHERE id=$1", rec["id"])
    await sweeper.mark_stale_recorders(90)
    await sweeper.mark_stale_recorders(90)
    assert [r["title"] for r in await su.fetch("SELECT title FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at", t)] == ["Recorder offline"]
    # seven hours later it is still down
    await su.execute("UPDATE app.recorders SET last_heartbeat_at = now() - interval '7 hours' WHERE id=$1", rec["id"])
    await su.execute("UPDATE app.alerts SET created_at = now() - interval '7 hours' WHERE tenant_id=$1", t)
    await sweeper.mark_stale_recorders(90)
    await sweeper.mark_stale_recorders(90)
    rows = await su.fetch("SELECT title, body FROM app.alerts WHERE tenant_id=$1 ORDER BY created_at", t)
    assert [r["title"] for r in rows] == ["Recorder offline", "Recorder still offline"] and "offline for 7 hours" in rows[1]["body"]
    # it comes back three days after the customer was last told: they still hear the good news
    await su.execute("UPDATE app.alerts SET created_at = now() - interval '3 days' WHERE tenant_id=$1", t)
    r = await client.post(f"/internal/v1/recorders/{rec['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.2.3.4", "channels": [{"channel_no": 1, "name": "C1"}]})
    assert r.status_code == 200
    assert await su.fetchval("SELECT title FROM app.alerts WHERE tenant_id=$1 AND rule_type='back_to_normal'", t) is not None
