"""The platform console: plans and their price policy, editing a customer's people, staff accounts, the attention list, customer health, audit search."""
import uuid
from datetime import date, timedelta


PLAN = {"name": "Console Test", "price_per_camera_paise": 100000, "stream_mode": "snapshots", "anpr": False, "review_queue": False, "reason": "New plan for testing"}


async def new_plan(client, owner, **kw):
    r = await client.post("/admin/v1/plans", json={**PLAN, "name": f"Plan {uuid.uuid4().hex[:5]}", **kw}, headers=hdr(owner))
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ plans


def hdr(staff):
    return staff["headers"]


# ------------------------------------------------------------------------------------------- staff accounts
async def test_the_owner_adds_staff_who_choose_their_own_password(client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    body = {"name": "Karan", "email": f"{uuid.uuid4().hex[:6]}@jasinfo.in", "role": "support"}
    assert (await client.post("/admin/v1/staff", json=body, headers=hdr(support))).status_code == 403
    assert (await client.get("/admin/v1/staff", headers=hdr(support))).status_code == 403
    r = await client.post("/admin/v1/staff", json=body, headers=hdr(owner))
    assert r.status_code == 201 and "/set-password?token=" in r.json()["link"]
    token = r.json()["link"].split("token=")[1]
    assert (await client.post("/admin/v1/staff", json=body, headers=hdr(owner))).json()["error"]["code"] == "email_in_use"
    assert (await client.post("/admin/v1/auth/login", json={"email": body["email"], "password": "whatever long password"})).status_code == 401
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": "short"})).json()["error"]["code"] == "weak_password"
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": "a good long password"})).status_code == 204
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": "another long password"})).json()["error"]["code"] == "link_invalid"   # works once
    login = await client.post("/admin/v1/auth/login", json={"email": body["email"], "password": "a good long password"})
    assert login.status_code == 200 and login.json()["user"]["role"] == "support"
    row = next(x for x in (await client.get("/admin/v1/staff", headers=hdr(owner))).json()["items"] if x["email"] == body["email"])
    assert row["has_password"] is True and "password_hash" not in row
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='staff.create' AND after->>'email'=$1", body["email"]) == 1


async def test_staff_rules_protect_the_last_owner_and_yourself(client, make, su):
    owner = await make.staff("owner")
    others = await su.fetchval("SELECT count(*) FROM platform.staff_users WHERE role='owner' AND status='active' AND id<>$1", owner["id"])
    for sid in await su.fetch("SELECT id FROM platform.staff_users WHERE role='owner' AND id<>$1", owner["id"]):
        await su.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", sid["id"])               # leave this one owner alone
    assert (await client.patch(f"/admin/v1/staff/{owner['id']}", json={"role": "support"}, headers=hdr(owner))).json()["error"]["code"] == "own_role"
    assert (await client.post(f"/admin/v1/staff/{owner['id']}/disable", params={"reason": "Testing it"}, headers=hdr(owner))).json()["error"]["code"] == "own_account"
    second = await make.staff("owner")
    assert (await client.patch(f"/admin/v1/staff/{second['id']}", json={"role": "support"}, headers=hdr(owner))).status_code == 200          # another owner exists, so this is fine
    assert (await client.patch(f"/admin/v1/staff/{second['id']}", json={"role": "owner"}, headers=hdr(owner))).status_code == 200
    s = await make.staff("support")
    assert (await client.post(f"/admin/v1/staff/{s['id']}/disable", params={"reason": "Left the team"}, headers=hdr(owner))).status_code == 204
    assert (await client.get("/admin/v1/me", headers=hdr(s))).status_code in (401, 403)                      # a disabled person is locked out at once
    assert (await client.post(f"/admin/v1/staff/{s['id']}/enable", params={"reason": "Back again"}, headers=hdr(owner))).status_code == 204
    link = await client.post(f"/admin/v1/staff/{s['id']}/reset-link", params={"reason": "Forgot password"}, headers=hdr(owner))
    assert link.status_code == 201 and "/set-password?token=" in link.json()["link"]
    del others


# ------------------------------------------------------------------------------------- the front page


async def test_the_audit_log_can_be_searched_and_limited_by_date(client, make):
    owner = await make.staff("owner")
    t = await make.tenant()
    await client.post(f"/admin/v1/tenants/{t}/suspend", json={"reason": "Unpaid for ninety days"}, headers=hdr(owner))
    got = (await client.get("/admin/v1/audit", params={"q": "ninety days"}, headers=hdr(owner))).json()["items"]
    assert got and all("ninety days" in (x["reason"] or "") for x in got)
    assert (await client.get("/admin/v1/audit", params={"q": "tenant.suspend", "since": (date.today() + timedelta(days=1)).isoformat() + "T00:00:00Z"}, headers=hdr(owner))).json()["items"] == []
    assert (await client.get("/admin/v1/audit", params={"q": "tenant.suspend", "since": (date.today() - timedelta(days=1)).isoformat() + "T00:00:00Z"}, headers=hdr(owner))).json()["items"]

