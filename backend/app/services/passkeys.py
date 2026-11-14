"""Sign in with Face ID or a fingerprint (passkeys, the WebAuthn standard).

Setup (signed in, once per phone): the server hands the phone a one-time challenge; the phone makes a key pair behind Face ID, keeps the private half and sends back the public half.
Sign-in: the server sends a new challenge; the phone signs it after Face ID; we check the signature with the saved public half and issue the same tokens as a password sign-in.
Only the public half is stored, so a copy of the database cannot sign anyone in. A challenge works once and for five minutes. The key is tied to this site's address (RP ID), so a look-alike site gets nothing.
Only ES256 (P-256) keys are accepted; every phone makes these for passkeys. We ask for no proof of the device make (attestation "none"). Face ID itself is required (user verification), not just a tap."""
import base64
import hashlib
import json
import os
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from ..config import get_settings
from ..errors import ApiError

CHALLENGE_MINUTES = 5
MAX_KEYS = 10
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception:
        raise ApiError(422, "passkey_invalid", "That did not work. Try again.")


def rp() -> tuple[str, str]:
    """(RP ID, origin): the site's name as the browser sees it, and its full address."""
    u = urlsplit(get_settings().public_base_url)
    return u.hostname or "localhost", f"{u.scheme}://{u.netloc}"


def _bad(msg="That did not work. Try again.") -> ApiError:
    return ApiError(422, "passkey_invalid", msg)


# ------------------------------------------------------------------------------------------------ a small CBOR reader (what a phone sends back is CBOR)
def cbor(data: bytes, i: int = 0):
    """Read one CBOR value starting at i. Returns (value, next index). Only what a passkey answer needs: numbers, bytes, text, lists, maps, true/false/null."""
    try:
        head = data[i]
        major, info = head >> 5, head & 31
        i += 1
        if info < 24:
            n = info
        elif info in (24, 25, 26, 27):
            size = {24: 1, 25: 2, 26: 4, 27: 8}[info]
            n = int.from_bytes(data[i:i + size], "big")
            i += size
        else:
            raise ValueError("unsupported")
        if major == 0:
            return n, i
        if major == 1:
            return -1 - n, i
        if major in (2, 3):
            chunk = data[i:i + n]
            if len(chunk) != n:
                raise ValueError("short")
            return (bytes(chunk) if major == 2 else chunk.decode()), i + n
        if major == 4:
            out = []
            for _ in range(n):
                v, i = cbor(data, i)
                out.append(v)
            return out, i
        if major == 5:
            out = {}
            for _ in range(n):
                k, i = cbor(data, i)
                v, i = cbor(data, i)
                out[k] = v
            return out, i
        if major == 7 and info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], i
    except (IndexError, ValueError, UnicodeDecodeError):
        pass
    raise _bad()


# ------------------------------------------------------------------------------------------------ challenges
async def new_challenge(conn, purpose: str, staff_id=None) -> str:
    await conn.execute("DELETE FROM platform.passkey_challenges WHERE created_at < now() - make_interval(mins => $1)", CHALLENGE_MINUTES)
    c = b64e(os.urandom(32))
    await conn.execute("INSERT INTO platform.passkey_challenges (challenge, purpose, staff_id) VALUES ($1,$2,$3)", c, purpose, staff_id)
    return c


async def _use_challenge(conn, challenge: str, purpose: str, staff_id=None) -> None:
    """A challenge works once: taking it is what spends it."""
    got = await conn.fetchval(
        """DELETE FROM platform.passkey_challenges WHERE challenge=$1 AND purpose=$2 AND ($3::uuid IS NULL OR staff_id=$3)
           AND created_at > now() - make_interval(mins => $4) RETURNING challenge""", challenge, purpose, staff_id, CHALLENGE_MINUTES)
    if got is None:
        raise _bad("This took too long or was already used. Try again.")


def _client_data(raw: str, expected_type: str) -> tuple[bytes, str]:
    data = b64d(raw)
    try:
        j = json.loads(data)
    except ValueError:
        raise _bad()
    _, origin = rp()
    if j.get("type") != expected_type or j.get("origin") != origin or j.get("crossOrigin") is True:
        raise _bad()
    return data, str(j.get("challenge", ""))


def _auth_data(ad: bytes, need_key: bool) -> dict:
    if len(ad) < 37:
        raise _bad()
    rp_id, _ = rp()
    if ad[:32] != hashlib.sha256(rp_id.encode()).digest():
        raise _bad()
    flags = ad[32]
    if not (flags & FLAG_UP and flags & FLAG_UV):
        raise _bad("Face ID or your fingerprint is needed to do this.")
    out = {"flags": flags, "count": int.from_bytes(ad[33:37], "big")}
    if need_key:
        if not flags & FLAG_AT or len(ad) < 55:
            raise _bad()
        n = int.from_bytes(ad[53:55], "big")
        out["credential_id"] = ad[55:55 + n]
        key, _ = cbor(ad, 55 + n)
        if not isinstance(key, dict) or key.get(1) != 2 or key.get(3) != -7 or key.get(-1) != 1 or len(key.get(-2, b"")) != 32 or len(key.get(-3, b"")) != 32:
            raise _bad("This phone made a kind of key we do not accept.")
        out["public_key"] = b"\x04" + key[-2] + key[-3]
    return out


# ------------------------------------------------------------------------------------------------ setting up a phone
async def registration_options(conn, staff_id) -> dict:
    u = await conn.fetchrow("SELECT id, name, email::text AS email FROM platform.staff_users WHERE id=$1", staff_id)
    have = await conn.fetch("SELECT credential_id FROM platform.passkeys WHERE staff_id=$1", staff_id)
    if len(have) >= MAX_KEYS:
        raise ApiError(409, "too_many_keys", "Remove one of your Face ID phones first.")
    rp_id, _ = rp()
    return {"challenge": await new_challenge(conn, "register", staff_id), "rp": {"id": rp_id, "name": "Jas iVision"},
            "user": {"id": b64e(u["id"].bytes), "name": u["email"], "displayName": u["name"]},
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}], "timeout": 60000, "attestation": "none",
            "authenticatorSelection": {"authenticatorAttachment": "platform", "residentKey": "required", "requireResidentKey": True, "userVerification": "required"},
            "excludeCredentials": [{"type": "public-key", "id": r["credential_id"]} for r in have]}


async def register(conn, staff_id, client_data_json: str, attestation_object: str, name: str) -> dict:
    data, challenge = _client_data(client_data_json, "webauthn.create")
    await _use_challenge(conn, challenge, "register", staff_id)
    att, _ = cbor(b64d(attestation_object))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise _bad()
    ad = _auth_data(att["authData"], need_key=True)
    cid = b64e(ad["credential_id"])
    if await conn.fetchval("SELECT 1 FROM platform.passkeys WHERE credential_id=$1", cid):
        raise ApiError(409, "already_set_up", "This phone already has Face ID set up for a sign-in.")
    row = await conn.fetchrow("INSERT INTO platform.passkeys (staff_id, credential_id, public_key, sign_count, name) VALUES ($1,$2,$3,$4,$5) RETURNING id, name, created_at",
                              staff_id, cid, ad["public_key"], ad["count"], (name or "This phone")[:60])
    return dict(row)


# ------------------------------------------------------------------------------------------------ signing in
async def login_options(conn) -> dict:
    rp_id, _ = rp()
    return {"challenge": await new_challenge(conn, "login"), "rpId": rp_id, "timeout": 60000, "userVerification": "required"}


async def check_login(conn, credential_id: str, client_data_json: str, authenticator_data: str, signature: str):
    """Returns the staff id when the phone's answer is genuine. Anything wrong is the same plain refusal."""
    row = await conn.fetchrow("SELECT p.id, p.staff_id, p.public_key, p.sign_count, u.status FROM platform.passkeys p JOIN platform.staff_users u ON u.id = p.staff_id WHERE p.credential_id=$1", credential_id)
    data, challenge = _client_data(client_data_json, "webauthn.get")
    await _use_challenge(conn, challenge, "login")
    if not row or row["status"] != "active":
        raise _bad("This phone is not set up for Face ID, or it was removed.")
    ad_raw = b64d(authenticator_data)
    ad = _auth_data(ad_raw, need_key=False)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(row["public_key"]))
    try:
        key.verify(b64d(signature), ad_raw + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise _bad()
    if row["sign_count"] and ad["count"] <= row["sign_count"]:            # the counter went backwards: this key was copied
        raise _bad("This sign-in key cannot be used. Remove Face ID and set it up again.")
    await conn.execute("UPDATE platform.passkeys SET sign_count=$2, last_used_at=now() WHERE id=$1", row["id"], ad["count"])
    return row["staff_id"]
