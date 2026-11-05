"""The backend side of Dahua Auto Register: what the customer is told to type, and where the worker gets the video from."""
import asyncio
import uuid

import httpx

from tests.helpers import setup_cameras
from worker.backend import WorkerBackend
from worker.relay import RelayClient, Relays, RelaySource, make_relay_reader


async def dahua_camera(client, make, stream="sub"):
    t = await make.tenant()
    u = await make.user(t)
    cred = (await client.post("/v1/recorders", json={"brand": "dahua"}, headers=u["headers"])).json()
    await client.post(f"/internal/v1/recorders/{cred['device_id']}/heartbeat", headers=make.service(),
                      json={"ip": "1.2.3.4", "channels": [{"channel_no": 1, "name": "C1", "external_id": "0"}]})
    r = await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 1, "name": "Gate", "use_anpr": True}, headers=u["headers"])
    assert r.status_code == 201, r.text
    cam = r.json()["id"]
    if stream != "sub":
        assert (await client.patch(f"/v1/cameras/{cam}", json={"stream": stream}, headers=u["headers"])).status_code == 200
    return cred, cam, u


async def test_the_customer_is_told_what_to_type_on_a_dahua_recorder(client, make):
    cred, _, _ = await dahua_camera(client, make)
    assert cred["protocol"] == "auto_register" and cred["port"] == 7000
    assert cred["viewer_user"] == "giview" and cred["device_id"] and cred["key"] and cred["server"]


async def test_hikvision_and_gb28181_recorders_do_not_get_a_viewer_user(client, make):
    u = await make.user(await make.tenant())
    assert (await client.post("/v1/recorders", json={"brand": "hikvision"}, headers=u["headers"])).json()["viewer_user"] is None


async def test_a_dahua_camera_is_listed_for_workers_and_its_source_names_the_gateway(client, make):
    cred, cam, _ = await dahua_camera(client, make)
    s = make.service()
    assert cam in (await client.get("/internal/v1/workers/cameras", headers=s)).json()["cameras"]
    src = (await client.get(f"/internal/v1/cameras/{cam}/source", headers=s)).json()
    assert src == {"kind": "auto_register", "device_id": cred["device_id"], "channel_no": 1, "stream": "sub"}


async def test_a_suspended_customer_gets_no_dahua_source(client, make, su):
    cred, cam, u = await dahua_camera(client, make)
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=(SELECT tenant_id FROM app.cameras WHERE id=$1)", uuid.UUID(cam))
    assert (await client.get(f"/internal/v1/cameras/{cam}/source", headers=make.service())).status_code == 403
    assert cam not in (await client.get("/internal/v1/workers/cameras", headers=make.service())).json()["cameras"]


async def test_the_worker_turns_the_source_into_a_dahua_relay_source(client, make):
    cred, cam, _ = await dahua_camera(client, make)

    class Backend(WorkerBackend):
        async def _call(self, method, path, **kw):                       # go through the test client instead of the network
            return await client.request(method, f"/internal/v1/cameras/{cam}/{path}", headers=make.service(), **kw)
    assert await Backend(client, "x", cam).source() == RelaySource(cred["device_id"], 1, "auto_register", "sub")


def test_dahua_video_is_read_as_dhav_from_the_dahua_gateway_and_gb28181_as_mpeg():
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((str(request.url.host), request.url.port, request.read()))
        return httpx.Response(201, json={"url": "http://gw/relay/v1/play/abc", "expires_in": 60})
    mk = lambda port: RelayClient(f"http://h:{port}", "t" * 40, http=httpx.Client(base_url=f"http://h:{port}", transport=httpx.MockTransport(handler)))
    relays = Relays(mk(8081), mk(8082))

    def command(source):
        reader = make_relay_reader(source, relays, 320, 180, 5)
        return reader._cmd()
    cmd = command(RelaySource("D-1", 2, "auto_register", "main"))
    assert cmd[cmd.index("-f") + 1] == "dhav" and asked[-1][1] == 8082 and b'"stream":"main"' in asked[-1][2].replace(b" ", b"")
    cmd = command(RelaySource("G-1", 1))
    assert cmd[cmd.index("-f") + 1] == "mpeg" and asked[-1][1] == 8081 and b"stream" not in asked[-1][2]
