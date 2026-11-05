import json
import uuid
from datetime import timedelta

import pytest

from app.services import maintenance
from tests.helpers import iso, now, setup_cameras


def pe(cam, plate="GJ14AW1013", ago=timedelta(minutes=10), conf=95, direction="in", track=None, snap="s/1.jpg", vt="Truck"):
    return {"camera_id": cam, "seen_at": iso(now() - ago), "plate_raw": plate, "confidence": conf, "direction": direction,
            "track_id": track or uuid.uuid4().hex[:12], "snapshot_key": snap, "vehicle_type": vt}


async def post(client, make, events):
    r = await client.post("/internal/v1/events/plates", json={"events": events}, headers=make.service())
    assert r.status_code == 200, r.text
    return r.json()


async def plates(client, u, **params):
    r = await client.get("/v1/plates", params={"from": iso(now() - timedelta(days=2)), **params}, headers=u["headers"])
    assert r.status_code == 200, r.text
    return r.json()


async def test_reads_are_cleaned_and_flagged_for_review_when_unsure(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    res = await post(client, make, [pe(cam, "gj 14-aw 1013"), pe(cam, "GJ14AW1O13", ago=timedelta(minutes=20)),
                                    pe(cam, "GJ14AW1013", ago=timedelta(minutes=30), conf=40), pe(cam, "HR55AB??21", ago=timedelta(minutes=40))])
    assert res["accepted"] == 4 and res["duplicates"] == 0 and res["rejected"] == []
    items = {i["seen_at"]: i for i in (await plates(client, u))["items"]}
    got = sorted((i["plate_text"], i["review_status"]) for i in items.values())
    assert got == [("GJ14AW1013", "auto"), ("GJ14AW1013", "auto"), ("GJ14AW1013", "needs_review"), ("HR55AB21", "needs_review")]


async def test_the_same_vehicle_is_not_counted_twice(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    first = pe(cam, track="T1")
    res = await post(client, make, [first, {**first}, pe(cam, "GJ14AW1013", ago=timedelta(minutes=10, seconds=-30), track="T2")])   # retry; same plate 30 s later
    assert (res["accepted"], res["duplicates"]) == (1, 2)
    out = await post(client, make, [pe(cam, "GJ14AW1013", ago=timedelta(minutes=9, seconds=30), direction="out", track="T3")])        # other direction is a different event
    assert out["accepted"] == 1
    later = await post(client, make, [pe(cam, "GJ14AW1013", ago=timedelta(minutes=5), track="T4")])                                  # minutes later is a new visit
    assert later["accepted"] == 1


async def test_exit_is_matched_to_entry_and_dwell_time_is_recorded(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, ago=timedelta(hours=3), direction="in"), pe(cam, "MH12AB1234", ago=timedelta(hours=2), direction="in")])
    await post(client, make, [pe(cam, ago=timedelta(hours=1), direction="out"), pe(cam, "KA01A1234", ago=timedelta(minutes=50), direction="out")])
    items = {i["plate_text"]: i for i in (await plates(client, u, direction="out"))["items"]}
    assert items["GJ14AW1013"]["dwell_seconds"] in range(7199, 7202) and items["GJ14AW1013"]["entry_event_id"]
    assert items["KA01A1234"]["entry_event_id"] is None                          # left without a recorded entry
    detail = (await client.get(f"/v1/plates/{items['GJ14AW1013']['id']}", headers=u["headers"])).json()
    assert detail["entry"]["direction"] == "in" and detail["plate_raw"] == "GJ14AW1013"
    entry_id = detail["entry"]["id"]
    back = (await client.get(f"/v1/plates/{entry_id}", headers=u["headers"])).json()
    assert back["exit"]["id"] == detail["id"]


async def test_one_entry_can_be_used_by_only_one_exit(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, ago=timedelta(hours=3), direction="in")])
    await post(client, make, [pe(cam, ago=timedelta(hours=2), direction="out")])
    await post(client, make, [pe(cam, ago=timedelta(hours=1), direction="out")])          # a second exit: nothing left to match
    outs = (await plates(client, u, direction="out"))["items"]
    assert sorted(bool(o["entry_event_id"]) for o in outs) == [False, True]


async def test_a_doubtful_exit_is_not_paired_automatically(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, ago=timedelta(hours=3), direction="in"), pe(cam, ago=timedelta(hours=1), direction="out", conf=50)])
    out = (await plates(client, u, direction="out"))["items"][0]
    assert out["review_status"] == "needs_review" and out["entry_event_id"] is None


async def test_a_blocked_plate_raises_one_alert_and_a_doubtful_read_raises_none(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    assert (await client.post("/v1/vehicle-lists", json={"plate_text": "MH04ZZ9090", "list_type": "blocked", "note": "Stolen"}, headers=u["headers"])).status_code == 201
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=30), conf=40)])               # unsure: no alert
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1", t) == 0
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=5), track="A")])
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=1), track="B")])              # again within 10 minutes
    rows = await su.fetch("SELECT title, severity, evidence_key, camera_id FROM app.alerts WHERE tenant_id=$1", t)
    assert len(rows) == 1 and rows[0]["title"] == "Blocked vehicle MH04ZZ9090" and rows[0]["severity"] == "critical" and rows[0]["evidence_key"] == "s/1.jpg"
    await post(client, make, [pe(cam, "MH12AB1234", ago=timedelta(minutes=2))])
    assert {i["plate_text"] for i in (await plates(client, u, blocked=True))["items"]} == {"MH04ZZ9090"}
    assert {i["plate_text"] for i in (await plates(client, u, blocked=False))["items"]} == {"MH12AB1234"}


async def test_rejections_name_the_reason(client, make, su):
    t, u, (foot,), (cam,) = await setup_cameras(client, make)
    res = await post(client, make, [pe(foot), pe("00000000-0000-0000-0000-000000000000"), pe(cam, ago=timedelta(days=9)), {**pe(cam), "seen_at": iso(now() + timedelta(hours=1))}])
    assert {r["index"]: r["reason"] for r in res["rejected"]} == {0: "plates_not_enabled", 1: "unknown_camera", 2: "too_old", 3: "in_future"}
    assert res["accepted"] == 0
    assert (await client.post("/internal/v1/events/plates", json={"events": [pe(cam)] * 201}, headers=make.service())).status_code == 422


async def test_search_filters_and_pages(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    evs = [pe(cam, f"GJ14AW{1000 + i}", ago=timedelta(minutes=10 + 3 * i), direction="in" if i % 2 else "out") for i in range(7)] + [pe(cam, "MH12AB1234", ago=timedelta(minutes=2))]
    await post(client, make, evs)
    assert len((await plates(client, u))["items"]) == 8
    assert {i["plate_text"] for i in (await plates(client, u, q="mh12"))["items"]} == {"MH12AB1234"}
    assert len((await plates(client, u, q="GJ14"))["items"]) == 7
    assert len((await plates(client, u, direction="out"))["items"]) == 4
    seen, cursor = [], None
    while True:
        page = await plates(client, u, limit=3, **({"cursor": cursor} if cursor else {}))
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 8 and len(set(seen)) == 8                                  # no gaps or repeats across pages
    r = await client.get("/v1/plates", params={"from": iso(now() - timedelta(days=200)), "to": iso(now())}, headers=u["headers"])
    assert r.json()["error"]["code"] == "bad_range"


async def test_review_queue_confirm_and_correct_with_audit(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, "GJ14AW1013", ago=timedelta(hours=2), direction="in"), pe(cam, "GJ14AW1O13", ago=timedelta(minutes=50), conf=45, direction="out", track="X")])
    q = (await client.get("/v1/plates/review-queue", headers=u["headers"])).json()["items"]
    assert len(q) == 1 and q[0]["review_status"] == "needs_review" and q[0]["entry_event_id"] is None
    pid = q[0]["id"]
    for bad in ("GJ14AW1O13", "nonsense", ""):
        assert (await client.patch(f"/v1/plates/{pid}", json={"plate_text": bad}, headers=u["headers"])).status_code in (422,)
    assert (await client.patch(f"/v1/plates/{pid}", json={}, headers=u["headers"])).status_code == 422
    assert (await client.patch(f"/v1/plates/{pid}", json={"plate_text": "GJ14AW1013", "confirm": True}, headers=u["headers"])).status_code == 422
    fixed = (await client.patch(f"/v1/plates/{pid}", json={"plate_text": "gj14aw1013"}, headers=u["headers"])).json()
    assert fixed["plate_text"] == "GJ14AW1013" and fixed["review_status"] == "corrected"
    assert fixed["entry_event_id"] and fixed["dwell_seconds"] in range(4199, 4202)    # the corrected exit found its entry
    assert (await client.get("/v1/plates/review-queue", headers=u["headers"])).json()["items"] == []
    ok = (await client.patch(f"/v1/plates/{pid}", json={"confirm": True}, headers=u["headers"])).json()
    assert ok["review_status"] == "confirmed"
    log = await su.fetch("SELECT action, after FROM platform.audit_log WHERE tenant_id=$1 AND action='plate.review' ORDER BY id", t)
    after = [json.loads(l["after"]) for l in log]
    assert [a["status"] for a in after] == ["corrected", "confirmed"]
    assert after[0]["from"] == "GJ14AW1013" and after[0]["to"] == "GJ14AW1013"      # the stored text was already cleaned; a person verified it
    assert (await client.get(f"/v1/plates/{pid}", headers=u["headers"])).json()["plate_raw"] == "GJ14AW1O13"     # the raw OCR text is kept


async def test_confirming_an_invalid_read_is_refused(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, "HR55AB??21")])
    pid = (await client.get("/v1/plates/review-queue", headers=u["headers"])).json()["items"][0]["id"]
    r = await client.patch(f"/v1/plates/{pid}", json={"confirm": True}, headers=u["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_plate"


async def test_regular_users_and_staff_views_cannot_change_reads(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, conf=30)])
    pid = (await plates(client, u))["items"][0]["id"]
    regular = await make.user(t, role="regular")
    assert (await client.get("/v1/plates", params={"from": iso(now() - timedelta(days=1))}, headers=regular["headers"])).status_code == 200
    assert (await client.patch(f"/v1/plates/{pid}", json={"confirm": True}, headers=regular["headers"])).status_code == 403
    assert (await client.get("/v1/plates/review-queue", headers=regular["headers"])).status_code == 403
    staff = await make.staff("support")
    imp = {"Authorization": "Bearer " + (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer asked us to check", "minutes": 15}, headers=staff["headers"])).json()["token"]}
    assert (await client.get(f"/v1/plates/{pid}", headers=imp)).status_code == 200
    assert (await client.patch(f"/v1/plates/{pid}", json={"confirm": True}, headers=imp)).json()["error"]["code"] == "read_only_session"


async def test_one_tenant_cannot_see_or_touch_anothers_plates(client, make):
    t1, u1, _, (c1,) = await setup_cameras(client, make)
    t2, u2, _, (c2,) = await setup_cameras(client, make)
    await post(client, make, [pe(c1, "GJ14AW1013", conf=30)])
    pid = (await plates(client, u1))["items"][0]["id"]
    assert (await plates(client, u2))["items"] == []
    assert (await client.get(f"/v1/plates/{pid}", headers=u2["headers"])).status_code == 404
    assert (await client.patch(f"/v1/plates/{pid}", json={"plate_text": "MH12AB1234"}, headers=u2["headers"])).status_code == 404
    assert (await plates(client, u2, camera_id=c1))["items"] == []


async def test_vehicle_lists(client, make):
    t, u, _, _ = await setup_cameras(client, make)
    regular = await make.user(t, role="regular")
    add = lambda body, h=u: client.post("/v1/vehicle-lists", json=body, headers=h["headers"])
    r = await add({"plate_text": "gj14 aw 1013", "list_type": "blocked"})
    assert r.status_code == 201 and r.json()["plate_text"] == "GJ14AW1013"
    assert (await add({"plate_text": "GJ14AW1013", "list_type": "blocked"})).json()["error"]["code"] == "already_listed"
    assert (await add({"plate_text": "GJ14AW1013", "list_type": "allowed"})).status_code == 201           # a plate may be on both lists' own terms
    assert (await add({"plate_text": "GJ14AW1O13", "list_type": "blocked"})).json()["error"]["code"] == "invalid_plate"
    assert (await add({"plate_text": "MH12AB1234", "list_type": "allowed"}, regular)).status_code == 403
    assert len((await client.get("/v1/vehicle-lists", headers=regular["headers"])).json()) == 2
    assert len((await client.get("/v1/vehicle-lists", params={"list_type": "blocked"}, headers=u["headers"])).json()) == 1
    assert (await client.delete(f"/v1/vehicle-lists/{r.json()['id']}", headers=regular["headers"])).status_code == 403
    assert (await client.delete(f"/v1/vehicle-lists/{r.json()['id']}", headers=u["headers"])).status_code == 204
    assert (await client.delete(f"/v1/vehicle-lists/{r.json()['id']}", headers=u["headers"])).status_code == 404


async def test_events_land_in_the_right_monthly_partition(client, make, su):
    await maintenance.ensure_partitions()
    t, u, _, (cam,) = await setup_cameras(client, make)
    await post(client, make, [pe(cam, "GJ14AW1013", ago=timedelta(minutes=3))])
    tables = {r["t"] for r in await su.fetch("SELECT tableoid::regclass::text AS t FROM app.plate_events WHERE tenant_id=$1", t)}
    assert tables and not any(x.endswith("default") for x in tables)
    parts = {r["relname"] for r in await su.fetch("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid WHERE i.inhparent='app.plate_events'::regclass")}
    nm = now()
    for k in range(0, 3):
        m = nm.month - 1 + k
        assert f"plate_events_{nm.year + m // 12}_{m % 12 + 1:02d}" in parts
    assert await su.fetchval("SELECT count(*) FROM app.plate_events_default") == 0
