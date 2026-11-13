from datetime import timedelta

import pytest

from tests.helpers import iso, minute, now, setup_cameras


def fb(cam, ts, i, o):
    return {"camera_id": cam, "bucket_start": iso(ts), "people_in": i, "people_out": o}


async def post(client, make, events):
    r = await client.post("/internal/v1/events/footfall", json={"events": events}, headers=make.service())
    assert r.status_code == 200, r.text
    return r.json()


async def test_ingest_stores_counts_and_reports_each_bad_event(client, make, su):
    t, u, (foot,), (plate_only,) = await setup_cameras(client, make)
    base = minute(now()) - timedelta(minutes=10)
    other_tenant_cam = (await setup_cameras(client, make))[2][0]
    evs = [fb(foot, base, 3, 1),                                           # 0 fine
           fb("00000000-0000-0000-0000-000000000000", base, 1, 1),          # 1 unknown camera
           fb(plate_only, base, 1, 1),                                      # 2 camera not set up for footfall
           {**fb(foot, base, 1, 1), "bucket_start": iso(base + timedelta(seconds=20))},   # 3 not on the minute
           fb(foot, now() + timedelta(hours=1), 1, 1),                      # 4 future (also off-minute is fine: reason order)
           fb(foot, minute(now()) - timedelta(days=9), 1, 1)]               # 5 too old
    res = await post(client, make, evs)
    assert res["accepted"] == 1
    assert {r["index"]: r["reason"] for r in res["rejected"]} == {1: "unknown_camera", 2: "footfall_not_enabled", 3: "not_on_the_minute", 4: "in_future", 5: "too_old"}
    assert await su.fetchval("SELECT count(*) FROM app.footfall_counts WHERE camera_id=$1", __import__("uuid").UUID(foot)) == 1
    assert other_tenant_cam != foot


async def test_the_tenant_comes_from_the_camera_not_the_request(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await post(client, make, [fb(foot, minute(now()) - timedelta(minutes=5), 2, 2)])
    assert await su.fetchval("SELECT tenant_id FROM app.footfall_counts WHERE camera_id=$1", __import__("uuid").UUID(foot)) == t
    r = await client.post("/internal/v1/events/footfall", json={"events": [{**fb(foot, minute(now()), 1, 1), "tenant_id": "x"}]}, headers=make.service())
    assert r.status_code == 422                                              # unknown fields are refused


async def test_retrying_a_batch_never_double_counts(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    ts = minute(now()) - timedelta(minutes=5)
    for _ in range(3):
        await post(client, make, [fb(foot, ts, 4, 2)])
    await post(client, make, [fb(foot, ts, 6, 3)])                           # a later, fuller figure for the same minute wins
    await post(client, make, [fb(foot, ts, 1, 0)])                           # a stale smaller figure does not reduce it
    row = await su.fetchrow("SELECT people_in, people_out FROM app.footfall_counts WHERE camera_id=$1", __import__("uuid").UUID(foot))
    assert (row["people_in"], row["people_out"]) == (6, 3)


async def test_suspended_tenant_events_are_refused(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    res = await post(client, make, [fb(foot, minute(now()), 1, 1)])
    assert res["accepted"] == 0 and res["rejected"][0]["reason"] == "tenant_suspended"


async def test_ingestion_needs_the_service_token_and_a_sane_batch(client, make):
    t, u, (foot,), _ = await setup_cameras(client, make)
    assert (await client.post("/internal/v1/events/footfall", json={"events": [fb(foot, minute(now()), 1, 1)]}, headers=u["headers"])).status_code == 401
    assert (await client.post("/internal/v1/events/footfall", json={"events": []}, headers=make.service())).status_code == 422
    assert (await client.post("/internal/v1/events/footfall", json={"events": [fb(foot, minute(now()), 1, 1)] * 1001}, headers=make.service())).status_code == 422
    naive = {"camera_id": foot, "bucket_start": "2026-11-04T10:00:00", "people_in": 1, "people_out": 1}
    assert (await client.post("/internal/v1/events/footfall", json={"events": [naive]}, headers=make.service())).status_code == 422


async def test_ingestion_updates_the_cameras_last_frame_time(client, make, su):
    t, u, (foot,), _ = await setup_cameras(client, make)
    ts = minute(now()) - timedelta(minutes=2)
    await post(client, make, [fb(foot, ts, 1, 0)])
    assert await su.fetchval("SELECT last_frame_at FROM app.cameras WHERE id=$1", __import__("uuid").UUID(foot)) == ts
