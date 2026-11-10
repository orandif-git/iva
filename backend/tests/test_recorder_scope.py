"""Which recorders can be added, and by whom. Customers add Dahua recorders (Auto Register) only. Staff can also add GB28181 recorders
and the "other" brand. Hikvision (ISUP) is not supported by anyone: those recorders stay on Hik-Connect."""
import pytest


async def _count(su, tenant):
    return await su.fetchval("SELECT count(*) FROM app.recorders WHERE tenant_id=$1", tenant)


async def test_a_customer_adds_a_dahua_recorder_with_auto_register(client, make):
    u = await make.user(await make.tenant())
    r = await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])
    assert r.status_code == 201 and r.json()["protocol"] == "auto_register" and r.json()["port"] == 7000
    again = await client.post("/v1/recorders", json={"brand": "dahua", "protocol": "auto_register"}, headers=u["headers"])
    assert again.status_code == 201


@pytest.mark.parametrize("body", [{"brand": "hikvision"}, {"brand": "hikvision", "protocol": "isup"}, {"brand": "dahua", "protocol": "isup"}])
async def test_hikvision_and_isup_are_refused_for_customers(client, make, su, body):
    t = await make.tenant()
    u = await make.user(t)
    r = await client.post("/v1/recorders", json=body, headers=u["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_supported"
    assert await _count(su, t) == 0


@pytest.mark.parametrize("body", [{"brand": "other"}, {"brand": "other", "protocol": "gb28181"}, {"brand": "dahua", "protocol": "gb28181"}])
async def test_customers_cannot_add_other_brands_or_gb28181(client, make, su, body):
    t = await make.tenant()
    u = await make.user(t)
    r = await client.post("/v1/recorders", json=body, headers=u["headers"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "staff_only"
    assert await _count(su, t) == 0


async def test_staff_can_add_gb28181_and_other_brand_recorders_and_it_is_audited(client, make, su):
    t = await make.tenant()
    tech = await make.staff("owner")
    url = f"/admin/v1/tenants/{t}/recorders"
    r = await client.post(url, json={"brand": "other", "protocol": "gb28181", "name": "Store room", "reason": "Customer has a non-Dahua recorder"}, headers=tech["headers"])
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    out = r.json()
    assert out["brand"] == "other" and out["protocol"] == "gb28181" and out["server_id"] and out["key"]
    assert await su.fetchval("SELECT tenant_id FROM app.recorders WHERE id=$1", out["recorder_id"]) == t

    audit = await su.fetch("SELECT * FROM platform.audit_log WHERE target_id=$1 AND action='recorder.admin_register'", str(out["recorder_id"]))
    assert len(audit) == 1 and audit[0]["reason"] == "Customer has a non-Dahua recorder" and audit[0]["tenant_id"] == t
    assert out["key"] not in str(dict(audit[0]))                         # the key is never written to the audit log

    dahua_gb = await client.post(url, json={"brand": "dahua", "protocol": "gb28181", "reason": "This model has no Auto Register page"}, headers=tech["headers"])
    assert dahua_gb.status_code == 201 and dahua_gb.json()["protocol"] == "gb28181"
    default = await client.post(url, json={"brand": "dahua", "reason": "Added on the customer's behalf"}, headers=tech["headers"])
    assert default.status_code == 201 and default.json()["protocol"] == "auto_register" and default.json()["viewer_user"]


async def test_staff_cannot_add_hikvision_either(client, make, su):
    t = await make.tenant()
    tech = await make.staff("owner")
    for body in ({"brand": "hikvision"}, {"brand": "other", "protocol": "isup"}):
        r = await client.post(f"/admin/v1/tenants/{t}/recorders", json={**body, "reason": "Customer asked for Hikvision"}, headers=tech["headers"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "not_supported"
    assert await _count(su, t) == 0


async def test_adding_for_a_customer_needs_an_owner_a_reason_and_an_active_customer(client, make):
    t = await make.tenant()
    owner, support = await make.staff("owner"), await make.staff("support")
    url = f"/admin/v1/tenants/{t}/recorders"
    body = {"brand": "dahua", "protocol": "gb28181", "reason": "Customer called support"}
    assert (await client.post(url, json=body, headers=support["headers"])).status_code == 403
    assert (await client.post(url, json={"brand": "dahua", "protocol": "gb28181"}, headers=owner["headers"])).status_code == 422      # a reason is required
    assert (await client.post(url, json=body)).status_code in (401, 403)                                                                # no sign-in
    gone = await client.post("/admin/v1/tenants/00000000-0000-0000-0000-000000000000/recorders", json=body, headers=owner["headers"])
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "tenant_not_found"
    suspended = await make.tenant(status="suspended")
    r = await client.post(f"/admin/v1/tenants/{suspended}/recorders", json=body, headers=owner["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "tenant_suspended"


async def test_an_old_isup_recorder_row_is_still_listed_and_does_not_break_the_connection_screen(client, make, su):
    t = await make.tenant()
    u = await make.user(t)
    rid = await su.fetchval(
        """INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at)
           VALUES ($1,'OLD-ISUP1','hikvision'::app.recorder_brand,'isup'::app.recorder_protocol,$2,'local',now()) RETURNING id""", t, b"x")
    listed = await client.get("/v1/recorders", headers=u["headers"])
    assert listed.status_code == 200 and [x["id"] for x in listed.json()] == [str(rid)] and listed.json()[0]["brand"] == "hikvision"
    r = await client.get(f"/v1/recorders/{rid}/connection", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "protocol_not_supported"
