import pytest

from app.security import issue_token


async def test_tenant_cannot_see_another_tenants_recorders(client, make):
    a, b = await make.tenant(), await make.tenant()
    ua, ub = await make.user(a), await make.user(b)
    r = await client.post("/v1/recorders", json={"brand": "hikvision"}, headers=ua["headers"])
    assert r.status_code == 201
    rid = r.json()["recorder_id"]
    assert [x["id"] for x in (await client.get("/v1/recorders", headers=ua["headers"])).json()] == [rid]
    assert (await client.get("/v1/recorders", headers=ub["headers"])).json() == []
    # Another tenant's id behaves exactly like a missing one, so ids cannot be probed.
    assert (await client.get(f"/v1/recorders/{rid}", headers=ub["headers"])).status_code == 404
    assert (await client.post(f"/v1/recorders/{rid}/rotate-key", headers=ub["headers"])).status_code == 404


async def test_token_audiences_do_not_mix(client, make):
    t = await make.tenant()
    u = await make.user(t)
    staff = await make.staff("owner")
    assert (await client.get("/admin/v1/me", headers=u["headers"])).status_code == 401      # customer token on admin API
    assert (await client.get("/v1/me", headers=staff["headers"])).status_code == 401        # staff token on customer API
    assert (await client.get("/admin/v1/me")).status_code == 401                            # no token
    assert (await client.get("/internal/v1/recorders/X/credentials", headers=u["headers"])).status_code == 401


async def test_staff_needs_mfa_and_must_be_active(client, make):
    no_mfa = await make.staff("owner", mfa=False)
    assert (await client.get("/admin/v1/me", headers=no_mfa["headers"])).json()["error"]["code"] == "mfa_required"
    gone = await make.staff("owner", status="disabled")
    assert (await client.get("/admin/v1/me", headers=gone["headers"])).status_code == 401


async def test_role_comes_from_database_not_token(client, make, su):
    s = await make.staff("support")
    assert (await client.post("/admin/v1/tenants", json={"name": "X Co", "plan_id": "quick"}, headers=s["headers"])).status_code == 403
    await su.execute("UPDATE platform.staff_users SET role='owner' WHERE id=$1", s["id"])
    assert (await client.post("/admin/v1/tenants", json={"name": "X Co", "plan_id": "quick"}, headers=s["headers"])).status_code == 201


async def test_regular_user_cannot_add_recorder_or_camera(client, make):
    t = await make.tenant()
    u = await make.user(t, role="regular")
    assert (await client.get("/v1/me", headers=u["headers"])).status_code == 200
    assert (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).status_code == 403


async def test_disabled_user_and_suspended_tenant_are_blocked(client, make):
    t = await make.tenant()
    gone = await make.user(t, status="disabled")
    assert (await client.get("/v1/me", headers=gone["headers"])).status_code == 401
    s = await make.tenant(status="suspended")
    u = await make.user(s)
    assert (await client.get("/v1/me", headers=u["headers"])).json()["error"]["code"] == "tenant_suspended"


async def test_validation_errors_use_the_standard_shape(client, make):
    t = await make.tenant()
    u = await make.user(t)
    r = await client.post("/v1/recorders", json={"brand": "sony", "extra": 1}, headers=u["headers"])
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["request_id"] and body["details"]
