"""Cameras made from an RTSP address: creation, safety of the address, and that everything downstream treats them like any other camera."""
import json
import stat
import uuid

import pytest

from app.config import get_settings
from tests.helpers import setup_cameras

GOOD = "rtsp://viewer:S3cretPw@10.20.0.14:554/cam/realmonitor?channel=1&subtype=1"
FAKE = r'''#!/usr/bin/env python3
import sys, time, json
a = sys.argv[1:]
if "-h" in a:
    print("  -timeout <int> set socket TCP I/O timeout in microseconds"); sys.exit(0)
url = a[-1]
if "hang" in url: time.sleep(60)
if "wrongpass" in url:
    sys.stderr.write("[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized for " + url + "\n"); sys.exit(1)
if "closed" in url:
    sys.stderr.write("[tcp @ 0x2] Connection to tcp://10.9.9.9:554 failed: Connection refused (" + url + ")\n"); sys.exit(1)
if "novideo" in url:
    print(json.dumps({"streams": []})); sys.exit(0)
print(json.dumps({"streams": [{"codec_name": "h264", "width": 1920, "height": 1080}]}))
'''


@pytest.fixture(autouse=True)
def fake_ffprobe(tmp_path, monkeypatch):
    f = tmp_path / "ffprobe"
    f.write_text(FAKE)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(get_settings(), "ffprobe_path", str(f))
    monkeypatch.setattr(get_settings(), "rtsp_probe_seconds", 3.0)


def body(**k):
    return {"name": "Main gate", "url": GOOD, "use_footfall": True, "reason": "Customer has a static IP", **k}


async def tech(make):
    return (await make.staff("technician"))["headers"]


async def tenant(make, **kw):
    return await make.tenant(**kw)


async def test_a_technician_creates_a_camera_from_an_address_and_the_rest_of_the_system_sees_it(client, make, su):
    t = await tenant(make)
    r = await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(), headers=await tech(make))
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["connection"] == "rtsp" and cam["source_host"] == "10.20.0.14:554" and cam["has_source"] and "S3cretPw" not in r.text and "viewer" not in r.text
    row = await su.fetchrow("SELECT recorder_id, channel_no, connection, source_ciphertext, source_host FROM app.cameras WHERE id=$1", uuid.UUID(cam["id"]))
    assert row["recorder_id"] is None and row["channel_no"] is None and row["connection"] == "rtsp" and b"S3cretPw" not in bytes(row["source_ciphertext"])   # encrypted at rest
    s = make.service()
    src = (await client.get(f"/internal/v1/cameras/{cam['id']}/source", headers=s)).json()
    assert src == {"kind": "rtsp", "url": GOOD}                                               # the worker receives it decrypted
    assert cam["id"] in (await client.get("/internal/v1/workers/cameras", headers=s)).json()["cameras"]
    conf = (await client.get(f"/internal/v1/cameras/{cam['id']}/config", headers=s)).json()
    assert conf["active"] and conf["use_footfall"] and conf["frame_rate"]["mode"] == "adaptive"
    u = await make.user(t)
    mine = (await client.get("/v1/cameras", headers=u["headers"])).json()
    assert [(c["name"], c["connection"], c["recorder_id"], c["channel_no"]) for c in mine] == [("Main gate", "rtsp", None, None)]
    audit = await su.fetch("SELECT after::text AS a, reason FROM platform.audit_log WHERE action='camera.rtsp.create' AND tenant_id=$1", t)
    assert len(audit) == 1 and "S3cretPw" not in audit[0]["a"] and "viewer" not in audit[0]["a"] and "10.20.0.14:554" in audit[0]["a"] and audit[0]["reason"]


async def test_such_a_camera_works_with_everything_a_normal_camera_does(client, make, su):
    t = await tenant(make)
    h = await tech(make)
    cam = (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(), headers=h)).json()["id"]
    u = await make.user(t)
    line = {"zones": [{"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}]}
    assert (await client.put(f"/v1/cameras/{cam}/zones", json=line, headers=u["headers"])).status_code == 200
    assert (await client.patch(f"/v1/cameras/{cam}", json={"name": "Gate 1"}, headers=u["headers"])).json()["name"] == "Gate 1"
    assert (await client.patch(f"/v1/cameras/{cam}", json={"enabled": False}, headers=u["headers"])).status_code == 200
    assert cam not in (await client.get("/internal/v1/workers/cameras", headers=make.service())).json()["cameras"]            # switched off: not run
    owner = await make.staff("owner")
    assert (await client.put(f"/admin/v1/cameras/{cam}/frame-rate", json={"mode": "fixed", "fixed_fps": 8, "reason": "Fast lane"}, headers=owner["headers"])).status_code == 200
    listed = (await client.get(f"/admin/v1/tenants/{t}/cameras", headers=owner["headers"])).json()["cameras"]
    assert listed[0]["has_source"] and listed[0]["source_host"] == "10.20.0.14:554" and listed[0]["effective"]["mode"] == "fixed"
    # a plate-reading camera and a second one with the same name are fine; no clash on the missing recorder
    for i in range(2):
        assert (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(name="Dock", use_anpr=True), headers=h)).status_code in (201, 403, 409)
    assert (await client.delete(f"/v1/cameras/{cam}", headers=u["headers"])).status_code == 204


async def test_the_address_is_checked_before_anything_is_saved(client, make, su):
    t = await tenant(make)
    h = await tech(make)
    url = f"/admin/v1/tenants/{t}/rtsp-cameras"
    for bad, code in [("http://10.20.0.14/x", "invalid_source"), ("rtsp:///x", "invalid_source"), ("rtsp://10.0.0.1/a b", "invalid_source"),
                      ("rtsp://u:p@127.0.0.1:554/x", "address_not_allowed"), ("rtsp://169.254.169.254/latest", "address_not_allowed"), ("rtsp://localhost/x", "address_not_allowed"),
                      ("rtsp://[::1]/x", "address_not_allowed"), ("rtsp://0.0.0.0/x", "address_not_allowed")]:
        r = await client.post(url, json=body(url=bad), headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (bad, r.text)
    ok = await client.post(url, json=body(url="rtsp://192.168.1.50:554/x"), headers=h)
    assert ok.status_code == 201                                                                # a private network address is exactly what customer sites use
    assert (await client.post(url, json={**body(), "use_footfall": False}, headers=h)).status_code == 422             # something to analyse is required
    assert (await client.post(url, json=body(reason="no"), headers=h)).status_code == 422
    assert (await client.post(url, json={**body(), "extra": 1}, headers=h)).status_code == 422
    assert await su.fetchval("SELECT count(*) FROM app.cameras WHERE tenant_id=$1", t) == 1


async def test_a_camera_that_does_not_answer_is_refused_with_a_plain_reason_and_nothing_leaks(client, make, su):
    t = await tenant(make)
    h = await tech(make)
    for marker, words in (("wrongpass", "user name or password"), ("closed", "Nothing is listening"), ("novideo", "no video"), ("hang", "did not answer in time")):
        r = await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(url=f"rtsp://viewer:S3cretPw@10.20.0.14:554/{marker}"), headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "address_unreachable" and words in r.json()["error"]["message"], (marker, r.text)
        assert "S3cretPw" not in r.text and "viewer" not in r.text and "10.20.0.14" not in r.text
    assert await su.fetchval("SELECT count(*) FROM app.cameras WHERE tenant_id=$1", t) == 0
    skipped = await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(url="rtsp://viewer:S3cretPw@10.20.0.14:554/wrongpass", check_first=False), headers=h)
    assert skipped.status_code == 201                                                             # the technician may save without the check (the site may be offline for now)


async def test_the_check_endpoint_reports_what_it_found(client, make):
    h = await tech(make)
    ok = (await client.post("/admin/v1/rtsp-check", json={"url": GOOD}, headers=h)).json()
    assert ok == {"host": "10.20.0.14:554", "ok": True, "codec": "h264", "width": 1920, "height": 1080}
    bad = await client.post("/admin/v1/rtsp-check", json={"url": "rtsp://viewer:S3cretPw@10.20.0.14/wrongpass"}, headers=h)
    assert bad.status_code == 200 and bad.json()["ok"] is False and "S3cretPw" not in bad.text and "viewer" not in bad.text
    assert (await client.post("/admin/v1/rtsp-check", json={"url": "rtsp://127.0.0.1/x"}, headers=h)).status_code == 422


async def test_limits_and_plan_features_apply_like_for_any_camera(client, make, su):
    h = await tech(make)
    full = await tenant(make, plan="full", limit=2)
    for _ in range(2):
        assert (await client.post(f"/admin/v1/tenants/{full}/rtsp-cameras", json=body(), headers=h)).status_code == 201
    assert (await client.post(f"/admin/v1/tenants/{full}/rtsp-cameras", json=body(), headers=h)).json()["error"]["code"] == "camera_limit_reached"
    quick = await tenant(make, plan="quick")
    r = await client.post(f"/admin/v1/tenants/{quick}/rtsp-cameras", json=body(use_footfall=False, use_anpr=True), headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "plan_feature_missing"
    gone = await tenant(make, status="suspended")
    assert (await client.post(f"/admin/v1/tenants/{gone}/rtsp-cameras", json=body(), headers=h)).json()["error"]["code"] == "tenant_not_active"
    assert (await client.post(f"/admin/v1/tenants/{uuid.uuid4()}/rtsp-cameras", json=body(), headers=h)).status_code == 404


async def test_only_owner_and_technician_can_do_this_and_never_a_customer(client, make):
    t = await tenant(make)
    for role in ("support", "finance", "analyst"):
        hh = (await make.staff(role))["headers"]
        assert (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(), headers=hh)).status_code == 403, role
        assert (await client.post("/admin/v1/rtsp-check", json={"url": GOOD}, headers=hh)).status_code == 403, role
    u = await make.user(t)
    assert (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(), headers=u["headers"])).status_code == 401
    assert (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body())).status_code == 401
    assert (await client.post(f"/admin/v1/tenants/{t}/rtsp-cameras", json=body(), headers=(await make.staff("owner"))["headers"])).status_code == 201


async def test_the_database_keeps_the_two_kinds_of_camera_apart(client, make, su):
    t, u, _, (cam,) = await setup_cameras(client, make)
    rec = await su.fetchval("SELECT recorder_id FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    for sql in ("INSERT INTO app.cameras (tenant_id, connection, name, use_footfall) VALUES ($1,'recorder','x',true)",                       # a recorder camera needs its recorder and channel
                "INSERT INTO app.cameras (tenant_id, connection, recorder_id, channel_no, name, use_footfall) VALUES ($1,'rtsp',(SELECT id FROM app.recorders WHERE tenant_id=$1 LIMIT 1),9,'x',true)",   # an address camera has neither
                "INSERT INTO app.cameras (tenant_id, connection, name, use_footfall) VALUES ($1,'bogus','x',true)"):
        with pytest.raises(Exception):
            await su.execute(sql, t)
    assert await su.fetchval("SELECT connection FROM app.cameras WHERE id=$1", uuid.UUID(cam)) == "recorder" and rec
    for _ in range(3):                                                                           # many address cameras: the missing recorder never counts as a duplicate
        await su.execute("INSERT INTO app.cameras (tenant_id, connection, name, use_footfall) VALUES ($1,'rtsp','a',true)", t)


async def test_recorder_cameras_still_work_exactly_as_before(client, make, su):
    t, u, (foot,), (plate,) = await setup_cameras(client, make)
    s = make.service()
    cams = (await client.get("/v1/cameras", headers=u["headers"])).json()
    assert {c["connection"] for c in cams} == {"recorder"} and all(c["recorder_id"] and c["channel_no"] for c in cams)
    await su.execute("UPDATE app.recorders SET protocol='gb28181' WHERE tenant_id=$1", t)
    assert {foot, plate} <= set((await client.get("/internal/v1/workers/cameras", headers=s)).json()["cameras"])
    assert (await client.get(f"/internal/v1/cameras/{foot}/source", headers=s)).json()["kind"] == "gb28181"
