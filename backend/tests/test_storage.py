"""The picture kept for a plate read, and the evidence storage settings in the owner console."""
import uuid
from datetime import timedelta

import pytest

from app.messaging import evidence
from tests.helpers import iso, now, setup_cameras

JPEG = b"\xff\xd8\xff\xe0" + b"jpegdata" * 20


@pytest.fixture(autouse=True)
async def clean(su):
    await su.execute("DELETE FROM platform.storage_settings")
    evidence.configure(None)
    evidence.set_store(None)
    yield
    await su.execute("DELETE FROM platform.storage_settings")
    evidence.configure(None)
    evidence.set_store(None)


async def read_with_picture(client, make, cam, picture=True, plate="GJ14AW1013"):
    eid = None
    if picture:
        up = await client.post("/internal/v1/evidence", params={"camera_id": cam}, content=JPEG, headers={**make.service(), "Content-Type": "image/jpeg"})
        assert up.status_code == 201, up.text
        eid = up.json()["evidence_id"]
    e = {"camera_id": cam, "seen_at": iso(now() - timedelta(minutes=5)), "plate_raw": plate, "confidence": 95, "direction": "in", "track_id": uuid.uuid4().hex[:10],
         "snapshot_key": "s/1.jpg", "vehicle_type": "Car", **({"evidence_id": eid} if eid else {})}
    r = await client.post("/internal/v1/events/plates", json={"events": [e]}, headers=make.service())
    assert r.status_code == 200 and r.json()["accepted"] == 1, r.text
    return eid


async def first_plate(client, u):
    r = await client.get("/v1/plates", headers=u["headers"])
    return r.json()["items"][0]


# ------------------------------------------------------------------------------------------- plate pictures
async def test_a_plate_read_keeps_its_picture_and_the_customer_can_see_it(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await read_with_picture(client, make, cam)
    p = await first_plate(client, u)
    assert p["has_snapshot"] is True
    img = await client.get(f"/v1/plates/{p['id']}/image", headers=u["headers"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content == JPEG and "private" in img.headers["cache-control"]


async def test_a_read_without_a_picture_says_so_and_nobody_sees_anothers_picture(client, make):
    t, u, _, (cam,) = await setup_cameras(client, make)
    await read_with_picture(client, make, cam, picture=False)
    p = await first_plate(client, u)
    assert p["has_snapshot"] is False
    assert (await client.get(f"/v1/plates/{p['id']}/image", headers=u["headers"])).json()["error"]["code"] == "picture_not_found"
    # another customer cannot fetch a picture by guessing the number, and cannot attach someone else's picture to their own reads
    t2, u2, _, (cam2,) = await setup_cameras(client, make)
    await read_with_picture(client, make, cam2, plate="MH12QR4471")
    mine = await first_plate(client, u2)
    assert (await client.get(f"/v1/plates/{mine['id']}/image", headers=u["headers"])).status_code == 404
    assert (await client.get(f"/v1/plates/{mine['id']}/image")).status_code == 401
    theirs = (await client.get("/v1/plates", headers=u2["headers"])).json()["items"][0]
    eid = await read_with_picture(client, make, cam, plate="GJ01CK7710")
    stray = {"camera_id": cam2, "seen_at": iso(now() - timedelta(minutes=2)), "plate_raw": "TN72AB1234", "confidence": 95, "direction": "in", "track_id": "x1", "snapshot_key": "s/2.jpg", "evidence_id": eid}
    await client.post("/internal/v1/events/plates", json={"events": [stray]}, headers=make.service())
    got = [i for i in (await client.get("/v1/plates", headers=u2["headers"])).json()["items"] if i["plate_text"] == "TN72AB1234"]
    assert got and got[0]["has_snapshot"] is False
    del theirs


async def test_the_picture_goes_when_the_evidence_expires(client, make, su):
    from app.services import retention
    t, u, _, (cam,) = await setup_cameras(client, make)
    eid = await read_with_picture(client, make, cam)
    await su.execute("UPDATE app.evidence SET expires_at = now() - interval '1 day' WHERE id=$1", uuid.UUID(eid))
    await retention.run_all()
    p = await first_plate(client, u)
    assert p["has_snapshot"] is False
    assert (await client.get(f"/v1/plates/{p['id']}/image", headers=u["headers"])).status_code == 404


# ------------------------------------------------------------------------------------- storage settings
R2 = {"backend": "r2", "account_id": "a" * 32, "bucket": "ivision-evidence", "access_key_id": "AKIAEXAMPLEKEY01", "secret_access_key": "SUPER-SECRET-VALUE", "reason": "Move evidence to R2"}


class FakeR2:
    puts = []
    fail = None

    def __init__(self, account_id, key_id, secret, bucket, endpoint=""):
        self.args = (account_id, key_id, secret, bucket, endpoint)
        self.files = {}
        if FakeR2.fail:
            raise RuntimeError(FakeR2.fail.replace("{secret}", secret).replace("{key}", key_id))

    async def put(self, key, data, content_type):
        self.files[key] = data
        FakeR2.puts.append(key)

    async def get(self, key):
        return self.files.get(key)

    async def delete(self, key):
        self.files.pop(key, None)


@pytest.fixture
def fake_r2(monkeypatch):
    FakeR2.puts, FakeR2.fail = [], None
    monkeypatch.setattr(evidence, "R2Store", FakeR2)
    return FakeR2


async def test_only_the_owner_sees_or_changes_storage(client, make):
    owner, support = await make.staff("owner"), await make.staff("support")
    assert (await client.get("/admin/v1/storage", headers=support["headers"])).status_code == 403
    assert (await client.put("/admin/v1/storage", json=R2, headers=support["headers"])).status_code == 403
    assert (await client.post("/admin/v1/storage/test", json={}, headers=support["headers"])).status_code == 403
    r = (await client.get("/admin/v1/storage", headers=owner["headers"])).json()
    assert r["backend"] in ("local", "r2") and r["source"] == "server" and "files" in r and "secret_access_key" not in r


async def test_a_working_r2_setup_is_saved_used_and_its_secret_is_never_shown(client, make, su, fake_r2):
    owner = await make.staff("owner")
    r = await client.put("/admin/v1/storage", json=R2, headers=owner["headers"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["backend"] == "r2" and out["bucket"] == "ivision-evidence" and out["has_secret"] is True and out["source"] == "console"
    assert "SUPER-SECRET-VALUE" not in r.text and "secret_access_key" not in out
    assert any(k.startswith("healthcheck/") for k in fake_r2.puts)                                  # a test file was written first
    raw = await su.fetchval("SELECT secret_ciphertext FROM platform.storage_settings")
    assert b"SUPER-SECRET-VALUE" not in bytes(raw)                                                  # encrypted at rest
    log = await su.fetch("SELECT after::text AS a, reason FROM platform.audit_log WHERE action='storage.update' ORDER BY id DESC LIMIT 1")
    assert log and "SUPER-SECRET-VALUE" not in log[0]["a"] and "AKIAEXAMPLEKEY01" not in log[0]["a"] and log[0]["reason"] == "Move evidence to R2"
    assert isinstance(evidence.store(), FakeR2) and evidence.store().args[3] == "ivision-evidence"      # the running API switched at once
    assert (await client.get("/admin/v1/storage", headers=owner["headers"])).json()["has_secret"] is True


async def test_another_copy_of_the_api_picks_the_change_up_from_the_database(client, make, fake_r2):
    owner = await make.staff("owner")
    await client.put("/admin/v1/storage", json=R2, headers=owner["headers"])
    evidence.configure(None)
    evidence.set_store(None)                                                                         # as a copy that has not heard yet
    assert not isinstance(evidence.store(), FakeR2)
    assert await evidence.reload_from_console() is True
    assert isinstance(evidence.store(), FakeR2)
    assert await evidence.reload_from_console() is False                                             # nothing new the second time


async def test_a_setup_that_does_not_work_is_refused_and_nothing_is_saved(client, make, su, fake_r2):
    owner = await make.staff("owner")
    fake_r2.fail = "Access denied for {key} with {secret}"
    r = await client.put("/admin/v1/storage", json=R2, headers=owner["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "storage_check_failed"
    assert "SUPER-SECRET-VALUE" not in r.text and "AKIAEXAMPLEKEY01" not in r.text                  # the reason is shown with the keys hidden
    assert await su.fetchval("SELECT count(*) FROM platform.storage_settings") == 0
    assert not isinstance(evidence.store(), FakeR2)


@pytest.mark.parametrize("change,code", [({"bucket": "Bad Bucket!"}, "invalid_bucket"), ({"account_id": "xyz"}, "invalid_account"), ({"access_key_id": "x"}, "invalid_key"),
                                         ({"secret_access_key": None}, "secret_missing"), ({"account_id": None, "endpoint_url": "http://r2.example.com"}, "invalid_endpoint")])
async def test_r2_details_are_checked_before_anything_is_tried(client, make, fake_r2, change, code):
    owner = await make.staff("owner")
    body = {**R2, **change}
    body = {k: v for k, v in body.items() if v is not None}
    r = await client.put("/admin/v1/storage", json=body, headers=owner["headers"])
    assert r.status_code == 422 and r.json()["error"]["code"] == code, r.text
    assert fake_r2.puts == []


async def test_the_test_button_uses_what_is_typed_and_the_saved_secret_when_it_is_left_blank(client, make, fake_r2):
    owner = await make.staff("owner")
    assert (await client.put("/admin/v1/storage", json=R2, headers=owner["headers"])).status_code == 200
    typed = {k: v for k, v in R2.items() if k not in ("reason", "secret_access_key")}
    ok = await client.post("/admin/v1/storage/test", json={**typed, "bucket": "another-bucket"}, headers=owner["headers"])      # secret left out: the saved one is used
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert (await client.get("/admin/v1/storage", headers=owner["headers"])).json()["bucket"] == "ivision-evidence"                # testing saved nothing


async def test_switching_back_to_this_servers_disk_works(client, make, su, fake_r2, tmp_path):
    owner = await make.staff("owner")
    await client.put("/admin/v1/storage", json=R2, headers=owner["headers"])
    r = await client.put("/admin/v1/storage", json={"backend": "local", "reason": "Back to disk for now"}, headers=owner["headers"])
    assert r.status_code == 200 and r.json()["backend"] == "local"
    assert isinstance(evidence.store(), evidence.LocalStore)
    assert await su.fetchval("SELECT secret_ciphertext FROM platform.storage_settings") is None          # the old secret is gone
