import json

import pytest

from app.services.framerate import resolve
from app.services.sweeper import mark_stale_cameras
from tests.helpers import setup_cameras

DEFAULT_TENANT = {"mode": "adaptive", "idle": 2, "active": 5, "fixed": 5}


async def cfg(client, make, cam):
    return (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()["frame_rate"]


# ------------------------------------------------------------------ resolution
def test_a_camera_value_wins_and_the_rest_comes_from_the_tenant():
    r = resolve(DEFAULT_TENANT, {"mode": None, "idle": None, "active": 15, "fixed": None})
    assert (r["mode"], r["idle_fps"], r["active_fps"], r["capture_fps"]) == ("adaptive", 2, 15, 15)
    assert r["from"] == {"mode": "tenant", "idle": "tenant", "active": "camera", "fixed": "tenant"}


def test_fixed_mode_runs_at_the_fixed_rate_and_idle_never_exceeds_active():
    assert resolve(DEFAULT_TENANT, {"mode": "fixed", "idle": None, "active": None, "fixed": 12, })["capture_fps"] == 12
    assert resolve({**DEFAULT_TENANT, "idle": 4}, {"mode": None, "idle": None, "active": 3, "fixed": None})["idle_fps"] == 3


# ---------------------------------------------------------------------- tenant
async def test_new_tenants_and_cameras_default_to_adaptive(client, make):
    t, u, (foot,), _ = await setup_cameras(client, make)
    assert await cfg(client, make, foot) == {"mode": "adaptive", "idle_fps": 2, "active_fps": 5, "fixed_fps": 5, "capture_fps": 5,
                                             "from": {"mode": "tenant", "idle": "tenant", "active": "tenant", "fixed": "tenant"}}


async def test_tenant_policy_can_be_raised_and_reaches_every_camera(client, make):
    owner = await make.staff("owner")
    t, u, (a, b), _ = await setup_cameras(client, make, footfall=2)
    r = await client.put(f"/admin/v1/tenants/{t}/frame-rate", json={"active_fps": 15, "reason": "Fast lanes at this plant"}, headers=owner["headers"])
    assert r.json() == {"mode": "adaptive", "idle_fps": 2, "active_fps": 15, "fixed_fps": 5}
    assert (await cfg(client, make, a))["capture_fps"] == (await cfg(client, make, b))["capture_fps"] == 15
    await client.put(f"/admin/v1/tenants/{t}/frame-rate", json={"mode": "fixed", "fixed_fps": 10, "reason": "Customer wants steady 10"}, headers=owner["headers"])
    got = await cfg(client, make, a)
    assert got["mode"] == "fixed" and got["capture_fps"] == 10


async def test_who_may_change_the_tenant_policy(client, make):
    t = await make.tenant()
    body = {"active_fps": 10, "reason": "Needed for the new gate"}
    for role, code in (("owner", 200), ("support", 403)):
        s = await make.staff(role)
        body = {"active_fps": 10 if code == 200 and role == "owner" else 12, "reason": "Needed for the new gate"}
        assert (await client.put(f"/admin/v1/tenants/{t}/frame-rate", json=body, headers=s["headers"])).status_code == code, role
    assert (await client.get(f"/admin/v1/tenants/{t}/frame-rate", headers=(await make.staff("support"))["headers"])).status_code == 200      # everyone may read


@pytest.mark.parametrize("body,status,code", [
    ({"reason": "nothing to set"}, 422, "validation_error"),
    ({"active_fps": 10}, 422, "validation_error"),                                  # a reason is required
    ({"active_fps": 10, "reason": "no"}, 422, "validation_error"),
    ({"active_fps": 30, "reason": "Too fast to be sensible"}, 422, "validation_error"),
    ({"idle_fps": 0, "reason": "Zero is not a rate"}, 422, "validation_error"),
    ({"idle_fps": 8, "active_fps": 5, "reason": "Idle faster than active"}, 422, "idle_above_active"),
    ({"mode": "turbo", "reason": "Unknown mode here"}, 422, "validation_error"),
    ({"active_fps": 5, "reason": "Already the default"}, 409, "no_change"),
])
async def test_policy_validation(client, make, body, status, code):
    t = await make.tenant()
    r = await client.put(f"/admin/v1/tenants/{t}/frame-rate", json=body, headers=(await make.staff("owner"))["headers"])
    assert r.status_code == status and r.json()["error"]["code"] == code


async def test_policy_changes_are_audited_with_before_and_after(client, make, su):
    t = await make.tenant()
    owner = await make.staff("owner")
    await client.put(f"/admin/v1/tenants/{t}/frame-rate", json={"active_fps": 12, "reason": "Fast lanes at this plant"}, headers=owner["headers"])
    row = await su.fetchrow("SELECT reason, before, after FROM platform.audit_log WHERE tenant_id=$1 AND action='framerate.tenant.update'", t)
    assert row["reason"] == "Fast lanes at this plant"
    assert json.loads(row["before"])["active_fps"] == 5 and json.loads(row["after"])["active_fps"] == 12


# ---------------------------------------------------------------------- camera
async def test_camera_override_beats_the_tenant_and_reset_returns_to_it(client, make):
    owner, tech = await make.staff("owner"), await make.staff("owner")
    t, u, (a, b), _ = await setup_cameras(client, make, footfall=2)
    r = await client.put(f"/admin/v1/cameras/{a}/frame-rate", json={"active_fps": 15, "reason": "Fast gate lane"}, headers=tech["headers"])
    assert r.json()["override"] == {"mode": None, "idle_fps": None, "active_fps": 15, "fixed_fps": None} and r.json()["effective"]["capture_fps"] == 15
    assert (await cfg(client, make, a))["capture_fps"] == 15 and (await cfg(client, make, b))["capture_fps"] == 5      # only that camera
    await client.put(f"/admin/v1/cameras/{a}/frame-rate", json={"idle_fps": 3, "reason": "Quieter idle rate"}, headers=tech["headers"])
    assert (await cfg(client, make, a))["active_fps"] == 15 and (await cfg(client, make, a))["idle_fps"] == 3     # overrides accumulate
    # The tenant changes: the camera keeps its own parts and follows the tenant for the rest.
    await client.put(f"/admin/v1/tenants/{t}/frame-rate", json={"mode": "fixed", "fixed_fps": 8, "reason": "Customer asked for steady 8"}, headers=owner["headers"])
    got = await cfg(client, make, a)
    assert got["mode"] == "fixed" and got["capture_fps"] == 8 and got["from"]["active"] == "camera" and got["from"]["mode"] == "tenant"
    reset = await client.delete(f"/admin/v1/cameras/{a}/frame-rate", params={"reason": "Back to the tenant default"}, headers=tech["headers"])
    assert reset.json()["override"] == {"mode": None, "idle_fps": None, "active_fps": None, "fixed_fps": None}
    assert (await cfg(client, make, a))["from"] == {"mode": "tenant", "idle": "tenant", "active": "tenant", "fixed": "tenant"}
    assert (await client.delete(f"/admin/v1/cameras/{a}/frame-rate", params={"reason": "Nothing left to remove"}, headers=tech["headers"])).status_code == 409


async def test_camera_override_roles_and_validation(client, make):
    t, u, (a,), _ = await setup_cameras(client, make)
    body = {"active_fps": 10, "reason": "Fast gate lane"}
    for role, code in (("owner", 200), ("support", 403)):
        s = await make.staff(role)
        body = {"active_fps": 10, "reason": "Fast gate lane"}
        assert (await client.put(f"/admin/v1/cameras/{a}/frame-rate", json=body, headers=s["headers"])).status_code == code, role
    s = await make.staff("owner")
    bad = await client.put(f"/admin/v1/cameras/{a}/frame-rate", json={"idle_fps": 10, "active_fps": 2, "reason": "Idle faster than active"}, headers=s["headers"])
    assert bad.json()["error"]["code"] == "idle_above_active"
    low = await client.put(f"/admin/v1/cameras/{a}/frame-rate", json={"active_fps": 1, "reason": "Active below tenant idle"}, headers=s["headers"])
    assert low.json()["error"]["code"] == "idle_above_active"                      # 1 would sit below the inherited idle rate of 2
    assert (await client.put("/admin/v1/cameras/00000000-0000-0000-0000-000000000000/frame-rate", json=body, headers=s["headers"])).status_code == 404


async def test_the_admin_camera_list_shows_overrides_effective_rates_and_health(client, make):
    owner = await make.staff("owner")
    t, u, (a, b), _ = await setup_cameras(client, make, footfall=2)
    await client.put(f"/admin/v1/cameras/{a}/frame-rate", json={"mode": "fixed", "fixed_fps": 10, "reason": "Steady rate for counting"}, headers=owner["headers"])
    await client.post(f"/internal/v1/cameras/{a}/frame-status", json={"state": "streaming", "analysis_fps": 9.8, "dropped": 112}, headers=make.service())
    out = (await client.get(f"/admin/v1/tenants/{t}/cameras", headers=owner["headers"])).json()
    assert out["tenant_policy"] == {"mode": "adaptive", "idle_fps": 2, "active_fps": 5, "fixed_fps": 5}
    cams = {c["id"]: c for c in out["cameras"]}
    assert cams[a]["override"]["mode"] == "fixed" and cams[a]["effective"]["capture_fps"] == 10 and cams[a]["stream_state"] == "streaming" and cams[a]["analysis_fps"] == pytest.approx(9.8) and cams[a]["frames_dropped"] == 112      # falling behind is visible to the administrator
    assert cams[b]["override"] == {"mode": None, "idle_fps": None, "active_fps": None, "fixed_fps": None} and cams[b]["stream_state"] == "unknown"
    assert all("source_ciphertext" not in c and "url" not in c for c in cams.values())


# ---------------------------------------------------------------------- source
URL = "rtsp://viewer:S3cretPass@10.20.0.14:554/Streaming/Channels/102"


async def test_a_stream_address_is_stored_encrypted_and_reaches_only_the_worker(client, make, su):
    import uuid
    tech = await make.staff("owner")
    t, u, (cam,), _ = await setup_cameras(client, make)
    r = await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": URL, "reason": "Site VPN is up"}, headers=tech["headers"])
    assert r.json() == {"source_host": "10.20.0.14:554", "has_source": True} and "S3cretPass" not in r.text
    raw = bytes(await su.fetchval("SELECT source_ciphertext FROM app.cameras WHERE id=$1", uuid.UUID(cam)))
    assert b"S3cretPass" not in raw and b"10.20.0.14" not in raw
    got = await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())
    assert got.json() == {"kind": "rtsp", "url": URL} and got.headers["cache-control"] == "no-store"
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=u["headers"])).status_code == 401
    audit = await su.fetch("SELECT * FROM platform.audit_log WHERE tenant_id=$1 AND action='camera.source.set'", t)
    assert audit and all("S3cretPass" not in str(dict(a)) for a in audit)                       # never in the audit log
    mine = (await client.get("/v1/cameras", headers=u["headers"])).text
    assert "S3cretPass" not in mine and "10.20.0.14" not in mine                               # customers never see it either


async def test_stream_address_validation_and_roles(client, make):
    t, u, (cam,), _ = await setup_cameras(client, make)
    tech = await make.staff("owner")
    put = lambda url, who=tech: client.put(f"/admin/v1/cameras/{cam}/source", json={"url": url, "reason": "Site VPN is up"}, headers=who["headers"])
    for bad in ("http://10.0.0.1/stream", "rtsp://", "rtsp://host/with space", "ftp://x/y", "rtsp://10.0.0.1:99999/x", "rtsp://h/" + "a" * 600):
        assert (await put(bad)).status_code == 422, bad
    assert (await put("rtsps://cam.example.in:322/live")).json()["source_host"] == "cam.example.in:322"
    assert (await put("rtsp://10.20.0.14/x", await make.staff("support"))).status_code == 403


async def test_source_is_withheld_when_missing_inactive_or_tampered(client, make, su):
    import uuid
    tech = await make.staff("owner")
    t, u, (cam,), _ = await setup_cameras(client, make, su=su, no_route=True)                  # an RTSP-only recorder: no video route
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())).json()["error"]["code"] == "no_source"
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": URL, "reason": "Site VPN is up"}, headers=tech["headers"])
    other = (await setup_cameras(client, make))[2][0]
    await client.put(f"/admin/v1/cameras/{other}/source", json={"url": "rtsp://x:y@10.9.9.9/z", "reason": "Site VPN is up"}, headers=tech["headers"])
    blob = await su.fetchval("SELECT source_ciphertext FROM app.cameras WHERE id=$1", uuid.UUID(other))
    await su.execute("UPDATE app.cameras SET source_ciphertext=$2 WHERE id=$1", uuid.UUID(cam), blob)       # a copy from another camera
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())).json()["error"]["code"] == "source_unreadable"
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": URL, "reason": "Set it again after the fault"}, headers=tech["headers"])
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())).status_code == 403
    await su.execute("UPDATE app.tenants SET status='active' WHERE id=$1", t)
    assert (await client.delete(f"/admin/v1/cameras/{cam}/source", params={"reason": "Camera was removed"}, headers=tech["headers"])).status_code == 204
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())).status_code == 404


# ---------------------------------------------------------------------- health
async def test_heartbeat_records_state_and_tells_the_worker_whether_to_continue(client, make, su):
    import uuid
    t, u, (cam,), _ = await setup_cameras(client, make)
    hb = lambda body: client.post(f"/internal/v1/cameras/{cam}/frame-status", json=body, headers=make.service())
    assert (await hb({"state": "streaming", "analysis_fps": 3.4})).json() == {"active": True}
    first = await su.fetchrow("SELECT stream_state, analysis_fps, stream_state_at FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    assert first["stream_state"] == "streaming" and first["analysis_fps"] == pytest.approx(3.4, abs=0.01)
    await hb({"state": "streaming", "analysis_fps": 5.0})
    assert (await su.fetchval("SELECT stream_state_at FROM app.cameras WHERE id=$1", uuid.UUID(cam))) == first["stream_state_at"]       # same state: time unchanged
    await hb({"state": "reconnecting", "error": "connection refused"})
    assert await su.fetchval("SELECT stream_state FROM app.cameras WHERE id=$1", uuid.UUID(cam)) == "reconnecting"
    assert (await client.get("/v1/cameras", headers=u["headers"])).json()[0]["stream_state"] == "reconnecting"
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    assert (await hb({"state": "streaming"})).json() == {"active": False}                                                        # worker is told to stop


async def test_passwords_are_scrubbed_from_reported_errors(client, make, su):
    import uuid
    t, u, (cam,), _ = await setup_cameras(client, make)
    await client.post(f"/internal/v1/cameras/{cam}/frame-status", json={"state": "offline", "error": "Failed to open rtsp://admin:Hunter2@10.0.0.9:554/x: refused"}, headers=make.service())
    err = await su.fetchval("SELECT stream_error FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    assert "Hunter2" not in err and "rtsp://***@10.0.0.9:554/x" in err


async def test_heartbeat_validation_and_auth(client, make):
    t, u, (cam,), _ = await setup_cameras(client, make)
    url = f"/internal/v1/cameras/{cam}/frame-status"
    assert (await client.post(url, json={"state": "streaming"}, headers=u["headers"])).status_code == 401
    assert (await client.post(url, json={"state": "flying"}, headers=make.service())).status_code == 422
    assert (await client.post(url, json={"state": "streaming", "analysis_fps": 999}, headers=make.service())).status_code == 422
    assert (await client.post(url, json={"state": "streaming", "dropped": -1}, headers=make.service())).status_code == 422
    assert (await client.post("/internal/v1/cameras/00000000-0000-0000-0000-000000000000/frame-status", json={"state": "streaming"}, headers=make.service())).status_code == 404


async def test_a_silent_camera_is_marked_offline_and_comes_back_with_the_next_report(client, make, su):
    import uuid
    t, u, (cam,), _ = await setup_cameras(client, make)
    await client.post(f"/internal/v1/cameras/{cam}/frame-status", json={"state": "streaming", "analysis_fps": 4}, headers=make.service())
    assert await mark_stale_cameras(60) == 0                                        # just reported
    await su.execute("UPDATE app.cameras SET stream_seen_at = now() - interval '5 minutes' WHERE id=$1", uuid.UUID(cam))
    assert await mark_stale_cameras(60) >= 1
    row = await su.fetchrow("SELECT stream_state, analysis_fps FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    assert row["stream_state"] == "offline" and row["analysis_fps"] is None
    await client.post(f"/internal/v1/cameras/{cam}/frame-status", json={"state": "streaming"}, headers=make.service())
    assert await su.fetchval("SELECT stream_state FROM app.cameras WHERE id=$1", uuid.UUID(cam)) == "streaming"



async def test_a_camera_on_a_registered_recorder_is_served_by_the_relay_unless_an_address_is_set(client, make):
    tech = await make.staff("owner")
    t = await make.tenant()
    u = await make.user(t)
    cred = (await client.post(f"/admin/v1/tenants/{t}/recorders", json={"brand": "dahua", "protocol": "gb28181", "reason": "Test: GB28181 recorder"}, headers=tech["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(),
                      json={"ip": "1.2.3.4", "channels": [{"channel_no": 1}, {"channel_no": 2}]})
    cam = (await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 2, "name": "Gate", "use_footfall": True}, headers=u["headers"])).json()["id"]
    src = lambda: client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())
    assert (await src()).json() == {"kind": "gb28181", "device_id": cred["device_id"], "channel_no": 2}          # no secret in it
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://viewer:pw@10.20.0.14/x", "reason": "Site VPN is up"}, headers=tech["headers"])
    assert (await src()).json() == {"kind": "rtsp", "url": "rtsp://viewer:pw@10.20.0.14/x"}                      # an administrator's address wins
    await client.delete(f"/admin/v1/cameras/{cam}/source", params={"reason": "Back to the relay"}, headers=tech["headers"])
    assert (await src()).json()["kind"] == "gb28181"


async def test_the_worker_link_turns_the_answer_into_a_relay_source_or_an_address(client, make):
    from worker.backend import WorkerBackend
    from worker.relay import RelaySource
    tech = await make.staff("owner")
    t = await make.tenant()
    u = await make.user(t)
    cred = (await client.post(f"/admin/v1/tenants/{t}/recorders", json={"brand": "dahua", "protocol": "gb28181", "reason": "Test: GB28181 recorder"}, headers=tech["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(), json={"ip": "1.2.3.4", "channels": [{"channel_no": 1}]})
    cam = (await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 1, "name": "Gate", "use_footfall": True}, headers=u["headers"])).json()["id"]
    link = WorkerBackend(client, make.service()["Authorization"].split()[1], cam)
    assert await link.source() == RelaySource(cred["device_id"], 1)
    await client.put(f"/admin/v1/cameras/{cam}/source", json={"url": "rtsp://v:p@10.0.0.1/x", "reason": "Site VPN is up"}, headers=tech["headers"])
    assert await link.source() == "rtsp://v:p@10.0.0.1/x"
