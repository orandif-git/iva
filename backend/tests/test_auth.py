import time
import uuid

import pytest

from app.config import get_settings
from app.crypto import vault
from app.services import passwords, totp
from app.services.loginguard import guard

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _fresh_guard():
    guard.reset()
    yield
    guard.reset()


async def person(su, tenant, email=None, password=PW, role="admin", status="active"):
    email = email or f"{uuid.uuid4().hex[:8]}@shop.test"
    uid = await su.fetchval(
        "INSERT INTO app.tenant_users (tenant_id,email,name,role,status,password_hash) VALUES ($1,$2,'Asha',$3::app.tenant_role,$4,$5) RETURNING id",
        tenant, email, role, status, passwords.hash_password(password) if password else None)
    return uid, email


async def login(client, email, password=PW, **kw):
    return await client.post("/v1/auth/login", json={"email": email, "password": password, **kw})


async def staff_with_totp(su, password=PW, role="owner"):
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    sid = await su.fetchval("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'Ravi',$2::platform.staff_role,$3) RETURNING id",
                            email, role, passwords.hash_password(password))
    secret = totp.new_secret()
    await su.execute("UPDATE platform.staff_users SET totp_secret_ciphertext=$2, mfa_enabled=true WHERE id=$1", sid, vault().encrypt(secret, f"totp:{sid}"))
    return sid, email, secret


def code_now(secret, offset=0):
    return totp._code(secret, int(time.time() // 30) + offset)


# ------------------------------------------------------------------------------------------ customers
async def test_a_customer_signs_in_and_the_token_works(client, make, su):
    t = await make.tenant()
    _, email = await person(su, t)
    r = await login(client, email.upper())                                     # email is not case-sensitive
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    j = r.json()
    assert j["token_type"] == "bearer" and j["expires_in"] == 900 and j["user"]["name"] == "Asha"
    me = await client.get("/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})
    assert me.status_code == 200 and me.json()["tenant"]["id"] == str(t)


async def test_every_kind_of_failed_sign_in_looks_the_same(client, make, su):
    t = await make.tenant()
    _, ok_email = await person(su, t)
    _, invited = await person(su, t, password=None, status="invited")
    _, disabled = await person(su, t, status="disabled")
    seen = set()
    for email, pw in ((ok_email, "wrong password!"), ("nobody@shop.test", PW), (invited, PW), (disabled, PW)):
        r = await login(client, email, pw)
        assert r.status_code == 401
        seen.add((r.json()["error"]["code"], r.json()["error"]["message"]))
    assert len(seen) == 1                                                       # nothing reveals which emails have accounts


async def test_five_wrong_passwords_lock_the_account_even_for_the_right_password(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)
    for _ in range(5):
        assert (await login(client, email, "nope nope nope")).status_code == 401
    guard.reset()                                                               # the database lock must hold on its own
    r = await login(client, email)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    await su.execute("UPDATE app.tenant_users SET locked_until = now() - interval '1 minute' WHERE id=$1", uid)
    assert (await login(client, email)).status_code == 200                      # unlocked after the wait


async def test_lockout_counts_emails_that_do_not_exist_too(client):
    for _ in range(5):
        assert (await login(client, "ghost@shop.test", "whatever password")).status_code == 401
    assert (await login(client, "ghost@shop.test", "whatever password")).status_code == 429     # same behaviour as a real account


async def test_too_many_attempts_from_one_address_are_refused(client):
    codes = [(await login(client, f"x{i}@shop.test", "whatever password")).status_code for i in range(get_settings().login_ip_limit + 3)]
    assert 429 in codes and codes[0] == 401


async def test_a_suspended_organisation_cannot_sign_in(client, make, su):
    t = await make.tenant(status="suspended")
    _, email = await person(su, t)
    r = await login(client, email)
    assert r.status_code == 403 and r.json()["error"]["code"] == "tenant_suspended"


async def test_one_email_in_two_organisations_must_say_which(client, make, su):
    t1, t2 = await make.tenant(name="Alpha Works"), await make.tenant(name="Beta Works")
    email = f"{uuid.uuid4().hex[:6]}@shared.test"
    await person(su, t1, email), await person(su, t2, email)
    r = await login(client, email)
    assert r.status_code == 409 and r.json()["error"]["code"] == "choose_account"
    r = await login(client, email, tenant="beta-works")
    assert r.status_code == 200
    assert (await client.get("/v1/me", headers={"Authorization": "Bearer " + r.json()["access_token"]})).json()["tenant"]["id"] == str(t2)


async def test_passwords_and_tokens_are_not_stored_in_the_clear(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)
    j = (await login(client, email)).json()
    stored = await su.fetchval("SELECT password_hash FROM app.tenant_users WHERE id=$1", uid)
    assert stored.startswith("scrypt$") and PW not in stored
    assert await su.fetchval("SELECT count(*) FROM platform.auth_tokens WHERE token_hash=$1", j["refresh_token"]) == 0     # only the hash is kept
    assert await su.fetchval("SELECT count(*) FROM platform.auth_tokens WHERE account_id=$1 AND kind='refresh'", uid) == 1


# --------------------------------------------------------------------------------- refresh and logout
async def test_refresh_gives_a_new_pair_and_each_refresh_token_works_once(client, make, su):
    t = await make.tenant()
    _, email = await person(su, t)
    first = (await login(client, email)).json()
    r = await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await client.get("/v1/me", headers={"Authorization": "Bearer " + second["access_token"]})).status_code == 200
    # the old one again: someone has a copy, so the whole sign-in ends, including the newest token
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})).status_code == 401


async def test_logout_ends_the_sign_in(client, make, su):
    t = await make.tenant()
    _, email = await person(su, t)
    j = (await login(client, email)).json()
    assert (await client.post("/v1/auth/logout", json={"refresh_token": j["refresh_token"]})).status_code == 204
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401


async def test_a_disabled_user_or_suspended_organisation_cannot_refresh(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)
    j = (await login(client, email)).json()
    await su.execute("UPDATE app.tenant_users SET status='disabled' WHERE id=$1", uid)
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    await su.execute("UPDATE app.tenant_users SET status='active' WHERE id=$1", uid)
    j = (await login(client, email)).json()
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)


async def test_the_other_audiences_refresh_token_is_not_accepted(client, make, su):
    t = await make.tenant()
    _, email = await person(su, t)
    j = (await login(client, email)).json()
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})).status_code == 401


# --------------------------------------------------------------------------------- invites and resets
async def test_staff_invite_a_customer_who_sets_their_own_password(client, make, su):
    staff = await make.staff("technician")
    t = await make.tenant()
    email = f"{uuid.uuid4().hex[:6]}@newshop.test"
    r = await client.post(f"/admin/v1/tenants/{t}/users", json={"email": email, "name": "Meena", "role": "admin"}, headers=staff["headers"])
    assert r.status_code == 201, r.text
    link = r.json()["link"]
    assert link.startswith("https://gi.test/set-password?token=")
    token = link.split("token=")[1]
    assert (await login(client, email, "any password here")).status_code == 401           # no password yet
    assert (await client.post("/v1/auth/set-password", json={"token": token, "password": "short"})).json()["error"]["code"] == "weak_password"
    assert (await client.post("/v1/auth/set-password", json={"token": token, "password": "password123"})).json()["error"]["code"] == "weak_password"
    assert (await client.post("/v1/auth/set-password", json={"token": token, "password": "a good long password"})).status_code == 204
    assert (await login(client, email, "a good long password")).status_code == 200
    assert await su.fetchval("SELECT status FROM app.tenant_users WHERE email=$1", email) == "active"
    again = await client.post("/v1/auth/set-password", json={"token": token, "password": "another long password"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "link_invalid"      # the link works once
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='user.invite' AND tenant_id=$1", t) == 1


async def test_an_invitation_cannot_be_made_twice_for_one_email_or_by_the_wrong_staff(client, make):
    t = await make.tenant()
    tech, finance = await make.staff("technician"), await make.staff("finance")
    body = {"email": "dup@shop.test", "name": "Dup"}
    assert (await client.post(f"/admin/v1/tenants/{t}/users", json=body, headers=finance["headers"])).status_code == 403
    assert (await client.post(f"/admin/v1/tenants/{t}/users", json=body, headers=tech["headers"])).status_code == 201
    assert (await client.post(f"/admin/v1/tenants/{t}/users", json=body, headers=tech["headers"])).json()["error"]["code"] == "email_in_use"
    assert (await client.post(f"/admin/v1/tenants/{uuid.uuid4()}/users", json=body, headers=tech["headers"])).status_code == 404
    bad = await client.post(f"/admin/v1/tenants/{t}/users", json={"email": "not-an-email", "name": "X"}, headers=tech["headers"])
    assert bad.status_code == 422


async def test_an_expired_or_unknown_link_is_refused(client, make, su):
    staff = await make.staff("support")
    t = await make.tenant()
    r = await client.post(f"/admin/v1/tenants/{t}/users", json={"email": "late@shop.test", "name": "Late"}, headers=staff["headers"])
    token = r.json()["link"].split("token=")[1]
    await su.execute("UPDATE platform.auth_tokens SET expires_at = now() - interval '1 minute' WHERE kind='invite' AND account_id=$1", r.json()["user_id"])
    assert (await client.post("/v1/auth/set-password", json={"token": token, "password": "a good long password"})).status_code == 400
    assert (await client.post("/v1/auth/set-password", json={"token": "x" * 43, "password": "a good long password"})).status_code == 400


async def test_a_reset_link_changes_the_password_signs_out_every_device_and_replaces_older_links(client, make, su):
    staff = await make.staff("support")
    t = await make.tenant()
    uid, email = await person(su, t)
    old_session = (await login(client, email)).json()
    one = (await client.post(f"/admin/v1/tenants/{t}/users/{uid}/reset-link", headers=staff["headers"])).json()["link"].split("token=")[1]
    two = (await client.post(f"/admin/v1/tenants/{t}/users/{uid}/reset-link", headers=staff["headers"])).json()["link"].split("token=")[1]
    assert (await client.post("/v1/auth/set-password", json={"token": one, "password": "a brand new password"})).status_code == 400      # replaced
    assert (await client.post("/v1/auth/set-password", json={"token": two, "password": "a brand new password"})).status_code == 204
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": old_session["refresh_token"]})).status_code == 401              # signed out
    assert (await login(client, email, PW)).status_code == 401
    assert (await login(client, email, "a brand new password")).status_code == 200


async def test_a_disabled_user_loses_access_at_once(client, make, su):
    staff = await make.staff("support")
    t = await make.tenant()
    uid, email = await person(su, t)
    j = (await login(client, email)).json()
    assert (await client.post(f"/admin/v1/tenants/{t}/users/{uid}/disable", headers=staff["headers"])).status_code == 204
    assert (await client.get("/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})).status_code == 401
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    assert (await login(client, email)).status_code == 401
    assert (await client.post(f"/admin/v1/tenants/{t}/users/{uid}/reset-link", headers=staff["headers"])).status_code == 409


async def test_staff_can_list_a_customers_users_without_any_secret(client, make, su):
    staff = await make.staff("analyst")
    t = await make.tenant()
    await person(su, t)
    items = (await client.get(f"/admin/v1/tenants/{t}/users", headers=staff["headers"])).json()["items"]
    assert len(items) == 1 and items[0]["has_password"] is True and "password_hash" not in items[0]


async def test_a_customer_changes_their_own_password(client, make, su):
    t = await make.tenant()
    uid, email = await person(su, t)
    a, b = (await login(client, email)).json(), (await login(client, email)).json()
    h = {"Authorization": "Bearer " + a["access_token"]}
    assert (await client.post("/v1/auth/change-password", json={"current_password": "not mine", "new_password": "a brand new password"}, headers=h)).status_code == 401
    assert (await client.post("/v1/auth/change-password", json={"current_password": PW, "new_password": "short"}, headers=h)).status_code == 422
    assert (await client.post("/v1/auth/change-password", json={"current_password": PW, "new_password": "a brand new password"}, headers=h)).status_code == 204
    assert (await login(client, email, PW)).status_code == 401
    assert (await login(client, email, "a brand new password")).status_code == 200
    assert (await client.post("/v1/auth/refresh", json={"refresh_token": b["refresh_token"]})).status_code == 401        # other devices sign in again


async def test_staff_viewing_as_a_customer_cannot_change_their_password(client, make, su):
    staff = await make.staff("owner")
    t = await make.tenant()
    uid, email = await person(su, t)
    r = await client.post(f"/admin/v1/tenants/{t}/impersonate", json={"reason": "Checking a report"}, headers=staff["headers"])
    assert r.status_code == 201
    r2 = await client.post("/v1/auth/change-password", json={"current_password": PW, "new_password": "a brand new password"},
                           headers={"Authorization": "Bearer " + r.json()["token"]})
    assert r2.status_code == 403


# ------------------------------------------------------------------------------------------------ staff
async def staff_login(client, email, password=PW, code="000000"):
    return await client.post("/admin/v1/auth/login", json={"email": email, "password": password, "code": code})


async def test_staff_need_password_and_a_code_and_the_token_works_on_the_admin_api(client, su):
    sid, email, secret = await staff_with_totp(su)
    assert (await staff_login(client, email, PW, "123456")).status_code == 401                 # right password, wrong code
    assert (await staff_login(client, email, "wrong password!", code_now(secret))).status_code == 401
    r = await staff_login(client, email, PW, code_now(secret))
    assert r.status_code == 200, r.text
    j = r.json()
    me = await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})
    assert me.status_code == 200 and me.json()["role"] == "owner"
    assert (await client.get("/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})).status_code == 401      # not a customer token


async def test_a_code_cannot_be_used_twice(client, su):
    _, email, secret = await staff_with_totp(su)
    c = code_now(secret)
    assert (await staff_login(client, email, PW, c)).status_code == 200
    assert (await staff_login(client, email, PW, c)).status_code == 401                         # a code seen over a shoulder is useless
    assert (await staff_login(client, email, PW, code_now(secret, +1))).status_code == 200      # the next one works


async def test_staff_without_an_authenticator_or_password_cannot_sign_in(client, su):
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    await su.execute("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'No Totp','owner',$2)", email, passwords.hash_password(PW))
    assert (await staff_login(client, email, PW, "123456")).status_code == 401
    email2 = f"{uuid.uuid4().hex[:8]}@gi.test"
    await su.execute("INSERT INTO platform.staff_users (email,name,role) VALUES ($1,'No Pw','owner')", email2)
    assert (await staff_login(client, email2, PW, "123456")).status_code == 401


async def test_disabled_staff_cannot_sign_in_or_refresh(client, su):
    sid, email, secret = await staff_with_totp(su)
    j = (await staff_login(client, email, PW, code_now(secret))).json()
    await su.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", sid)
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    assert (await staff_login(client, email, PW, code_now(secret, +1))).status_code == 401


async def test_staff_accounts_lock_after_wrong_attempts(client, su):
    sid, email, secret = await staff_with_totp(su)
    for _ in range(5):
        await staff_login(client, email, PW, "111111")
    guard.reset()
    r = await staff_login(client, email, PW, code_now(secret))
    assert r.status_code == 429


async def test_staff_refresh_and_logout(client, su):
    _, email, secret = await staff_with_totp(su)
    j = (await staff_login(client, email, PW, code_now(secret))).json()
    r = await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})
    assert r.status_code == 200
    me = await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + r.json()["access_token"]})
    assert me.status_code == 200                                                                   # the refreshed token is still a multi-factor one
    assert (await client.post("/admin/v1/auth/logout", json={"refresh_token": r.json()["refresh_token"]})).status_code == 204
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})).status_code == 401


# ------------------------------------------------------------------------------------------------ units
def test_the_password_rules():
    from app.errors import ApiError
    for bad in ("short", "password123", "aaaaaaaaaaaa", "me@shop.test", "Password123"[:9]):
        with pytest.raises(ApiError):
            passwords.check_policy(bad, "me@shop.test")
    passwords.check_policy("a long and fine password", "me@shop.test")


def test_totp_matches_the_standard_test_vector():
    import base64
    assert totp._code(base64.b32encode(b"12345678901234567890").decode(), 59 // 30) == "287082"      # RFC 6238 appendix B
    s = totp.new_secret()
    assert totp.verify(s, totp._code(s, 100), now=100 * 30) == 100
    assert totp.verify(s, totp._code(s, 100), last_step=100, now=100 * 30) is None
    assert totp.verify(s, totp._code(s, 90), now=100 * 30) is None                                      # too old
    assert totp.verify(s, "12345", now=0) is None and totp.verify(s, "abcdef", now=0) is None


# ------------------------------------------------------------------------- staff sign-in without an authenticator (GI_STAFF_REQUIRE_MFA=false)
@pytest.fixture
def mfa_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)


def claims_of(token):
    from app.security import verify_token
    return verify_token("platform", token)


async def test_by_default_a_code_is_required_and_the_sign_in_page_is_told_so(client, su):
    assert (await client.get("/admin/v1/auth/options")).json() == {"mfa_required": True}
    _, email, _ = await staff_with_totp(su)
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).status_code == 401            # no code: refused
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "code": "123456"})).status_code == 401


async def test_with_two_step_off_staff_sign_in_with_a_password_alone(client, su, mfa_off):
    assert (await client.get("/admin/v1/auth/options")).json() == {"mfa_required": False}
    sid, email, secret = await staff_with_totp(su)
    r = await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})            # no code at all
    assert r.status_code == 200, r.text
    j = r.json()
    assert claims_of(j["access_token"])["mfa"] is False and j["refresh_token"].startswith("n_")      # marked as a password-only sign-in
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})).status_code == 200
    ok = await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})
    assert ok.status_code == 200 and ok.json()["refresh_token"].startswith("n_")                     # stays password-only when refreshed
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": "wrong password!"})).status_code == 401
    assert (await client.post("/admin/v1/auth/login", json={"email": "nobody@gi.test", "password": PW})).status_code == 401


async def test_a_staff_member_with_no_authenticator_can_sign_in_when_it_is_off_and_not_when_it_is_on(client, su, monkeypatch):
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    await su.execute("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'No App','owner',$2)", email, passwords.hash_password(PW))
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).status_code == 401           # on: refused
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).status_code == 200            # off: allowed


async def test_a_valid_code_still_gives_a_full_token_when_two_step_is_off(client, su, mfa_off):
    _, email, secret = await staff_with_totp(su)
    j = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "code": code_now(secret)})).json()
    assert claims_of(j["access_token"])["mfa"] is True and not j["refresh_token"].startswith("n_")


async def test_turning_two_step_back_on_refuses_what_a_password_only_sign_in_made(client, su, monkeypatch):
    _, email, secret = await staff_with_totp(su)
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    weak = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).json()
    full = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "code": code_now(secret, +1)})).json()
    monkeypatch.setattr(get_settings(), "staff_require_mfa", True)
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + weak["access_token"]})).status_code == 401       # the access token no longer works
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": weak["refresh_token"]})).status_code == 401           # and cannot be upgraded
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + full["access_token"]})).status_code == 200         # one made with a code still works
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": full["refresh_token"]})).status_code == 200


async def test_the_prefix_cannot_be_added_or_removed_to_change_what_a_token_is(client, su, monkeypatch):
    _, email, secret = await staff_with_totp(su)
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    weak = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).json()["refresh_token"]
    monkeypatch.setattr(get_settings(), "staff_require_mfa", True)
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": weak[2:]})).status_code == 401              # prefix removed: a different (unknown) token
    full = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "code": code_now(secret, +1)})).json()["refresh_token"]
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": "n_" + full})).status_code == 401           # prefix added: also unknown


async def test_passwords_still_lock_accounts_when_two_step_is_off(client, su, mfa_off):
    sid, email, _ = await staff_with_totp(su)
    for _ in range(5):
        assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": "wrong password!"})).status_code == 401
    guard.reset()
    assert (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).status_code == 429             # locked, even with the right password


async def test_the_old_mfa_flag_in_a_token_is_still_honoured_when_two_step_is_off(client, make, mfa_off):
    weak = await make.staff("owner", mfa=False)                      # a token without the multi-factor flag
    assert (await client.get("/admin/v1/me", headers=weak["headers"])).status_code == 200
