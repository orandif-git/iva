"""Staff sign-in (email and password), refresh and logout, the links that set a password, and a person's own profile."""
import uuid

import pytest

from app.services import passwords
from app.services.loginguard import guard

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _fresh_guard():
    guard.reset()
    yield
    guard.reset()


async def staff_with_password(su, password=PW, role="owner", status="active"):
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    sid = await su.fetchval("INSERT INTO platform.staff_users (email,name,role,status,password_hash) VALUES ($1,'Ravi',$2::platform.staff_role,$3,$4) RETURNING id",
                            email, role, status, passwords.hash_password(password) if password else None)
    return sid, email


async def staff_login(client, email, password=PW):
    return await client.post("/admin/v1/auth/login", json={"email": email, "password": password})


# ------------------------------------------------------------------------------------------------ signing in
async def test_staff_sign_in_with_email_and_password_and_the_token_works_on_the_admin_api(client, su):
    sid, email = await staff_with_password(su)
    r = await staff_login(client, email)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["user"]["role"] == "owner" and j["refresh_token"] and r.headers["cache-control"] == "no-store"
    me = await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})
    assert me.status_code == 200 and me.json()["email"] == email
    assert await su.fetchval("SELECT last_login_at IS NOT NULL FROM platform.staff_users WHERE id=$1", sid)
    assert (await client.get("/admin/v1/me")).status_code == 401, "no token, no access"


async def test_every_kind_of_failed_sign_in_looks_the_same(client, su):
    _, email = await staff_with_password(su)
    nopw, email_nopw = await staff_with_password(su, password=None)
    _, email_off = await staff_with_password(su, status="disabled")
    seen = set()
    for who, pw in ((email, "wrong password!"), ("nobody@gi.test", PW), (email_nopw, PW), (email_off, PW)):
        r = await staff_login(client, who, pw)
        assert r.status_code == 401
        seen.add((r.json()["error"]["code"], r.json()["error"]["message"]))
    assert len(seen) == 1, "nothing tells a stranger which emails have accounts"


async def test_staff_accounts_lock_after_wrong_attempts(client, su):
    sid, email = await staff_with_password(su)
    for _ in range(5):
        await staff_login(client, email, "wrong password!")
    guard.reset()
    assert (await staff_login(client, email)).status_code == 429, "even the right password waits"


async def test_too_many_attempts_from_one_address_are_refused(client):
    codes = [(await staff_login(client, f"x{i}@gi.test", "wrong password!")).status_code for i in range(40)]
    assert 429 in codes


async def test_passwords_and_tokens_are_not_stored_in_the_clear(client, su):
    sid, email = await staff_with_password(su)
    j = (await staff_login(client, email)).json()
    stored = await su.fetchval("SELECT password_hash FROM platform.staff_users WHERE id=$1", sid)
    assert stored.startswith("scrypt$") and PW not in stored
    assert await su.fetchval("SELECT count(*) FROM platform.auth_tokens WHERE token_hash=$1", j["refresh_token"]) == 0, "only the hash is kept"


# ------------------------------------------------------------------------------------------------ refresh and logout
async def test_refresh_gives_a_new_pair_and_each_refresh_token_works_once(client, su):
    _, email = await staff_with_password(su)
    j = (await staff_login(client, email)).json()
    r = await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})
    assert r.status_code == 200 and r.json()["refresh_token"] != j["refresh_token"]
    me = await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + r.json()["access_token"]})
    assert me.status_code == 200
    again = await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})
    assert again.status_code == 401, "an old token used again means it was copied"
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})).status_code == 401, "so the whole sign-in ends"


async def test_logout_ends_the_sign_in(client, su):
    _, email = await staff_with_password(su)
    j = (await staff_login(client, email)).json()
    assert (await client.post("/admin/v1/auth/logout", json={"refresh_token": j["refresh_token"]})).status_code == 204
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401


async def test_a_disabled_person_cannot_refresh_or_use_the_admin_api(client, su):
    sid, email = await staff_with_password(su)
    j = (await staff_login(client, email)).json()
    await su.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", sid)
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 401
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})).status_code == 401
    assert (await staff_login(client, email)).status_code == 401


async def test_a_token_for_another_audience_is_not_accepted(client, su):
    from app.security import issue_token
    sid, _ = await staff_with_password(su)
    with pytest.raises(KeyError):
        issue_token("customer", {"sub": str(sid)}, 600)               # there is no customer sign-in any more
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + "x" * 40})).status_code == 401


# ------------------------------------------------------------------------------------------------ invitation and reset links
async def link_for(client, make, sid):
    owner = await make.staff("owner")
    r = await client.post(f"/admin/v1/staff/{sid}/reset-link", params={"reason": "A new link for the test"}, headers=owner["headers"])
    assert r.status_code == 201, r.text
    return r.json()["link"].split("token=")[1]


async def test_a_link_sets_a_password_once_and_signs_out_every_device(client, make, su):
    sid, email = await staff_with_password(su)
    old = (await staff_login(client, email)).json()
    token = await link_for(client, make, sid)
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": "short"})).status_code == 422
    new = "a brand new phrase 2026"
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": new})).status_code == 204
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": new + "!"})).status_code == 400, "a link works once"
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401, "a reset signs out every device"
    assert (await staff_login(client, email, PW)).status_code == 401
    assert (await staff_login(client, email, new)).status_code == 200


async def test_a_new_link_replaces_the_older_one_and_unknown_or_expired_links_are_refused(client, make, su):
    sid, _ = await staff_with_password(su)
    first = await link_for(client, make, sid)
    second = await link_for(client, make, sid)
    assert (await client.post("/admin/v1/auth/set-password", json={"token": first, "password": "a brand new phrase 2026"})).status_code == 400
    assert (await client.post("/admin/v1/auth/set-password", json={"token": "x" * 43, "password": "a brand new phrase 2026"})).status_code == 400
    await su.execute("UPDATE platform.auth_tokens SET expires_at = now() - interval '1 minute' WHERE account_id=$1 AND kind IN ('invite','reset')", sid)
    assert (await client.post("/admin/v1/auth/set-password", json={"token": second, "password": "a brand new phrase 2026"})).status_code == 400


async def test_an_invited_person_can_sign_in_right_after_choosing_a_password(client, make, su):
    owner = await make.staff("owner")
    r = await client.post("/admin/v1/staff", json={"name": "Karthik", "email": f"{uuid.uuid4().hex[:6]}@gi.test", "role": "support"}, headers=owner["headers"])
    assert r.status_code == 201, r.text
    token = r.json()["link"].split("token=")[1]
    assert "/set-password?token=" in r.json()["link"]
    assert (await client.post("/admin/v1/auth/set-password", json={"token": token, "password": "a fine long password 1"})).status_code == 204
    assert (await staff_login(client, r.json()["email"], "a fine long password 1")).status_code == 200


# ------------------------------------------------------------------------------------------------ units
def test_the_password_rules():
    from app.errors import ApiError
    for bad in ("short", "password123", "aaaaaaaaaaaa", "me@shop.test", "Password123"[:9]):
        with pytest.raises(ApiError):
            passwords.check_policy(bad, "me@shop.test")
    passwords.check_policy("a long and fine password", "me@shop.test")


# ------------------------------------------------------------------------------------------------ a person's own profile
async def device(sid):
    """One more place this person is signed in on (a full sign-in with its own family of tokens)."""
    from app.db import db
    from app.services import auth
    async with db.admin_tx() as c:
        return await auth._tokens(c, sid)


async def test_staff_profile_name_password_and_other_devices(client, su):
    sid, email = await staff_with_password(su)
    a, b = await device(sid), await device(sid)
    ha = {"Authorization": "Bearer " + a["access_token"]}
    me = (await client.get("/admin/v1/me", headers=ha)).json()
    assert me["email"] == email and me["devices"] == 2
    r = await client.patch("/admin/v1/me", json={"name": "  Aman Singh "}, headers=ha)
    assert r.status_code == 200 and r.json()["name"] == "Aman Singh"
    assert (await client.patch("/admin/v1/me", json={"name": ""}, headers=ha)).status_code == 422
    new = "a brand new phrase 2026"
    body = {"current_password": PW, "new_password": new, "refresh_token": a["refresh_token"]}
    assert (await client.post("/admin/v1/me/change-password", json={**body, "current_password": "wrong password!"}, headers=ha)).status_code == 401
    assert (await client.post("/admin/v1/me/change-password", json={**body, "new_password": "short"}, headers=ha)).status_code == 422
    assert (await client.post("/admin/v1/me/change-password", json={**body, "new_password": PW}, headers=ha)).status_code == 422          # not the same one again
    assert (await client.post("/admin/v1/me/change-password", json=body, headers=ha)).status_code == 204
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": b["refresh_token"]})).status_code == 401, "the other device is signed out"
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 200, "this one stays signed in"
    assert passwords.verify_password(new, await su.fetchval("SELECT password_hash FROM platform.staff_users WHERE id=$1", sid)) and not passwords.verify_password(PW, await su.fetchval("SELECT password_hash FROM platform.staff_users WHERE id=$1", sid))
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action IN ('staff.password_change','staff.password_change_failed','staff.profile') AND target_id=$1", str(sid)) == 3
    assert new not in str(await su.fetch("SELECT before, after FROM platform.audit_log WHERE target_id=$1", str(sid))), "no password in the log"


async def test_signing_out_every_other_device_keeps_this_one(client, su):
    sid, email = await staff_with_password(su)
    a, b, c = await device(sid), await device(sid), await device(sid)
    ha = {"Authorization": "Bearer " + a["access_token"]}
    assert (await client.post("/admin/v1/me/sign-out-others", json={}, headers=ha)).status_code == 409           # it must know which one to keep
    r = await client.post("/admin/v1/me/sign-out-others", json={"refresh_token": a["refresh_token"]}, headers=ha)
    assert r.status_code == 200 and r.json()["devices"] == 1
    for dead in (b, c):
        assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": dead["refresh_token"]})).status_code == 401
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 200


# ------------------------------------------------------------------------------------------------ stay signed in on this phone
async def _expires_in_days(su, raw):
    from app.services.auth import token_hash
    return await su.fetchval("SELECT extract(epoch FROM expires_at - now()) / 86400 FROM platform.auth_tokens WHERE token_hash=$1", token_hash(raw))


async def test_a_sign_in_lasts_12_hours_unless_the_person_chose_to_stay_signed_in_for_30_days(client, su):
    sid, email = await staff_with_password(su)
    short = (await staff_login(client, email)).json()
    assert short["remember"] is False and 0.45 < await _expires_in_days(su, short["refresh_token"]) < 0.55
    guard.reset()
    long = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "remember": True})).json()
    assert long["remember"] is True and 29.9 < await _expires_in_days(su, long["refresh_token"]) < 30.1


async def test_a_long_sign_in_stays_long_when_it_is_renewed_and_a_short_one_stays_short(client, su):
    sid, email = await staff_with_password(su)
    long = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, "remember": True})).json()
    again = (await client.post("/admin/v1/auth/refresh", json={"refresh_token": long["refresh_token"]})).json()
    assert again["remember"] is True and 29.9 < await _expires_in_days(su, again["refresh_token"]) < 30.1
    guard.reset()
    short = (await staff_login(client, email)).json()
    renewed = (await client.post("/admin/v1/auth/refresh", json={"refresh_token": short["refresh_token"]})).json()
    assert renewed["remember"] is False and await _expires_in_days(su, renewed["refresh_token"]) < 1


async def test_the_switch_under_more_makes_a_sign_in_long_or_short_from_now_on(client, su):
    sid, email = await staff_with_password(su)
    j = (await staff_login(client, email)).json()
    on = await client.post("/admin/v1/auth/remember", json={"refresh_token": j["refresh_token"], "remember": True})
    assert on.status_code == 200 and on.json()["remember"] is True and await _expires_in_days(su, on.json()["refresh_token"]) > 29
    off = await client.post("/admin/v1/auth/remember", json={"refresh_token": on.json()["refresh_token"], "remember": False})
    assert off.json()["remember"] is False and await _expires_in_days(su, off.json()["refresh_token"]) < 1
    assert (await client.post("/admin/v1/auth/remember", json={"refresh_token": "x" * 40, "remember": True})).status_code == 401
    assert (await client.post("/admin/v1/auth/remember", json={"refresh_token": on.json()["refresh_token"], "remember": True})).status_code == 401, "a used token does not work twice"


# ---------------------------------------------------------------- stronger password hashing, upgraded at the next sign-in
import base64 as _b64
import hashlib as _hl
import os as _os

from app.services import passwords


def _old_hash(password: str) -> str:
    """What the server saved before the settings were raised (N=2^14, p=1)."""
    salt = _os.urandom(16)
    h = _hl.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${2 ** 14}$8$1${_b64.b64encode(salt).decode()}${_b64.b64encode(h).decode()}"


def test_new_hashes_use_todays_settings_and_old_ones_are_flagged():
    new = passwords.hash_password("a long enough password")
    assert new.split("$")[1:4] == [str(2 ** 15), "8", "3"] and not passwords.needs_rehash(new)
    old = _old_hash("a long enough password")
    assert passwords.needs_rehash(old) and passwords.verify_password("a long enough password", old) and not passwords.verify_password("wrong password!!", old)
    assert not passwords.needs_rehash(None) and not passwords.needs_rehash("garbage")


async def test_an_old_password_hash_is_replaced_by_a_stronger_one_at_sign_in(client, su):
    _, email = await staff_with_password(su)
    await su.execute("UPDATE platform.staff_users SET password_hash=$2 WHERE email=$1", email, _old_hash(PW))
    assert (await staff_login(client, email, PW)).status_code == 200
    now = await su.fetchval("SELECT password_hash FROM platform.staff_users WHERE email=$1", email)
    assert not passwords.needs_rehash(now) and passwords.verify_password(PW, now)
    assert (await staff_login(client, email, PW)).status_code == 200


async def test_a_wrong_password_does_not_change_the_saved_hash(client, su):
    _, email = await staff_with_password(su)
    old = _old_hash(PW)
    await su.execute("UPDATE platform.staff_users SET password_hash=$2 WHERE email=$1", email, old)
    assert (await staff_login(client, email, "not the password!")).status_code == 401
    assert await su.fetchval("SELECT password_hash FROM platform.staff_users WHERE email=$1", email) == old
