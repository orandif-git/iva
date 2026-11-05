async def _online(client, make, plan="full", limit=5, channels=6):
    t = await make.tenant(plan=plan, limit=limit)
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "hikvision"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(),
                      json={"ip": "1.2.3.4", "channels": [{"channel_no": n, "name": f"C{n}"} for n in range(1, channels + 1)]})
    return t, u, cred["recorder_id"]


def body(rid, n, **kw):
    return {"recorder_id": rid, "channel_no": n, "name": f"Camera {n}", "use_footfall": True, **kw}


async def test_camera_limit_is_enforced(client, make):
    t, u, rid = await _online(client, make, limit=2)
    assert (await client.post("/v1/cameras", json=body(rid, 1), headers=u["headers"])).status_code == 201
    assert (await client.post("/v1/cameras", json=body(rid, 2), headers=u["headers"])).status_code == 201
    r = await client.post("/v1/cameras", json=body(rid, 3), headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "camera_limit_reached"


async def test_anpr_needs_a_plan_that_includes_it(client, make):
    t, u, rid = await _online(client, make, plan="quick")
    r = await client.post("/v1/cameras", json=body(rid, 1, use_anpr=True), headers=u["headers"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_feature_missing"
    t2, u2, rid2 = await _online(client, make, plan="full")
    assert (await client.post("/v1/cameras", json=body(rid2, 1, use_anpr=True), headers=u2["headers"])).status_code == 201


async def test_channel_must_exist_and_cannot_be_added_twice(client, make):
    t, u, rid = await _online(client, make, channels=2)
    assert (await client.post("/v1/cameras", json=body(rid, 9), headers=u["headers"])).json()["error"]["code"] == "channel_not_found"
    assert (await client.post("/v1/cameras", json=body(rid, 1), headers=u["headers"])).status_code == 201
    assert (await client.post("/v1/cameras", json=body(rid, 1), headers=u["headers"])).json()["error"]["code"] == "camera_exists"


async def test_a_camera_needs_at_least_one_use(client, make):
    t, u, rid = await _online(client, make)
    r = await client.post("/v1/cameras", json={"recorder_id": rid, "channel_no": 1, "name": "X"}, headers=u["headers"])
    assert r.status_code == 422


async def test_cannot_attach_a_camera_to_another_tenants_recorder(client, make):
    t1, u1, rid1 = await _online(client, make)
    t2 = await make.tenant()
    u2 = await make.user(t2)
    r = await client.post("/v1/cameras", json=body(rid1, 1), headers=u2["headers"])
    assert r.status_code == 404


async def test_zones_are_validated_and_replaced(client, make):
    t, u, rid = await _online(client, make)
    cam = (await client.post("/v1/cameras", json=body(rid, 1, use_anpr=True), headers=u["headers"])).json()
    url = f"/v1/cameras/{cam['id']}/zones"
    line = {"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}
    zone = {"kind": "anpr_zone", "points": [[0.2, 0.6], [0.8, 0.6], [0.9, 0.95], [0.1, 0.95]]}
    assert (await client.put(url, json={"zones": [line, zone]}, headers=u["headers"])).status_code == 200
    assert {z["kind"] for z in (await client.get(url, headers=u["headers"])).json()} == {"count_line", "anpr_zone"}
    assert (await client.put(url, json={"zones": [{"kind": "count_line", "points": [[0, 0], [1, 1], [1, 0]]}]}, headers=u["headers"])).json()["error"]["code"] == "bad_line"
    assert (await client.put(url, json={"zones": [{"kind": "anpr_zone", "points": [[0, 0], [2, 1], [1, 0]]}]}, headers=u["headers"])).status_code == 422   # outside 0..1
    assert (await client.put(url, json={"zones": [line, line]}, headers=u["headers"])).json()["error"]["code"] == "duplicate_zone"
    assert len((await client.put(url, json={"zones": [line]}, headers=u["headers"])).json()) == 1               # replaced, not appended
