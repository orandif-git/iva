"""Notifications on a console user's phone: the message is encrypted so only that phone can read it, each person gets what their role (or their own switches) says, and the watcher tells once."""
import base64
import json
import uuid
from datetime import datetime

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings
from app.db import db
from app.services import staff_push


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def unseal(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What a phone does with a push body (RFC 8291 section 3.4)."""
    salt, idlen = body[:16], body[20]
    as_pub, record = body[21:21 + idlen], body[21 + idlen:]
    ua_pub = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    return AESGCM(cek).decrypt(nonce, record, None).rstrip(b"\x00")[:-1]


class Phone:
    """A pretend phone: it has keys, and the 'push service' it gives out is a function that records what arrives."""
    def __init__(self, n=1):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"a" * 16
        self.endpoint = f"https://fcm.googleapis.com/fcm/send/{n}-{id(self)}" + "x" * 10
        self.got = []
        self.status = 201

    @property
    def keys(self):
        pub = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return {"p256dh": base64.urlsafe_b64encode(pub).rstrip(b"=").decode(), "auth": base64.urlsafe_b64encode(self.auth).rstrip(b"=").decode()}


@pytest.fixture(autouse=True)
async def _empty(su):
    """Every test starts with no phones and nothing remembered, whatever the tests before it left behind."""
    await su.execute("DELETE FROM platform.push_subscriptions")
    await su.execute("DELETE FROM platform.push_state")


@pytest.fixture
def push(monkeypatch):
    s = get_settings()
    pub, priv = staff_push.keygen()
    monkeypatch.setattr(s, "vapid_public_key", pub, raising=False)
    monkeypatch.setattr(s, "vapid_private_key", priv, raising=False)
    phones: dict[str, Phone] = {}

    def handler(request: httpx.Request):
        p = phones[str(request.url)]
        p.got.append((request, json.loads(unseal(request.content, p.key, p.auth))))
        return httpx.Response(p.status)
    monkeypatch.setattr(staff_push, "transport", httpx.MockTransport(handler))

    class Box:
        public = pub

        def phone(self):
            p = Phone(len(phones))
            phones[p.endpoint] = p
            return p
    return Box()


async def subscribe(client, who, phone):
    r = await client.post("/admin/v1/push/subscribe", json={"endpoint": phone.endpoint, "keys": phone.keys, "device": "iPhone"}, headers=who["headers"])
    assert r.status_code == 204, r.text


# ------------------------------------------------------------------------------------------------ the message itself
def test_the_message_is_made_the_way_the_standard_says():
    """The worked example in RFC 8291 appendix A: the same inputs must give the same bytes."""
    sender = ec.derive_private_key(int.from_bytes(b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    out = staff_push.encrypt(b"When I grow up, I want to be a watermelon", "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                             "BTBZMqHH6r4Tts7J_aSIgg", sender=sender, salt=b64d("DGv6ra1nlYgDCS1FRnbzlw"))
    assert base64.urlsafe_b64encode(out).rstrip(b"=").decode() == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_a_phone_can_read_what_was_sent_and_nobody_else_can():
    phone = Phone()
    body = staff_push.encrypt(b'{"title":"Hi"}', phone.keys["p256dh"], phone.keys["auth"])
    assert unseal(body, phone.key, phone.auth) == b'{"title":"Hi"}'
    other = Phone()
    with pytest.raises(Exception):
        unseal(body, other.key, other.auth)


# ------------------------------------------------------------------------------------------------ who gets what
def test_each_role_starts_with_its_own_notifications_and_a_persons_own_switches_win():
    assert staff_push.chosen("owner", None) == set(staff_push.KEYS) == staff_push.chosen("admin", None)
    assert staff_push.chosen("support", None) == {"customers"} and staff_push.chosen("onboarding", None) == {"ending"}
    assert staff_push.chosen("support", {"customers": False, "held": True}) == {"held"}
    assert staff_push.chosen("owner", {"daily": False, "nonsense": True}) == set(staff_push.KEYS) - {"daily"}


async def test_the_screen_shows_the_role_defaults_and_saves_what_the_person_changes(client, make, push):
    support = await make.staff("support")
    s = (await client.get("/admin/v1/push", headers=support["headers"])).json()
    assert s["enabled"] is True and s["public_key"] == push.public and {c["key"] for c in s["categories"] if c["on"]} == {"customers"} and s["devices"] == 0
    r = await client.put("/admin/v1/push/preferences", json={"categories": {"held": True, "customers": False}}, headers=support["headers"])
    assert r.status_code == 200 and r.json()["categories"] == ["held"]
    assert (await client.put("/admin/v1/push/preferences", json={"categories": {"x": True}}, headers=support["headers"])).status_code == 422


async def test_notifications_say_so_when_the_server_has_no_keys(client, make, monkeypatch):
    monkeypatch.setattr(get_settings(), "vapid_public_key", "", raising=False)
    owner = await make.staff("owner")
    s = (await client.get("/admin/v1/push", headers=owner["headers"])).json()
    assert s["enabled"] is False and s["public_key"] is None
    phone = Phone()
    r = await client.post("/admin/v1/push/subscribe", json={"endpoint": phone.endpoint, "keys": phone.keys}, headers=owner["headers"])
    assert r.status_code == 503


async def test_a_phone_turns_notifications_on_gets_a_test_and_turns_them_off(client, make, push):
    owner = await make.staff("owner")
    phone = push.phone()
    assert (await client.post("/admin/v1/push/test", headers=owner["headers"])).status_code == 409, "nothing is turned on yet"
    await subscribe(client, owner, phone)
    assert (await client.get("/admin/v1/push", headers=owner["headers"])).json()["devices"] == 1
    t = await client.post("/admin/v1/push/test", headers=owner["headers"])
    assert t.status_code == 200 and t.json()["ok"] == 1
    req, msg = phone.got[0]
    assert msg["title"] == "Jas iVision" and "test" in msg["body"].lower() and msg["url"] == "/console/#more"
    assert req.headers["content-encoding"] == "aes128gcm" and req.headers["ttl"] == "86400"
    auth = req.headers["authorization"]                                    # the sender's proof: a signed note that names the push service and us
    token, key = auth[len("vapid t="):].split(", k=")
    claims = jwt.decode(token, ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(key)), algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert key == push.public and claims["sub"]
    await client.post("/admin/v1/push/unsubscribe", json={"endpoint": phone.endpoint}, headers=owner["headers"])
    assert (await client.get("/admin/v1/push", headers=owner["headers"])).json()["devices"] == 0


async def test_one_person_cannot_turn_off_or_test_another_persons_phone(client, make, push):
    a, b = await make.staff("owner"), await make.staff("support")
    phone = push.phone()
    await subscribe(client, a, phone)
    await client.post("/admin/v1/push/unsubscribe", json={"endpoint": phone.endpoint}, headers=b["headers"])
    assert (await client.get("/admin/v1/push", headers=a["headers"])).json()["devices"] == 1
    assert (await client.post("/admin/v1/push/test", headers=b["headers"])).status_code == 409


async def test_a_notification_goes_to_the_roles_and_people_who_chose_it_on_every_phone_they_have(client, make, push):
    people = {r: await make.staff(r) for r in ("owner", "admin", "support", "onboarding")}
    phones = {}
    for r, who in people.items():
        phones[r] = push.phone()
        await subscribe(client, who, phones[r])
    second = push.phone()
    await subscribe(client, people["support"], second)                      # support has two phones
    async with db.admin_tx() as conn:
        out = await staff_push.notify(conn, "customers", "Lakshmi Jewellers: recorder offline", "Dr SMS is offline.", "/console/#customer/x/health")
        assert out["ok"] == 4                                              # owner, admin, support (two phones)
        await staff_push.notify(conn, "ending", "1 customer ending soon", "Vel Auto Spares (2 days)")
    assert [len(phones[r].got) for r in ("owner", "admin", "support", "onboarding")] == [2, 2, 1, 1]
    assert len(second.got) == 1 and phones["support"].got[0][1]["title"].endswith("recorder offline") and phones["onboarding"].got[0][1]["body"].startswith("Vel Auto")
    await client.put("/admin/v1/push/preferences", json={"categories": {"customers": False}}, headers=people["owner"]["headers"])
    async with db.admin_tx() as conn:
        await staff_push.notify(conn, "customers", "again", "x")
    assert len(phones["owner"].got) == 2, "the owner switched this kind off"


async def test_a_phone_that_has_gone_is_forgotten_and_a_disabled_person_gets_nothing(client, make, push, su):
    owner, other = await make.staff("owner"), await make.staff("admin")
    gone, fine = push.phone(), push.phone()
    gone.status = 410
    await subscribe(client, owner, gone)
    await subscribe(client, other, fine)
    await su.execute("UPDATE platform.staff_users SET status='disabled' WHERE id=$1", other["id"])
    async with db.admin_tx() as conn:
        out = await staff_push.notify(conn, "server", "Server problem", "x")
    assert out == {"ok": 0, "gone": 1, "error": 0} and not fine.got
    assert await su.fetchval("SELECT count(*) FROM platform.push_subscriptions WHERE staff_id=$1", owner["id"]) == 0


# ------------------------------------------------------------------------------------------------ the watcher
async def test_a_recorder_going_offline_is_announced_once_and_when_it_comes_back(client, make, push, su):
    owner = await make.staff("owner")
    phone = push.phone()
    await subscribe(client, owner, phone)
    shop = f"Lakshmi {uuid.uuid4().hex[:6]}"
    t = await make.tenant(shop)
    rid = await su.fetchval("INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, name, status) VALUES ($1,'DEV-PUSH','dahua','auto_register','Dr SMS','online') RETURNING id", t)

    def mine():                                                            # other tests may leave other customers' recorders behind; only this one is judged
        return [m for _, m in phone.got if m["title"].startswith(shop)]
    async with db.admin_tx() as conn:
        await staff_push.check_recorders(conn)                              # first sight: quiet
    assert not phone.got
    await su.execute("UPDATE app.recorders SET status='offline' WHERE id=$1", rid)
    for _ in range(3):                                                     # a minute passes, three times: still only one note
        async with db.admin_tx() as conn:
            await staff_push.check_recorders(conn)
    assert len(mine()) == 1 and mine()[0]["title"] == f"{shop}: recorder offline" and mine()[0]["url"] == f"/console/#customer/{t}/health"
    await su.execute("UPDATE app.recorders SET status='online' WHERE id=$1", rid)
    async with db.admin_tx() as conn:
        await staff_push.check_recorders(conn)
    assert len(mine()) == 2 and "back" in mine()[1]["title"]


async def test_a_server_problem_is_announced_once_and_so_is_the_all_clear(client, make, push, monkeypatch):
    owner = await make.staff("owner")
    phone = push.phone()
    await subscribe(client, owner, phone)
    state = {"p": [("disk", "The server disk is 96% full, about 1 days left.")]}

    async def fake(conn):
        return state["p"]
    monkeypatch.setattr(staff_push, "server_problems", fake)
    for _ in range(3):
        async with db.admin_tx() as conn:
            await staff_push.check_server(conn)
    assert [m["title"] for _, m in phone.got] == ["Server problem"] and "96%" in phone.got[0][1]["body"]
    state["p"] = [("disk", "x"), ("worker", "The analysis worker is not reporting. Restart it on the server.")]
    async with db.admin_tx() as conn:
        await staff_push.check_server(conn)
    assert len(phone.got) == 2 and "worker" in phone.got[1][1]["body"], "only the new problem is announced"
    state["p"] = []
    async with db.admin_tx() as conn:
        await staff_push.check_server(conn)
    assert phone.got[-1][1]["title"] == "Server is back to normal"


async def test_customers_ending_soon_and_the_morning_summary_come_once_a_day_at_the_right_time(client, make, push, su):
    owner = await make.staff("owner")
    phone = push.phone()
    await subscribe(client, owner, phone)
    bakery = f"Bakery {uuid.uuid4().hex[:6]}"
    t = await make.tenant(bakery)
    await su.execute("UPDATE app.tenants SET ends_on = (now() at time zone 'Asia/Kolkata')::date + 4 WHERE id=$1", t)
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    early = datetime(today.year, today.month, today.day, 7, 0, tzinfo=ist)
    async with db.admin_tx() as conn:
        await staff_push.check_daily(conn, early)
    assert not phone.got, "too early"
    for hour in (8, 9, 9, 10):
        async with db.admin_tx() as conn:
            await staff_push.check_daily(conn, datetime(today.year, today.month, today.day, hour, 45, tzinfo=ist))
    titles = [m["title"] for _, m in phone.got]
    assert titles.count("Good morning") == 1 and any("ending soon" in x for x in titles) and len(titles) == 2
    assert any(f"{bakery} (4 days)" in m["body"] for _, m in phone.got)


async def test_messages_that_fail_are_announced_but_not_more_than_every_half_hour(client, make, push, su):
    owner = await make.staff("owner")
    phone = push.phone()
    await subscribe(client, owner, phone)
    t = await make.tenant(f"Medicals {uuid.uuid4().hex[:6]}")

    async def fail_one():
        await su.execute("INSERT INTO app.notifications (tenant_id, channel, recipient, status, event) VALUES ($1,'whatsapp','+919800000000','failed','system.camera_offline')", t)
    async with db.admin_tx() as conn:
        await staff_push.check_failed(conn)                                # remembers 0
    await fail_one()
    async with db.admin_tx() as conn:
        await staff_push.check_failed(conn)
    await fail_one()
    async with db.admin_tx() as conn:
        await staff_push.check_failed(conn)
    assert len(phone.got) == 1 and "1 more message" in phone.got[0][1]["body"]


# ---------------------------------------------------------------- only real push services may be called
import pytest
from app.services import staff_push


@pytest.mark.parametrize("url", ["https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/abc", "https://web.push.apple.com/QWxv",
                                 "https://wns2-par02p.notify.windows.com/w/?token=abc", "https://eu.push.apple.com/x"])
def test_the_push_services_of_the_phone_makers_are_accepted(url):
    staff_push.check_endpoint(url)


@pytest.mark.parametrize("url", ["https://127.0.0.1/x", "https://localhost/x", "https://169.254.169.254/latest/meta-data", "https://10.0.0.5:6379/", "https://internal.example.com/hook",
                                 "http://fcm.googleapis.com/fcm/send/abc", "https://fcm.googleapis.com:8443/x", "https://user:pw@fcm.googleapis.com/x",
                                 "https://fcm.googleapis.com.evil.test/x", "https://evilpush.apple.com.evil.test/x", "https://notify.windows.com.evil.test/x", "ftp://fcm.googleapis.com/x"])
def test_any_other_address_is_refused(url):
    with pytest.raises(ValueError):
        staff_push.check_endpoint(url)


async def test_a_phone_cannot_register_an_address_that_is_not_a_push_service(client, make, push):
    staff = await make.staff("support")
    r = await client.post("/admin/v1/push/subscribe", json={"endpoint": "https://10.0.0.5:6379/internal-service-path", "keys": {"p256dh": "x" * 20, "auth": "y" * 20}}, headers=staff["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "push_address_not_allowed"


async def test_a_saved_address_that_is_not_allowed_is_never_called_and_is_forgotten(su, push):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(201)
    staff_push.transport = httpx.MockTransport(handler)
    try:
        res = await staff_push.send_one({"endpoint": "https://10.0.0.5/x" + "x" * 10, "p256dh": "x", "auth": "y"}, {"title": "t"})
    finally:
        staff_push.transport = None
    assert res == "gone" and calls == []
