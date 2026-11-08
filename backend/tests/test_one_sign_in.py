"""One sign-in form: customers and staff from the same address; staff who need a code are asked for it."""
import uuid

import pytest

from app.config import get_settings
from app.services import passwords, totp
from app.services.loginguard import guard
from tests.test_auth import PW, person

pytestmark = pytest.mark.usefixtures("_isolated")


@pytest.fixture(autouse=True)
def _isolated():
    guard.reset()
    yield
    guard.reset()


async def sign_in(client, email, password=PW, **kw):
    return await client.post("/v1/auth/sign-in", json={"email": email, "password": password, **kw})


async def test_a_customer_is_signed_in_and_told_they_are_a_customer(client, make, su):
    t = await make.tenant()
    _, email = await person(su, t)
    r = await sign_in(client, email)
    assert r.status_code == 200 and r.json()["kind"] == "customer" and r.json()["access_token"] and r.json()["user"]["tenant"]


async def test_a_staff_member_without_two_step_is_signed_in_as_staff(client, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    await su.execute("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'Ravi','owner',$2)", email, passwords.hash_password(PW))
    r = await sign_in(client, email)
    assert r.status_code == 200 and r.json()["kind"] == "staff" and r.json()["refresh_token"]


async def test_a_staff_member_with_two_step_is_asked_for_the_code_only_after_the_password_is_right(client, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "staff_require_mfa", True)
    email = f"{uuid.uuid4().hex[:8]}@gi.test"
    secret = totp.new_secret()
    sid = await su.fetchval("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'Ravi','owner',$2) RETURNING id", email, passwords.hash_password(PW))
    await su.execute("UPDATE platform.staff_users SET totp_secret_ciphertext=$2 WHERE id=$1", sid, __import__("app.crypto", fromlist=["vault"]).vault().encrypt(secret, f"totp:{sid}"))
    assert (await sign_in(client, email)).json() == {"next": "code"}                         # right password, no code: asked for it
    wrong = await sign_in(client, email, password="not the password")
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "wrong_credentials"                                 # wrong password: the same plain answer, no hint about codes
    r = await sign_in(client, email, code=totp._code(secret, int(__import__("time").time() // 30)))
    assert r.status_code == 200 and r.json()["kind"] == "staff"


async def test_a_wrong_password_gives_the_same_answer_for_staff_customers_and_unknown_emails(client, make, su):
    t = await make.tenant()
    _, cust = await person(su, t)
    staff = f"{uuid.uuid4().hex[:8]}@gi.test"
    await su.execute("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'Ravi','owner',$2)", staff, passwords.hash_password(PW))
    answers = {(await sign_in(client, e, password="nope nope nope")).json()["error"]["code"] for e in (cust, staff, f"{uuid.uuid4().hex}@nowhere.test")}
    assert answers == {"wrong_credentials"}


async def test_an_email_that_is_both_is_signed_in_as_staff_when_the_staff_password_is_right(client, make, su, monkeypatch):
    monkeypatch.setattr(get_settings(), "staff_require_mfa", False)
    t = await make.tenant()
    _, email = await person(su, t)
    await su.execute("INSERT INTO platform.staff_users (email,name,role,password_hash) VALUES ($1,'Ravi','support',$2)", email, passwords.hash_password(PW))
    assert (await sign_in(client, email)).json()["kind"] == "staff"
    assert (await sign_in(client, email, password="other password here")).status_code == 401           # the customer password is not used as a staff password


async def test_a_disabled_or_suspended_account_is_refused(client, make, su):
    t = await make.tenant(status="suspended")
    _, email = await person(su, t)
    assert (await sign_in(client, email)).json()["error"]["code"] == "tenant_suspended"
