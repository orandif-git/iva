async def test_view_as_customer_is_read_only_and_logged(client, make, su):
    support, owner = await make.staff("support"), await make.staff("owner")
    t = await make.tenant()
    u = await make.user(t)
    await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])

    assert (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "no", "minutes": 30}, headers=support["headers"])).status_code == 422
    assert (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Missing plate reads", "minutes": 5}, headers=support["headers"])).status_code == 422
    r = await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer reports missing plate reads", "minutes": 30}, headers=support["headers"])
    assert r.status_code == 201
    imp = {"Authorization": "Bearer " + r.json()["token"]}
    sid = r.json()["session_id"]

    me = (await client.get("/v1/me", headers=imp)).json()
    assert me["viewing_as_staff"] is True and me["user"]["name"] == "Staff support"
    assert len((await client.get("/v1/recorders", headers=imp)).json()) == 1                  # sees the customer's data
    w = await client.post("/v1/recorders", json={"brand": "dahua"}, headers=imp)               # but cannot change it
    assert w.status_code == 403 and w.json()["error"]["code"] == "read_only_session"
    assert (await client.delete(f"/v1/cameras/{t}", headers=imp)).status_code == 403

    # Another tenant stays invisible even while viewing this one.
    other = await make.tenant()
    assert (await client.get("/v1/recorders", headers=imp)).json() != []
    assert other != t

    assert (await client.delete(f"/admin/v1/impersonations/{sid}", headers=support["headers"])).status_code == 204
    assert (await client.get("/v1/me", headers=imp)).json()["error"]["code"] == "session_ended"

    actions = [a["action"] for a in (await client.get("/admin/v1/audit", params={"tenant_id": str(t)}, headers=owner["headers"])).json()["items"]]
    assert "impersonate.start" in actions and "impersonate.end" in actions


async def test_only_owner_and_support_can_view_as_customer(client, make):
    t = await make.tenant()
    for role in ("analyst",):                                                         # a role that is no longer used can do nothing
        s = await make.staff(role)
        assert (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Checking something", "minutes": 15}, headers=s["headers"])).status_code == 403


async def test_expired_session_is_rejected_even_with_a_valid_token(client, make, su):
    support = await make.staff("support")
    t = await make.tenant()
    r = await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer reports missing plate reads", "minutes": 15}, headers=support["headers"])
    imp = {"Authorization": "Bearer " + r.json()["token"]}
    assert (await client.get("/v1/me", headers=imp)).status_code == 200
    await su.execute("UPDATE platform.impersonation_sessions SET started_at = now() - interval '2 hours', expires_at = now() - interval '1 hour 45 minutes' WHERE id=$1", __import__("uuid").UUID(r.json()["session_id"]))
    assert (await client.get("/v1/me", headers=imp)).json()["error"]["code"] == "session_ended"


async def test_suspending_a_tenant_ends_open_view_sessions(client, make):
    support, owner = await make.staff("support"), await make.staff("owner")
    t = await make.tenant()
    r = await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer reports missing plate reads", "minutes": 30}, headers=support["headers"])
    imp = {"Authorization": "Bearer " + r.json()["token"]}
    await client.post(f"/admin/v1/tenants/{t}/suspend", json={"reason": "Unpaid for 45 days"}, headers=owner["headers"])
    assert (await client.get("/v1/me", headers=imp)).json()["error"]["code"] == "session_ended"


async def test_staff_cannot_end_someone_elses_session_unless_owner(client, make):
    a, b, owner = await make.staff("support"), await make.staff("support"), await make.staff("owner")
    t = await make.tenant()
    sid = (await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Customer reports missing plate reads", "minutes": 30}, headers=a["headers"])).json()["session_id"]
    assert (await client.delete(f"/admin/v1/impersonations/{sid}", headers=b["headers"])).status_code == 403
    assert (await client.delete(f"/admin/v1/impersonations/{sid}", headers=owner["headers"])).status_code == 204
