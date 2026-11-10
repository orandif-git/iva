"""The platform console: plans and their price policy, editing a customer's people, staff accounts, the attention list, customer health, audit search."""
import uuid
from datetime import date, timedelta

from app.config import get_settings
from tests.test_billing import M, gen, hdr, invoice_of, tenant, use

PLAN = {"name": "Console Test", "price_per_camera_paise": 100000, "stream_mode": "snapshots", "anpr": False, "review_queue": False, "reason": "New plan for testing"}


async def new_plan(client, owner, **kw):
    r = await client.post("/admin/v1/plans", json={**PLAN, "name": f"Plan {uuid.uuid4().hex[:5]}", **kw}, headers=hdr(owner))
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ plans
async def test_only_the_owner_manages_plans_and_everyone_can_see_them(client, make):
    owner, support = await make.staff("owner"), await make.staff("support")
    assert (await client.post("/admin/v1/plans", json=PLAN, headers=hdr(support))).status_code == 403
    p = await new_plan(client, owner, price_per_camera_paise=123400, anpr=True)
    assert p["price_per_camera_paise"] == 123400 and p["anpr"] is True and p["customers"] == 0 and p["active"] is True
    assert (await client.put(f"/admin/v1/plans/{p['id']}", json={"price_per_camera_paise": 1, "reason": "Trying"}, headers=hdr(support))).status_code == 403
    assert (await client.delete(f"/admin/v1/plans/{p['id']}", params={"reason": "Trying it"}, headers=hdr(support))).status_code == 403
    assert p["id"] in {x["id"] for x in (await client.get("/admin/v1/plans", headers=hdr(support))).json()["items"]}
    assert (await client.post("/admin/v1/plans", json={**PLAN, "reason": "no"}, headers=hdr(owner))).status_code == 422          # a reason is required


async def test_a_plan_with_customers_is_retired_not_deleted(client, make, su):
    owner = await make.staff("owner")
    p = await new_plan(client, owner)
    t = await make.tenant(plan=p["id"])
    r = await client.delete(f"/admin/v1/plans/{p['id']}", params={"reason": "Not wanted any more"}, headers=hdr(owner))
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_in_use"
    assert (await client.put(f"/admin/v1/plans/{p['id']}", json={"active": False, "reason": "Retiring it"}, headers=hdr(owner))).json()["active"] is False
    assert p["id"] not in {x["id"] for x in (await client.get("/admin/v1/plans", headers=hdr(owner))).json()["items"]}                 # hidden from new customers
    assert p["id"] in {x["id"] for x in (await client.get("/admin/v1/plans", params={"include_retired": "true"}, headers=hdr(owner))).json()["items"]}
    assert (await client.post("/admin/v1/tenants", json={"name": "Late Joiner", "plan_id": p["id"]}, headers=hdr(owner))).status_code == 422
    assert await su.fetchval("SELECT plan_id FROM app.tenants WHERE id=$1", t) == p["id"]                                                # current customers keep it
    await su.execute("DELETE FROM app.tenants WHERE id=$1", t)
    assert (await client.delete(f"/admin/v1/plans/{p['id']}", params={"reason": "Nobody on it now"}, headers=hdr(owner))).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM platform.plans WHERE id=$1", p["id"]) == 0
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action IN ('plan.create','plan.update','plan.delete') AND (after->>'id' = $1 OR before->>'id' = $1)", p["id"]) == 3


async def test_a_plan_cannot_lose_number_plates_while_cameras_use_them(client, make, su):
    owner = await make.staff("owner")
    p = await new_plan(client, owner, anpr=True)
    t = await make.tenant(plan=p["id"])
    rec = await su.fetchval("INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at) VALUES ($1,$2,'dahua','auto_register',$3,'local',now()) RETURNING id", t, f"G-{uuid.uuid4().hex[:6]}", b"x")
    await su.execute("INSERT INTO app.cameras (tenant_id, recorder_id, channel_no, name, use_anpr) VALUES ($1,$2,1,'Gate',true)", t, rec)
    r = await client.put(f"/admin/v1/plans/{p['id']}", json={"anpr": False, "reason": "Simplify plans"}, headers=hdr(owner))
    assert r.status_code == 409 and r.json()["error"]["code"] == "plan_feature_in_use"


async def test_a_price_change_can_move_everyone_at_the_next_invoice(client, make, su):
    owner = await make.staff("owner")
    p = await new_plan(client, owner, price_per_camera_paise=100000, price_change_policy="next")
    t = await tenant(make, su, plan=p["id"])
    r = await client.put(f"/admin/v1/plans/{p['id']}", json={"price_per_camera_paise": 150000, "reason": "Yearly review"}, headers=hdr(owner))
    assert r.json()["price_per_camera_paise"] == 150000
    assert await su.fetchval("SELECT price_override_paise FROM app.tenants WHERE id=$1", t) is None
    await use(su, t, 2, 31, plan=p["id"])
    await gen(client, owner, t=t)
    line = await su.fetchrow("SELECT unit_paise, amount_paise FROM app.invoice_lines WHERE invoice_id=$1", (await invoice_of(su, t))["id"])
    assert line["unit_paise"] == 150000 and line["amount_paise"] == 300000


async def test_a_price_change_can_leave_existing_customers_on_their_old_price(client, make, su):
    owner = await make.staff("owner")
    p = await new_plan(client, owner, price_per_camera_paise=100000, price_change_policy="keep")
    old, other = await tenant(make, su, plan=p["id"]), await make.tenant(plan="quick")
    r = await client.put(f"/admin/v1/plans/{p['id']}", json={"price_per_camera_paise": 150000, "reason": "Yearly review"}, headers=hdr(owner))
    assert r.json()["customers_on_old_price"] == 1
    assert await su.fetchval("SELECT price_override_paise FROM app.tenants WHERE id=$1", old) == 100000
    assert await su.fetchval("SELECT price_override_paise FROM app.tenants WHERE id=$1", other) is None
    new = await tenant(make, su, plan=p["id"])                                                           # a customer who joins later pays the new price
    await use(su, old, 2, 31, plan=p["id"])
    await use(su, new, 2, 31, plan=p["id"])
    await gen(client, owner, t=old)
    await gen(client, owner, t=new)
    assert (await su.fetchrow("SELECT unit_paise FROM app.invoice_lines WHERE invoice_id=$1", (await invoice_of(su, old))["id"]))["unit_paise"] == 100000
    assert (await su.fetchrow("SELECT unit_paise FROM app.invoice_lines WHERE invoice_id=$1", (await invoice_of(su, new))["id"]))["unit_paise"] == 150000
    assert (await client.get(f"/admin/v1/tenants/{old}", headers=hdr(owner))).json()["price_override_paise"] == 100000
    # moving them to another plan ends the kept price; and a later "move everyone" change ends it for the rest
    await client.post(f"/admin/v1/tenants/{old}/plan", json={"plan_id": "quick", "reason": "They asked to move"}, headers=hdr(owner))
    assert await su.fetchval("SELECT price_override_paise FROM app.tenants WHERE id=$1", old) is None


# --------------------------------------------------------------------------------------- a customer's people
async def person(client, owner, t, email=None, role="admin"):
    r = await client.post(f"/admin/v1/tenants/{t}/users", json={"email": email or f"{uuid.uuid4().hex[:6]}@shop.test", "name": "Meena", "role": role}, headers=hdr(owner))
    assert r.status_code == 201, r.text
    return r.json()["user_id"]


async def test_the_owner_edits_disables_enables_and_deletes_a_person_with_reasons(client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    t = await make.tenant()
    a, b = await person(client, owner, t, role="admin"), await person(client, owner, t, role="regular")
    url = f"/admin/v1/tenants/{t}/users/{b}"
    assert (await client.patch(url, json={"name": "Dev"}, headers=hdr(support))).status_code == 403
    r = await client.patch(url, json={"name": "Dev Patel", "email": "Dev.Patel@Shop.test", "role": "admin"}, headers=hdr(owner))
    assert r.status_code == 200 and r.json()["name"] == "Dev Patel" and r.json()["email"] == "dev.patel@shop.test" and r.json()["role"] == "admin"
    assert (await client.patch(url, json={}, headers=hdr(owner))).status_code == 422
    assert (await client.patch(f"/admin/v1/tenants/{t}/users/{a}", json={"email": "dev.patel@shop.test"}, headers=hdr(owner))).json()["error"]["code"] == "email_in_use"
    assert (await client.post(f"{url}/disable", params={"reason": "Left the company"}, headers=hdr(owner))).status_code == 204
    assert await su.fetchval("SELECT status FROM app.tenant_users WHERE id=$1", uuid.UUID(b)) == "disabled"
    assert (await client.post(f"{url}/enable", params={"reason": "Came back"}, headers=hdr(owner))).status_code == 204
    assert await su.fetchval("SELECT status FROM app.tenant_users WHERE id=$1", uuid.UUID(b)) == "invited"          # no password yet, so still waiting for the link
    assert (await client.post(f"{url}/enable", headers=hdr(owner))).status_code == 409
    assert (await client.delete(url, headers=hdr(owner))).status_code == 422                                          # a reason is required
    assert (await client.delete(url, params={"reason": "Left the company"}, headers=hdr(support))).status_code == 403
    assert (await client.delete(url, params={"reason": "Left the company"}, headers=hdr(owner))).status_code == 204
    assert (await client.delete(url, params={"reason": "Left the company"}, headers=hdr(owner))).status_code == 404
    acts = {x["action"]: x["reason"] for x in await su.fetch("SELECT action, reason FROM platform.audit_log WHERE tenant_id=$1 AND action LIKE 'user.%'", t)}
    assert acts["user.disable"] == "Left the company" and acts["user.delete"] == "Left the company" and "user.update" in acts


async def test_the_last_administrator_cannot_be_removed_or_demoted(client, make):
    owner = await make.staff("owner")
    t = await make.tenant()
    only = await person(client, owner, t, role="admin")
    url = f"/admin/v1/tenants/{t}/users/{only}"
    assert (await client.patch(url, json={"role": "regular"}, headers=hdr(owner))).json()["error"]["code"] == "last_admin"
    assert (await client.delete(url, params={"reason": "Clearing up"}, headers=hdr(owner))).json()["error"]["code"] == "last_admin"
    await person(client, owner, t, role="admin")
    assert (await client.delete(url, params={"reason": "Clearing up"}, headers=hdr(owner))).status_code == 204


# ------------------------------------------------------------------------------------------- staff accounts
async def test_the_owner_adds_staff_who_choose_their_own_password(client, make, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    owner, support = await make.staff("owner"), await make.staff("support")
    body = {"name": "Karan", "email": f"{uuid.uuid4().hex[:6]}@jasinfo.in", "role": "support"}
    assert (await client.post("/admin/v1/staff", json=body, headers=hdr(support))).status_code == 403
    assert (await client.get("/admin/v1/staff", headers=hdr(support))).status_code == 403
    r = await client.post("/admin/v1/staff", json=body, headers=hdr(owner))
    assert r.status_code == 201 and "/staff/set-password?token=" in r.json()["link"]
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
    assert link.status_code == 201 and "/staff/set-password?token=" in link.json()["link"]
    del others


# ------------------------------------------------------------------------------------- the front page
async def test_the_attention_list_shows_what_needs_a_person_and_hides_money_from_support(client, make, su):
    owner, support = await make.staff("owner"), await make.staff("support")
    t = await make.tenant(name="Attention Traders")
    dead = await su.fetchval("INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at, status, last_heartbeat_at) "
                             "VALUES ($1,'ATTN-OFF','dahua','auto_register',$2,'local',now(),'offline', now() - interval '3 hours') RETURNING id", t, b"x")
    never = await su.fetchval("INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at, created_at) "
                              "VALUES ($1,'ATTN-NEVER','dahua','auto_register',$2,'local',now(), now() - interval '2 days') RETURNING id", t, b"x")
    await su.execute("UPDATE app.tenants SET status='trial', trial_ends_at = now() + interval '2 days' WHERE id=$1", t)
    items = (await client.get("/admin/v1/overview/attention", headers=hdr(support))).json()["items"]
    by = {x["kind"] + ":" + x["target"]["id"]: x for x in items if x["target"]["id"] in (str(dead), str(never), str(t))}
    assert by[f"recorder_offline:{dead}"]["severity"] == "critical" and "3 h" in by[f"recorder_offline:{dead}"]["title"]
    assert f"recorder_never_connected:{never}" in by and f"trial_ending:{t}" in by
    assert items.index(by[f"recorder_offline:{dead}"]) < items.index(by[f"trial_ending:{t}"])            # most urgent first
    # an overdue invoice is shown to the owner only
    inv = await su.fetchval("INSERT INTO app.invoices (tenant_id, period_month, status, subtotal_paise, total_paise, tax_rate, number, due_at) VALUES ($1,$2,'issued',1000,1180,18,'ATTN-1', now() - interval '5 days') RETURNING id", t, M)
    assert any(x["kind"] == "invoice_overdue" and x["target"]["id"] == str(inv) for x in (await client.get("/admin/v1/overview/attention", headers=hdr(owner))).json()["items"])
    assert not any(x["kind"] == "invoice_overdue" for x in (await client.get("/admin/v1/overview/attention", headers=hdr(support))).json()["items"])
    await su.execute("UPDATE app.recorders SET status='online' WHERE id=$1", dead)
    assert not any(x["target"]["id"] == str(dead) for x in (await client.get("/admin/v1/overview/attention", headers=hdr(owner))).json()["items"])        # fixed, gone
    assert (await client.get("/admin/v1/overview/attention")).status_code == 401


async def test_customer_health_and_fleet_numbers(client, make, su):
    owner = await make.staff("owner")
    t = await make.tenant()
    rec = await su.fetchval("INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at) VALUES ($1,$2,'dahua','auto_register',$3,'local',now()) RETURNING id", t, f"H-{uuid.uuid4().hex[:6]}", b"x")
    for n, state in enumerate(("streaming", "streaming", "offline"), 1):
        await su.execute("INSERT INTO app.cameras (tenant_id, recorder_id, channel_no, name, use_footfall, stream_state, last_frame_at) VALUES ($1,$2,$3,'C',true,$4, now())", t, rec, n, state)
    h = (await client.get(f"/admin/v1/tenants/{t}/health", headers=hdr(owner))).json()
    assert h["cameras_enabled"] == 3 and h["cameras_streaming"] == 2 and h["alerts_7d"] == 0 and h["messages_failed_7d"] == 0 and h["last_frame_at"]
    assert (await client.get(f"/admin/v1/tenants/{uuid.uuid4()}/health", headers=hdr(owner))).status_code == 404
    ov = (await client.get("/admin/v1/overview", headers=hdr(owner))).json()
    assert ov["cameras_by_state"].get("streaming", 0) >= 2


async def test_the_audit_log_can_be_searched_and_limited_by_date(client, make):
    owner = await make.staff("owner")
    t = await make.tenant()
    await client.post(f"/admin/v1/tenants/{t}/suspend", json={"reason": "Unpaid for ninety days"}, headers=hdr(owner))
    got = (await client.get("/admin/v1/audit", params={"q": "ninety days"}, headers=hdr(owner))).json()["items"]
    assert got and all("ninety days" in (x["reason"] or "") for x in got)
    assert (await client.get("/admin/v1/audit", params={"q": "tenant.suspend", "since": (date.today() + timedelta(days=1)).isoformat() + "T00:00:00Z"}, headers=hdr(owner))).json()["items"] == []
    assert (await client.get("/admin/v1/audit", params={"q": "tenant.suspend", "since": (date.today() - timedelta(days=1)).isoformat() + "T00:00:00Z"}, headers=hdr(owner))).json()["items"]


async def test_email_servers_and_webhooks_can_be_deleted_by_the_console_role(client, make):
    """The console database role must be allowed to delete these (it was only allowed to add and change)."""
    owner = await make.staff("owner")
    r = await client.post("/admin/v1/webhooks", json={"name": "Gone soon", "alert_type": "crowding", "url": "https://hooks.example.com/gone"}, headers=hdr(owner))
    assert r.status_code == 201, r.text
    assert (await client.delete(f"/admin/v1/webhooks/{r.json()['id']}", params={"reason": "Not needed any more"}, headers=hdr(owner))).status_code == 204
    m = await client.post("/admin/v1/messaging/providers", json={"channel": "email", "name": "Mail", "kind": "smtp", "secrets": {}, "reason": "to delete again",
                          "config": {"host": "smtp.example.com", "port": 587, "from_email": "a@b.co"}}, headers=hdr(owner))
    assert m.status_code == 201, m.text
    assert (await client.delete(f"/admin/v1/messaging/providers/{m.json()['id']}", params={"reason": "Not needed any more"}, headers=hdr(owner))).status_code == 204