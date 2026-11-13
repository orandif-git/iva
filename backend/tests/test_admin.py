import asyncpg
import pytest

from app.security import issue_token


async def test_create_tenant_and_audit(client, make, su):
    owner = await make.staff("owner")
    r = await client.post("/admin/v1/tenants", json={"name": "Karur Textile Mills", "plan_id": "full", "camera_limit": 10}, headers=owner["headers"])
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "trial" and t["trial_ends_at"] and t["slug"] == "karur-textile-mills"
    again = await client.post("/admin/v1/tenants", json={"name": "Karur Textile Mills", "plan_id": "full"}, headers=owner["headers"])
    assert again.status_code == 201 and again.json()["slug"] != t["slug"]                    # slug clash resolved
    row = await su.fetchrow("SELECT * FROM platform.audit_log WHERE action='tenant.create' AND tenant_id=$1", __import__("uuid").UUID(t["id"]))
    assert row["actor_role"] == "owner"


async def test_suspend_requires_owner_and_a_reason_and_blocks_customers(client, make):
    owner, finance = await make.staff("owner"), await make.staff("onboarding")
    t = await make.tenant()
    u = await make.user(t)
    url = f"/admin/v1/tenants/{t}/suspend"
    assert (await client.post(url, json={"reason": "Unpaid for 45 days"}, headers=finance["headers"])).status_code == 403
    assert (await client.post(url, json={"reason": "no"}, headers=owner["headers"])).status_code == 422
    assert (await client.post(url, json={"reason": "Unpaid for 45 days"}, headers=owner["headers"])).json()["status"] == "suspended"
    assert (await client.post(url, json={"reason": "Unpaid for 45 days"}, headers=owner["headers"])).status_code == 409
    assert (await client.get("/v1/me", headers=u["headers"])).json()["error"]["code"] == "tenant_suspended"
    assert (await client.post(f"/admin/v1/tenants/{t}/reactivate", json={"reason": "Paid in full"}, headers=owner["headers"])).json()["status"] == "active"
    assert (await client.get("/v1/me", headers=u["headers"])).status_code == 200


async def test_plan_change_blocked_while_a_removed_feature_is_in_use(client, make):
    owner = await make.staff("owner")
    t = await make.tenant(plan="full")
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.1.1.1", "channels": [{"channel_no": 1}]})
    await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 1, "name": "Gate", "use_anpr": True}, headers=u["headers"])
    r = await client.post(f"/admin/v1/tenants/{t}/plan", json={"plan_id": "quick", "reason": "Customer asked to downgrade"}, headers=owner["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_feature_in_use"


async def test_lowering_limit_below_usage_is_refused(client, make):
    owner = await make.staff("owner")
    t = await make.tenant(limit=5)
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.1.1.1", "channels": [{"channel_no": 1}, {"channel_no": 2}]})
    for n in (1, 2):
        await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": n, "name": f"C{n}", "use_footfall": True}, headers=u["headers"])
    r = await client.patch(f"/admin/v1/tenants/{t}", json={"camera_limit": 1}, headers=owner["headers"])
    assert r.json()["error"]["code"] == "limit_below_usage"


async def test_tenant_list_filters_and_pages(client, make):
    owner = await make.staff("owner")
    for i in range(3):
        await make.tenant(name=f"Pagetest {i}")
    seen, cursor = [], None
    while True:
        r = (await client.get("/admin/v1/tenants", params={"q": "Pagetest", "limit": 2, **({"cursor": cursor} if cursor else {})}, headers=owner["headers"])).json()
        seen += [t["name"] for t in r["items"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == ["Pagetest 0", "Pagetest 1", "Pagetest 2"]


async def test_overview_hides_revenue_from_support(client, make):
    assert (await client.get("/admin/v1/overview", headers=(await make.staff("support"))["headers"])).json()["mrr_paise"] is None
    assert isinstance((await client.get("/admin/v1/overview", headers=(await make.staff("owner"))["headers"])).json()["mrr_paise"], int)


async def test_staff_can_look_at_recorders_but_cannot_change_them(client, make):
    from .test_recorder_onboarding import _onboard
    support, owner = await make.staff("support"), await make.staff("owner")
    t, u, cred = await _onboard(client, make)
    rid = cred["recorder_id"]
    rows = (await client.get("/admin/v1/recorders", params={"tenant_id": str(t)}, headers=support["headers"])).json()
    assert [r["device_id"] for r in rows] == [cred["device_id"]]
    conn = (await client.get(f"/admin/v1/recorders/{rid}/connection", headers=support["headers"])).json()
    assert conn["device_id"] == cred["device_id"] and cred["key"] not in str(conn)
    for who in (support, owner):                                                      # recorders belong to the customer, not to staff
        assert (await client.post("/admin/v1/recorders", json={"tenant_id": str(t), "brand": "dahua"}, headers=who["headers"])).status_code == 405
        assert (await client.delete(f"/admin/v1/recorders/{rid}", params={"reason": "Customer left"}, headers=who["headers"])).status_code == 405
        assert (await client.patch(f"/admin/v1/recorders/{rid}", json={"name": "x"}, headers=who["headers"])).status_code == 405
        assert (await client.post(f"/admin/v1/recorders/{rid}/rotate-key", json={"reason": "Lost it"}, headers=who["headers"])).status_code in (404, 405)


async def test_audit_log_cannot_be_changed_even_by_the_admin_role(client, make, su):
    owner = await make.staff("owner")
    await client.post("/admin/v1/tenants", json={"name": "Audit Probe", "plan_id": "quick"}, headers=owner["headers"])
    from app.db import db
    async with db.admin_tx() as c:
        with pytest.raises(asyncpg.PostgresError):
            await c.execute("UPDATE platform.audit_log SET action='x'")
    async with db.admin_tx() as c:
        with pytest.raises(asyncpg.PostgresError):
            await c.execute("DELETE FROM platform.audit_log")
    with pytest.raises(asyncpg.PostgresError):                                          # even the superuser is stopped by the trigger
        await su.execute("DELETE FROM platform.audit_log")
