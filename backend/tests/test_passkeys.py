"""Sign in with Face ID or a fingerprint (passkeys). The phone is played by a small stand-in that makes keys and signs the way a real phone does."""
import hashlib
import json
import os
import struct
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import passkeys
from app.services.loginguard import guard
from tests.test_auth import PW, staff_with_password

b64e, b64d = passkeys.b64e, passkeys.b64d


def cbor(v) -> bytes:
    """Write the few CBOR shapes a passkey answer uses."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        if n < 65536:
            return bytes([major << 5 | 25]) + n.to_bytes(2, "big")
        return bytes([major << 5 | 26]) + n.to_bytes(4, "big")
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


ORIGIN, RP = "https://gi.test", "gi.test"


class Phone:
    """What Face ID does: it makes a key pair, keeps the private half, and signs challenges after a look at the face."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred = os.urandom(32)
        self.count = 0

    def _ad(self, flags, with_key=False, rp=RP):
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if with_key:
            n = self.key.public_key().public_numbers()
            cose = {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
            ad += b"\x00" * 16 + len(self.cred).to_bytes(2, "big") + self.cred + cbor(cose)
        return ad

    def create(self, options, *, origin=ORIGIN, flags=0x45, rp=RP, kind="webauthn.create", challenge=None):
        cd = json.dumps({"type": kind, "challenge": challenge or options["challenge"], "origin": origin}).encode()
        att = cbor({"fmt": "none", "attStmt": {}, "authData": self._ad(flags, True, rp)})
        return {"clientDataJSON": b64e(cd), "attestationObject": b64e(att)}

    def get(self, options, *, origin=ORIGIN, flags=0x05, rp=RP, kind="webauthn.get", count=None, key=None, challenge=None):
        self.count = self.count + 1 if count is None else count
        cd = json.dumps({"type": kind, "challenge": challenge or options["challenge"], "origin": origin}).encode()
        ad = self._ad(flags, False, rp)
        sig = (key or self.key).sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64e(self.cred), "clientDataJSON": b64e(cd), "authenticatorData": b64e(ad), "signature": b64e(sig)}


@pytest.fixture(autouse=True)
def _fresh_guard():
    guard.reset()
    yield
    guard.reset()


async def signed_in(client, su, role="owner"):
    sid, email = await staff_with_password(su, role=role)
    j = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).json()
    return sid, email, {"Authorization": "Bearer " + j["access_token"]}


async def set_up(client, headers, phone=None):
    phone = phone or Phone()
    opts = (await client.post("/admin/v1/passkeys/options", headers=headers)).json()
    r = await client.post("/admin/v1/passkeys", json={**phone.create(opts), "name": "iPhone"}, headers=headers)
    assert r.status_code == 201, r.text
    return phone


async def face_login(client, phone, **kw):
    opts = (await client.post("/admin/v1/auth/passkey/options")).json()
    return await client.post("/admin/v1/auth/passkey/login", json=phone.get(opts, **kw))


# ------------------------------------------------------------------------------------------------ setting up and signing in
async def test_a_phone_sets_up_face_id_and_then_signs_in_with_it(client, su):
    sid, email, h = await signed_in(client, su)
    opts = (await client.post("/admin/v1/passkeys/options", headers=h)).json()
    assert opts["rp"]["id"] == RP and opts["user"]["name"] == email and opts["pubKeyCredParams"] == [{"type": "public-key", "alg": -7}]
    assert opts["authenticatorSelection"]["userVerification"] == "required" and opts["authenticatorSelection"]["residentKey"] == "required" and opts["attestation"] == "none"
    phone = Phone()
    r = await client.post("/admin/v1/passkeys", json={**phone.create(opts), "name": "iPhone"}, headers=h)
    assert r.status_code == 201 and r.json()["name"] == "iPhone"
    assert (await client.get("/admin/v1/passkeys", headers=h)).json()["items"][0]["name"] == "iPhone"
    assert await su.fetchval("SELECT count(*) FROM platform.passkeys WHERE staff_id=$1", sid) == 1
    stored = await su.fetchval("SELECT public_key FROM platform.passkeys WHERE staff_id=$1", sid)
    assert len(stored) == 65, "only the public half is kept"
    out = await face_login(client, phone)
    assert out.status_code == 200, out.text
    j = out.json()
    assert j["user"]["role"] == "owner" and j["refresh_token"] and j["remember"] is False and out.headers["cache-control"] == "no-store"
    me = await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["access_token"]})
    assert me.status_code == 200 and me.json()["email"] == email
    assert await su.fetchval("SELECT last_used_at IS NOT NULL FROM platform.passkeys WHERE staff_id=$1", sid)
    assert (await client.post("/admin/v1/auth/refresh", json={"refresh_token": j["refresh_token"]})).status_code == 200, "it is a normal sign-in afterwards"


async def test_a_challenge_works_once_and_only_for_what_it_was_made_for(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    opts = (await client.post("/admin/v1/auth/passkey/options")).json()
    body = phone.get(opts)
    assert (await client.post("/admin/v1/auth/passkey/login", json=body)).status_code == 200
    again = await client.post("/admin/v1/auth/passkey/login", json=body)
    assert again.status_code == 422 and again.json()["error"]["code"] == "passkey_invalid", "the same answer cannot be sent twice"
    reg = (await client.post("/admin/v1/passkeys/options", headers=h)).json()          # a setup challenge is no good for signing in
    assert (await client.post("/admin/v1/auth/passkey/login", json=phone.get(opts, challenge=reg["challenge"]))).status_code == 422
    made_up = b64e(os.urandom(32))
    assert (await client.post("/admin/v1/auth/passkey/login", json=phone.get(opts, challenge=made_up))).status_code == 422


async def test_an_answer_that_is_wrong_in_any_way_is_refused(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    for bad in ({"origin": "https://evil.test"}, {"rp": "evil.test"}, {"flags": 0x01}, {"flags": 0x04}, {"kind": "webauthn.create"}, {"key": ec.generate_private_key(ec.SECP256R1())}):
        r = await face_login(client, phone, **bad)
        assert r.status_code == 422, bad
    stranger = Phone()                                                                  # a phone nobody set up
    assert (await face_login(client, stranger)).status_code == 422
    assert (await face_login(client, phone)).status_code == 200, "and the real phone still works"


async def test_a_copied_key_is_noticed_when_the_counter_goes_back(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    assert (await face_login(client, phone, count=10)).status_code == 200
    assert (await face_login(client, phone, count=10)).status_code == 422
    assert (await face_login(client, phone, count=3)).status_code == 422
    assert (await face_login(client, phone, count=11)).status_code == 200


async def test_phones_that_keep_no_counter_still_work(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    for _ in range(2):
        assert (await face_login(client, phone, count=0)).status_code == 200                # iCloud keys always say 0


async def test_setup_refuses_keys_we_do_not_accept_and_answers_for_another_person(client, su):
    sid, email, h = await signed_in(client, su)
    opts = (await client.post("/admin/v1/passkeys/options", headers=h)).json()
    p = Phone()
    r = await client.post("/admin/v1/passkeys", json=p.create(opts, flags=0x41), headers=h)               # no Face ID check
    assert r.status_code == 422
    opts = (await client.post("/admin/v1/passkeys/options", headers=h)).json()
    assert (await client.post("/admin/v1/passkeys", json=p.create(opts, origin="https://evil.test"), headers=h)).status_code == 422
    sid2, _, h2 = await signed_in(client, su)
    opts = (await client.post("/admin/v1/passkeys/options", headers=h)).json()
    assert (await client.post("/admin/v1/passkeys", json=p.create(opts), headers=h2)).status_code == 422, "a challenge made for one person is no good for another"
    assert await su.fetchval("SELECT count(*) FROM platform.passkeys WHERE staff_id = ANY($1::uuid[])", [sid, sid2]) == 0
    assert (await client.post("/admin/v1/passkeys/options")).status_code == 401, "setting up needs a signed-in person"


async def test_the_same_phone_cannot_be_set_up_twice_and_the_options_list_it(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    opts = (await client.post("/admin/v1/passkeys/options", headers=h)).json()
    assert opts["excludeCredentials"][0]["id"] == b64e(phone.cred)
    assert (await client.post("/admin/v1/passkeys", json=phone.create(opts), headers=h)).status_code == 409


# ------------------------------------------------------------------------------------------------ removing and losing a phone
async def test_a_person_removes_their_own_phone_and_nobody_elses(client, su):
    sid, email, h = await signed_in(client, su)
    sid2, email2, h2 = await signed_in(client, su, "support")
    p1, p2 = await set_up(client, h), await set_up(client, h2)
    key = (await client.get("/admin/v1/passkeys", headers=h)).json()["items"][0]["id"]
    assert (await client.delete(f"/admin/v1/passkeys/{key}", headers=h2)).status_code == 404, "not theirs"
    assert (await client.delete(f"/admin/v1/passkeys/{key}", headers=h)).status_code == 204
    assert (await face_login(client, p1)).status_code == 422 and (await face_login(client, p2)).status_code == 200


async def test_an_owner_or_admin_removes_every_face_id_of_a_person_who_lost_their_phone_and_support_cannot(client, su):
    sid, email, h = await signed_in(client, su)
    admin_id, _, ha = await signed_in(client, su, "admin")
    support_id, _, hs = await signed_in(client, su, "support")
    phone = await set_up(client, hs)
    assert (await client.delete(f"/admin/v1/passkeys/for/{support_id}", headers=hs)).status_code == 403
    listed = {r["id"]: r["passkeys"] for r in (await client.get("/admin/v1/staff", headers=ha)).json()["items"]}
    assert listed[str(support_id)] == 1 and listed[str(sid)] == 0
    assert (await client.delete(f"/admin/v1/passkeys/for/{support_id}", headers=ha)).status_code == 204
    assert (await face_login(client, phone)).status_code == 422
    assert await su.fetchval("SELECT count(*) FROM platform.audit_log WHERE action='passkey.remove_all'") == 1


async def test_a_new_password_from_a_reset_link_ends_face_id_and_a_disabled_person_cannot_use_it(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    from app.services import auth
    from app.db import db
    async with db.admin_tx() as conn:
        raw, _ = await auth.make_link_token(conn, "reset", sid, sid)
    assert (await client.post("/admin/v1/auth/set-password", json={"token": raw, "password": "a brand new long phrase 42"})).status_code == 204
    assert await su.fetchval("SELECT count(*) FROM platform.passkeys WHERE staff_id=$1", sid) == 0
    sid2, _, h2 = await signed_in(client, su)
    p2 = await set_up(client, h2)
    await su.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", sid2)
    assert (await face_login(client, p2)).status_code == 422


async def test_old_challenges_are_cleaned_up_and_expire(client, su):
    sid, email, h = await signed_in(client, su)
    phone = await set_up(client, h)
    opts = (await client.post("/admin/v1/auth/passkey/options")).json()
    await su.execute("UPDATE platform.passkey_challenges SET created_at = now() - interval '6 minutes'")
    assert (await face_login_with(client, phone, opts)).status_code == 422, "too slow"
    await client.post("/admin/v1/auth/passkey/options")
    assert await su.fetchval("SELECT count(*) FROM platform.passkey_challenges WHERE created_at < now() - interval '5 minutes'") == 0


async def face_login_with(client, phone, opts):
    return await client.post("/admin/v1/auth/passkey/login", json=phone.get(opts))


def test_the_reader_of_phone_answers_handles_what_a_passkey_sends():
    value = {"fmt": "none", "attStmt": {}, "authData": b"\x01\x02", "n": [1, -2, 300, True, "x"], -3: b"yz"}
    got, end = passkeys.cbor(cbor(value))
    assert got == value and end == len(cbor(value))
    for junk in (b"", b"\xff", b"\xa1", b"\x5f", b"\x42a"):
        with pytest.raises(Exception):
            passkeys.cbor(junk)


# ---------------------------------------------------------------- Face ID as the second step for owners and admins
from app.security import issue_token


async def pw_login(client, email, **extra):
    return await client.post("/admin/v1/auth/login", json={"email": email, "password": PW, **extra})


async def second(client, ticket, answer):
    return await client.post("/admin/v1/auth/passkey/second", json={"ticket": ticket, **answer})


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owners_and_admins_with_face_id_must_pass_it_after_the_password(client, su, role):
    sid, email, headers = await signed_in(client, su, role)
    phone = await set_up(client, headers)
    r = await pw_login(client, email)
    j = r.json()
    assert r.status_code == 200 and j["second_step"] == "passkey" and "access_token" not in j and "refresh_token" not in j
    done = await second(client, j["ticket"], phone.get(j["options"]))
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["user"]["role"] == role and out["refresh_token"]
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + out["access_token"]})).status_code == 200


async def test_a_role_that_is_not_in_the_list_signs_in_with_the_password_alone(client, su):
    sid, email, headers = await signed_in(client, su, "support")
    await set_up(client, headers)
    j = (await pw_login(client, email)).json()
    assert "second_step" not in j and j["access_token"]


async def test_someone_who_has_not_set_up_face_id_is_not_locked_out(client, su):
    sid, email = await staff_with_password(su, role="owner")
    j = (await pw_login(client, email)).json()
    assert "second_step" not in j and j["access_token"]


async def test_the_password_step_alone_gives_no_access_and_the_note_is_not_a_sign_in_token(client, su):
    sid, email, headers = await signed_in(client, su)
    await set_up(client, headers)
    j = (await pw_login(client, email)).json()
    assert (await client.get("/admin/v1/me", headers={"Authorization": "Bearer " + j["ticket"]})).status_code == 401, "the note must not work as an access token"
    ok = (await client.post("/admin/v1/auth/login", json={"email": email, "password": PW})).json()
    assert ok["second_step"] == "passkey"
    # and an ordinary access token is not accepted as the note
    access = issue_token("platform", {"sub": str(sid)}, 300)
    phone_answer = {"id": "x" * 10, "clientDataJSON": "y" * 30, "authenticatorData": "z" * 50, "signature": "s" * 50}
    assert (await second(client, access, phone_answer)).status_code in (401, 422)


async def test_another_persons_face_id_does_not_finish_my_sign_in(client, su):
    sid, email, headers = await signed_in(client, su)
    await set_up(client, headers)
    sid2, email2, headers2 = await signed_in(client, su, "owner")
    other = await set_up(client, headers2)
    j = (await pw_login(client, email)).json()
    r = await second(client, j["ticket"], other.get(j["options"]))
    assert r.status_code == 401


async def test_a_wrong_or_old_note_gets_nothing(client, su):
    sid, email, headers = await signed_in(client, su)
    phone = await set_up(client, headers)
    j = (await pw_login(client, email)).json()
    answer = phone.get(j["options"])
    assert (await second(client, "not-a-note-at-all-" + "x" * 20, answer)).status_code == 401
    expired = issue_token("ticket", {"sub": str(sid)}, -10)
    assert (await second(client, expired, answer)).status_code == 401


async def test_the_phones_answer_works_once(client, su):
    sid, email, headers = await signed_in(client, su)
    phone = await set_up(client, headers)
    j = (await pw_login(client, email)).json()
    answer = phone.get(j["options"])
    assert (await second(client, j["ticket"], answer)).status_code == 200
    assert (await second(client, j["ticket"], answer)).status_code == 422, "a challenge is spent by its first use"


async def test_staying_signed_in_survives_the_second_step(client, su):
    sid, email, headers = await signed_in(client, su)
    phone = await set_up(client, headers)
    j = (await pw_login(client, email, remember=True)).json()
    out = (await second(client, j["ticket"], phone.get(j["options"]))).json()
    assert out["remember"] is True


async def test_a_wrong_password_still_gets_no_note(client, su):
    sid, email, headers = await signed_in(client, su)
    await set_up(client, headers)
    r = await client.post("/admin/v1/auth/login", json={"email": email, "password": "definitely wrong!!"})
    assert r.status_code == 401 and "ticket" not in r.text
