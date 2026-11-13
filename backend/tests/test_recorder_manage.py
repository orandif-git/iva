from .test_recorder_onboarding import _onboard




async def test_connection_details_stay_available_and_never_include_the_key(client, make):
    t, u, cred = await _onboard(client, make)
    r = await client.get(f"/v1/recorders/{cred['recorder_id']}/connection", headers=u["headers"])
    assert r.status_code == 200 and r.json()["device_id"] == cred["device_id"] and cred["key"] not in r.text




async def test_another_customer_cannot_rename_or_delete_a_recorder(client, make):
    _, _, cred = await _onboard(client, make)
    _, other, _ = await _onboard(client, make)
    rid = cred["recorder_id"]
    assert (await client.patch(f"/v1/recorders/{rid}", json={"name": "x"}, headers=other["headers"])).status_code == 404
    assert (await client.delete(f"/v1/recorders/{rid}", headers=other["headers"])).status_code == 404
