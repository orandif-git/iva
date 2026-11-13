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
    """The plate reads of the customer, newest first, as saved (what the Vehicles page is built from)."""
    from app.db import db
    where, args = ["tenant_id=$1", "seen_at >= now() - interval '2 days'"], [u["tenant_id"]]
    if "direction" in params:
        args.append(params["direction"]); where.append(f"direction = ${len(args)}::app.direction")
    if "blocked" in params:
        where.append(("" if params["blocked"] else "NOT ") + "EXISTS (SELECT 1 FROM app.vehicle_lists v WHERE v.tenant_id = p.tenant_id AND v.plate_text = p.plate_text AND v.list_type = 'blocked')")
    async with db.admin_tx() as c:
        rows = await c.fetch(f"""SELECT id, seen_at, plate_text, plate_raw, confidence::int AS confidence, direction::text AS direction, review_status::text AS review_status,
                                        entry_event_id, dwell_seconds, (evidence_id IS NOT NULL) AS has_snapshot
                                 FROM app.plate_events p WHERE {' AND '.join(where)} ORDER BY seen_at DESC, id DESC""", *args)
    return {"items": [dict(r) for r in rows]}


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
    entry = [i for i in (await plates(client, u, direction="in"))["items"] if i["id"] == items["GJ14AW1013"]["entry_event_id"]]
    assert len(entry) == 1 and entry[0]["direction"] == "in" and entry[0]["plate_text"] == "GJ14AW1013", "the exit points at its own entry"


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
    await post(client, make, [pe(cam, "KA01AB1234", ago=timedelta(minutes=40), conf=40)])               # unsure, and nothing like a blocked plate: only "needs review"
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='blocked_plate'", t) == 0
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=5), track="A")])
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=1), track="B")])              # again within 10 minutes
    rows = await su.fetch("SELECT title, severity, evidence_key, camera_id FROM app.alerts WHERE tenant_id=$1", t)
    assert len(rows) == 1 and rows[0]["title"] == "Blocked vehicle detected: MH04ZZ9090" and rows[0]["severity"] == "critical" and rows[0]["evidence_key"] == "s/1.jpg"
    await post(client, make, [pe(cam, "MH12AB1234", ago=timedelta(minutes=2))])
    assert {i["plate_text"] for i in (await plates(client, u, blocked=True))["items"]} == {"MH04ZZ9090"}
    assert {i["plate_text"] for i in (await plates(client, u, blocked=False))["items"]} == {"MH12AB1234", "KA01AB1234"}


async def test_an_unclear_read_that_looks_like_a_blocked_plate_is_reported_as_possible(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    assert (await client.post("/v1/vehicle-lists", json={"plate_text": "MH04ZZ9090", "list_type": "blocked", "note": "Stolen"}, headers=u["headers"])).status_code == 201
    await post(client, make, [pe(cam, "MH04ZZ9090", ago=timedelta(minutes=30), conf=40)])               # the right plate, but the camera was not sure
    a = await su.fetchrow("SELECT title, body, severity, rule_type FROM app.alerts WHERE tenant_id=$1", t)
    assert a["rule_type"] == "blocked_plate" and a["title"] == "Possible blocked vehicle MH04ZZ9090" and a["severity"] == "critical" and "check the picture" in a["body"]
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await post(client, make, [pe(cam, "MH04ZZ9099", ago=timedelta(minutes=20), conf=45, track="n1")])   # one character away, and unsure: still worth a look
    assert await su.fetchval("SELECT title FROM app.alerts WHERE tenant_id=$1 AND rule_type='blocked_plate'", t) == "Possible blocked vehicle MH04ZZ9090"
    await su.execute("DELETE FROM app.alerts WHERE tenant_id=$1", t)
    await post(client, make, [pe(cam, "MH04ZZ9099", ago=timedelta(minutes=10), track="n2")])            # one character away but read clearly: a different vehicle
    await post(client, make, [pe(cam, "MH04AB1299", ago=timedelta(minutes=5), conf=40, track="n3")])    # unsure and nothing like it
    assert await su.fetchval("SELECT count(*) FROM app.alerts WHERE tenant_id=$1 AND rule_type='blocked_plate'", t) == 0


async def test_rejections_name_the_reason(client, make, su):
    t, u, (foot,), (cam,) = await setup_cameras(client, make)
    res = await post(client, make, [pe(foot), pe("00000000-0000-0000-0000-000000000000"), pe(cam, ago=timedelta(days=9)), {**pe(cam), "seen_at": iso(now() + timedelta(hours=1))}])
    assert {r["index"]: r["reason"] for r in res["rejected"]} == {0: "plates_not_enabled", 1: "unknown_camera", 2: "too_old", 3: "in_future"}
    assert res["accepted"] == 0
    assert (await client.post("/internal/v1/events/plates", json={"events": [pe(cam)] * 201}, headers=make.service())).status_code == 422




async def test_vehicle_lists(client, make):
    t, u, _, _ = await setup_cameras(client, make)
    support = await make.staff("support")
    add = lambda body, h=u: client.post("/v1/vehicle-lists", json=body, headers=h["headers"])
    r = await add({"plate_text": "gj14 aw 1013", "list_type": "blocked"})
    assert r.status_code == 201 and r.json()["plate_text"] == "GJ14AW1013"
    assert (await add({"plate_text": "GJ14AW1013", "list_type": "blocked"})).json()["error"]["code"] == "already_listed"
    assert (await add({"plate_text": "GJ14AW1013", "list_type": "allowed"})).status_code == 201           # a plate may be on both lists' own terms
    assert (await add({"plate_text": "GJ14AW1O13", "list_type": "blocked"})).json()["error"]["code"] == "invalid_plate"
    base = f"/admin/v1/tenants/{t}/shop/vehicle-lists"
    assert (await client.post(base, json={"plate_text": "MH12AB1234", "list_type": "allowed"}, headers=support["headers"])).status_code == 403     # Support only looks
    assert len((await client.get(base, headers=support["headers"])).json()) == 2
    assert len((await client.get("/v1/vehicle-lists", params={"list_type": "blocked"}, headers=u["headers"])).json()) == 1
    assert (await client.delete(f"{base}/{r.json()['id']}", headers=support["headers"])).status_code == 403
    assert (await client.delete(f"/v1/vehicle-lists/{r.json()['id']}", headers=u["headers"])).status_code == 204



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
