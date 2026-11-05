from .test_recorder_onboarding import _onboard


async def test_a_recorder_can_be_named_renamed_and_cleared(client, make):
    t, u, _ = await _onboard(client, make)
    r = await client.post("/v1/recorders", json={"brand": "dahua", "name": "Front office"}, headers=u["headers"])
    assert r.status_code == 201
    rid = r.json()["recorder_id"]
    assert (await client.get(f"/v1/recorders/{rid}", headers=u["headers"])).json()["name"] == "Front office"
    p = await client.patch(f"/v1/recorders/{rid}", json={"name": "Warehouse"}, headers=u["headers"])
    assert p.status_code == 200 and p.json()["name"] == "Warehouse"
    p = await client.patch(f"/v1/recorders/{rid}", json={"name": None}, headers=u["headers"])
    assert p.json()["name"] is None


async def test_connection_details_stay_available_and_never_include_the_key(client, make):
    t, u, cred = await _onboard(client, make)
    r = await client.get(f"/v1/recorders/{cred['recorder_id']}/connection", headers=u["headers"])
    assert r.status_code == 200 and r.json()["device_id"] == cred["device_id"] and cred["key"] not in r.text


async def test_deleting_a_recorder_removes_it_and_the_gateway_drops_it(client, make, su):
    t, u, cred = await _onboard(client, make)
    rid, dev = cred["recorder_id"], cred["device_id"]
    assert (await client.delete(f"/v1/recorders/{rid}", headers=u["headers"])).status_code == 204
    assert (await client.get(f"/v1/recorders/{rid}", headers=u["headers"])).status_code == 404
    assert (await client.get(f"/internal/v1/recorders/{dev}/credentials", headers=make.service())).status_code == 404
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='recorder.delete' AND target_id=$1", str(rid)) == 1


async def test_another_customer_cannot_rename_or_delete_a_recorder(client, make):
    _, _, cred = await _onboard(client, make)
    _, other, _ = await _onboard(client, make)
    rid = cred["recorder_id"]
    assert (await client.patch(f"/v1/recorders/{rid}", json={"name": "x"}, headers=other["headers"])).status_code == 404
    assert (await client.delete(f"/v1/recorders/{rid}", headers=other["headers"])).status_code == 404
