import uuid
from datetime import datetime, timedelta, timezone


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def minute(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def now() -> datetime:
    return datetime.now(timezone.utc)


async def setup_cameras(client, make, plan="full", footfall=1, anpr=1, tenant=None, limit=20, su=None, no_route=False):
    """A tenant with one recorder online and cameras: `footfall` footfall-only cameras, `anpr` plate cameras. Returns (tenant, user, foot_ids, anpr_ids).
    The recorder is a Dahua one (it has a video route). With `no_route=True` (needs `su`) it is an RTSP-only recorder instead: it has no video
    route, so its cameras need an address set by an administrator. The API cannot add that kind of recorder, so the row is inserted directly."""
    t = tenant or await make.tenant(plan=plan, limit=limit)
    u = await make.user(t)
    if no_route:
        device_id = f"RTSP-{uuid.uuid4().hex[:8].upper()}"
        rid = await su.fetchval(
            """INSERT INTO app.recorders (tenant_id, device_id, brand, protocol, key_ciphertext, key_kms_id, key_set_at)
               VALUES ($1,$2,'other'::app.recorder_brand,'rtsp'::app.recorder_protocol,$3,'local',now()) RETURNING id""", t, device_id, b"x")
        cred = {"recorder_id": str(rid), "device_id": device_id}
    else:
        cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    n = footfall + anpr
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(),
                      json={"ip": "1.2.3.4", "channels": [{"channel_no": i, "name": f"C{i}"} for i in range(1, n + 1)]})
    ids = []
    for i in range(1, n + 1):
        body = {"recorder_id": cred["recorder_id"], "channel_no": i, "name": f"Cam {i}"}
        body.update({"use_footfall": True} if i <= footfall else {"use_anpr": True})
        r = await client.post("/v1/cameras", json=body, headers=u["headers"])
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return t, u, ids[:footfall], ids[footfall:]
