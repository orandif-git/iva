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


async def test_hourly_series_fills_empty_hours_with_zero(client, make):
    t, u, (foot,), _ = await setup_cameras(client, make)
    h0 = minute(now()).replace(minute=0) - timedelta(hours=3)
    await post(client, make, [fb(foot, h0 + timedelta(minutes=5), 3, 1), fb(foot, h0 + timedelta(minutes=40), 2, 2), fb(foot, h0 + timedelta(hours=2, minutes=1), 5, 0)])
    r = (await client.get("/v1/footfall", params={"from": iso(h0), "to": iso(h0 + timedelta(hours=4)), "tz": "UTC"}, headers=u["headers"])).json()
    assert [(p["people_in"], p["people_out"]) for p in r["items"]] == [(5, 3), (0, 0), (5, 0), (0, 0)]
    assert r["interval"] == "hour" and len(r["items"]) == 4


async def test_days_are_cut_at_midnight_in_the_chosen_timezone(client, make):
    t, u, (foot,), _ = await setup_cameras(client, make)
    midnight_ist = (now() - timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)      # 00:00 IST
    await post(client, make, [fb(foot, midnight_ist - timedelta(minutes=1), 7, 0), fb(foot, midnight_ist, 2, 0)])
    params = {"from": iso(midnight_ist - timedelta(days=1)), "to": iso(midnight_ist + timedelta(days=1)), "interval": "day"}
    ist = (await client.get("/v1/footfall", params={**params, "tz": "Asia/Kolkata"}, headers=u["headers"])).json()["items"]
    assert len(ist) == 2 and [p["people_in"] for p in ist] == [7, 2]                         # split across two Indian days...
    utc = [p["people_in"] for p in (await client.get("/v1/footfall", params={**params, "tz": "UTC"}, headers=u["headers"])).json()["items"] if p["people_in"]]
    assert utc == [9]                                                        # ...but one UTC day


async def test_camera_filter_and_tenant_isolation(client, make):
    t, u, (f1, f2), _ = await setup_cameras(client, make, footfall=2)
    t2, u2, (g1,), _ = await setup_cameras(client, make)
    ts = minute(now()) - timedelta(minutes=30)
    await post(client, make, [fb(f1, ts, 10, 0), fb(f2, ts, 4, 0), fb(g1, ts, 99, 99)])
    rng = {"from": iso(ts - timedelta(hours=1)), "to": iso(ts + timedelta(hours=1)), "tz": "UTC"}
    tot = lambda r: sum(p["people_in"] for p in r.json()["items"])
    assert tot(await client.get("/v1/footfall", params=rng, headers=u["headers"])) == 14
    assert tot(await client.get("/v1/footfall", params={**rng, "camera_id": f2}, headers=u["headers"])) == 4
    assert tot(await client.get("/v1/footfall", params={**rng, "camera_id": g1}, headers=u["headers"])) == 0       # another tenant's camera: nothing
    assert tot(await client.get("/v1/footfall", params=rng, headers=u2["headers"])) == 99


async def test_summary_has_totals_peak_hour_and_per_camera(client, make):
    t, u, (f1, f2), _ = await setup_cameras(client, make, footfall=2)
    h = minute(now()).replace(minute=0) - timedelta(hours=5)
    await post(client, make, [fb(f1, h + timedelta(minutes=1), 5, 2), fb(f1, h + timedelta(hours=1, minutes=1), 20, 1), fb(f2, h + timedelta(hours=1, minutes=2), 3, 4)])
    s = (await client.get("/v1/footfall/summary", params={"from": iso(h - timedelta(hours=1)), "to": iso(h + timedelta(hours=4)), "tz": "UTC"}, headers=u["headers"])).json()
    assert (s["people_in"], s["people_out"], s["net"]) == (28, 7, 21)
    assert s["peak_hour"]["people_in"] == 23
    assert {c["name"]: c["people_in"] for c in s["cameras"]} == {"Cam 1": 25, "Cam 2": 3}
    empty = (await client.get("/v1/footfall/summary", params={"from": iso(h - timedelta(days=3)), "to": iso(h - timedelta(days=2))}, headers=u["headers"])).json()
    assert empty["peak_hour"] is None and empty["people_in"] == 0


@pytest.mark.parametrize("params,code", [
    ({"from": "2026-11-04T10:00:00Z", "to": "2026-11-04T09:00:00Z"}, "bad_range"),
    ({"from": "2026-11-04T10:00:00", "to": "2026-11-04T11:00:00Z"}, "timezone_required"),
    ({"from": "2026-01-01T00:00:00Z", "to": "2026-03-01T00:00:00Z"}, "range_too_large"),
    ({"from": "2026-11-04T00:00:00Z", "to": "2026-11-05T00:00:00Z", "tz": "Mars/Base"}, "unknown_timezone"),
])
async def test_bad_queries_are_explained(client, make, params, code):
    t = await make.tenant()
    u = await make.user(t)
    r = await client.get("/v1/footfall", params=params, headers=u["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == code


async def test_view_as_customer_can_read_footfall(client, make):
    t, u, (foot,), _ = await setup_cameras(client, make)
    staff = await make.staff("support")
    imp = (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer reports wrong counts", "minutes": 15}, headers=staff["headers"])).json()
    r = await client.get("/v1/footfall/summary", params={"from": iso(now() - timedelta(hours=1)), "to": iso(now())}, headers={"Authorization": "Bearer " + imp["token"]})
    assert r.status_code == 200
