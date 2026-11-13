import re

KEY_RE = re.compile(r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")


async def _onboard(client, make, plan="full", limit=5):
    t = await make.tenant(plan=plan, limit=limit)
    u = await make.user(t)
    r = await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])
    assert r.status_code == 201, r.text
    return t, u, r.json()


async def test_full_registration_flow(client, make, su):
    t, u, cred = await _onboard(client, make)
    assert r"no-store" in (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).headers["cache-control"]
    assert cred["server"] and cred["port"] == 7000 and KEY_RE.match(cred["key"])
    rid, dev = cred["recorder_id"], cred["device_id"]

    row = await su.fetchrow("SELECT status::text AS status, channel_count, preview_ok_at FROM app.recorders WHERE id=$1", rid)
    assert row["status"] == "never" and row["channel_count"] == 0 and row["preview_ok_at"] is None

    # The registrar fetches the key, then reports the recorder online with its channels.
    svc = make.service()
    assert (await client.get(f"/internal/v1/recorders/{dev}/credentials", headers=svc)).json()["key"] == cred["key"]
    hb = await client.post(f"/internal/v1/recorders/{dev}/heartbeat", headers=svc,
                           json={"ip": "49.205.118.40", "firmware": "V5.7", "channels": [{"channel_no": n, "name": f"Cam {n}"} for n in range(1, 5)]})
    assert hb.json() == {"allowed": True}

    row = await su.fetchrow("SELECT status::text AS status, channel_count, preview_ok_at FROM app.recorders WHERE id=$1", rid)
    assert row["status"] == "online" and row["channel_count"] == 4 and row["preview_ok_at"] is None
    await client.post(f"/internal/v1/recorders/{dev}/preview-ok", headers=svc)
    assert await su.fetchval("SELECT preview_ok_at IS NOT NULL FROM app.recorders WHERE id=$1", rid) is True

    chans = (await client.get(f"/v1/recorders/{rid}/channels", headers=u["headers"])).json()
    assert [c["channel_no"] for c in chans] == [1, 2, 3, 4] and not any(c["enabled_as_camera"] for c in chans)


async def test_key_is_encrypted_at_rest_and_never_returned_again(client, make, su):
    t, u, cred = await _onboard(client, make)
    raw = await su.fetchval("SELECT key_ciphertext FROM app.recorders WHERE id=$1", cred["recorder_id"])
    assert cred["key"].encode() not in bytes(raw) and cred["key"].replace("-", "").encode() not in bytes(raw)
    listed = (await client.get("/v1/recorders", headers=u["headers"])).json()
    assert listed and all("key" not in r and "key_ciphertext" not in r for r in listed)
    audit = await su.fetch("SELECT * FROM platform.audit_log WHERE target_id=$1", str(cred["recorder_id"]))
    assert audit and all(cred["key"] not in str(dict(a)) for a in audit)           # the key is never written to the audit log


async def test_ciphertext_is_bound_to_its_device(client, make, su):
    t, u, c1 = await _onboard(client, make)
    _, _, c2 = await _onboard(client, make)
    blob = await su.fetchval("SELECT key_ciphertext FROM app.recorders WHERE id=$1", c1["recorder_id"])
    await su.execute("UPDATE app.recorders SET key_ciphertext=$2 WHERE id=$1", c2["recorder_id"], blob)    # copy onto another row
    r = await client.get(f"/internal/v1/recorders/{c2['device_id']}/credentials", headers=make.service())
    assert r.status_code == 500                                                    # fails closed, no key released


async def test_rotate_key_changes_the_key(client, make):
    t, u, cred = await _onboard(client, make)
    new = (await client.post(f"/admin/v1/recorders/{cred['recorder_id']}/new-password", headers=u["headers"])).json()
    assert new["key"] != cred["key"]
    got = (await client.get(f"/internal/v1/recorders/{cred['device_id']}/credentials", headers=make.service())).json()
    assert got["key"] == new["key"]


async def test_device_ids_are_unique_across_tenants_with_similar_names(client, make):
    ids = []
    for name in ("Sharma Auto", "Sharma Steel", "Sharma Textiles"):          # all start with SHARMA
        t = await make.tenant(name=f"{name} {len(ids)}")
        u = await make.user(t)
        for _ in range(3):
            ids.append((await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()["device_id"])
    assert len(set(ids)) == 9
    assert all(i.startswith("SHARMA-") and len(i) == 11 for i in ids)


async def test_suspended_tenant_recorder_is_refused_by_registrar(client, make, su):
    t, u, cred = await _onboard(client, make)
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    svc = make.service()
    hb = await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=svc, json={"ip": "1.2.3.4", "channels": []})
    assert hb.json() == {"allowed": False}
    assert (await client.get(f"/internal/v1/recorders/{cred['device_id']}/credentials", headers=svc)).status_code == 403
    assert await su.fetchval("SELECT status::text FROM app.recorders WHERE id=$1", cred["recorder_id"]) == "never"


async def test_stale_recorders_are_marked_offline(client, make, su):
    from app.services.sweeper import mark_stale_recorders
    t, u, cred = await _onboard(client, make)
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.2.3.4", "channels": [{"channel_no": 1}]})
    await su.execute("UPDATE app.recorders SET last_heartbeat_at = now() - interval '5 minutes' WHERE id=$1", cred["recorder_id"])
    assert await mark_stale_recorders(90) >= 1
    assert await su.fetchval("SELECT status::text FROM app.recorders WHERE id=$1", cred["recorder_id"]) == "offline"


async def test_worker_can_fetch_a_cameras_config(client, make):
    from tests.helpers import setup_cameras
    t, u, (foot,), (plate,) = await setup_cameras(client, make)
    line = {"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}
    assert (await client.put(f"/v1/cameras/{foot}/zones", json={"zones": [line]}, headers=u["headers"])).status_code == 200
    cfg = (await client.get(f"/internal/v1/cameras/{foot}/config", headers=make.service())).json()
    assert cfg["active"] and cfg["use_footfall"] and not cfg["use_anpr"]
    assert cfg["frame_rate"] == {"mode": "adaptive", "idle_fps": 2, "active_fps": 5, "fixed_fps": 5, "capture_fps": 5,
                                 "from": {"mode": "tenant", "idle": "tenant", "active": "tenant", "fixed": "tenant"}}
    assert cfg["zones"] == [{"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}]
    assert (await client.get(f"/internal/v1/cameras/{foot}/config", headers=u["headers"])).status_code == 401       # not for customers
    assert (await client.get("/internal/v1/cameras/00000000-0000-0000-0000-000000000000/config", headers=make.service())).status_code == 404
    from app.db import db
    async with db.service_tx() as c:
        await c.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await client.get(f"/internal/v1/cameras/{foot}/config", headers=make.service())).json()["active"] is False
