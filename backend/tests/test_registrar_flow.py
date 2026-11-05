import asyncio
import os
import re
import uuid

import httpx
import pytest
import pytest_asyncio

from registrar.backend_client import BackendClient
from registrar.config import RegistrarSettings
from registrar.server import Registrar
from tests.fake_device import FakeDevice, SERVER_ID, channel_id


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest_asyncio.fixture
async def env(client, make, su):
    clock = Clock()
    cfg = RegistrarSettings(service_token=os.environ["GI_SERVICE_TOKEN"], public_ip="127.0.0.1", sip_bind="127.0.0.1", sip_port=0,
                            rtp_port_min=41000, rtp_port_max=41400, catalog_timeout=2.0, probe_timeout=3.0,
                            relay_http_port=0, relay_idle_grace=0.3, relay_stall_seconds=3.0)
    reg = Registrar(cfg, BackendClient("http://test", cfg.service_token, http=client), clock=clock)
    await reg.start()
    devices = []

    class Env:
        pass
    e = Env()
    e.reg, e.clock, e.cfg = reg, clock, cfg

    async def onboard(plan="full", protocol="gb28181", transport="udp", **dev_kw):
        t = await make.tenant(plan=plan)
        u = await make.user(t)
        r = await client.post("/v1/recorders", json={"brand": "hikvision", "protocol": protocol}, headers=u["headers"])
        assert r.status_code == 201, r.text
        cred = r.json()
        port = reg.udp_port if transport == "udp" else reg.tcp_port
        dev = FakeDevice(cred["device_id"], cred["key"], ("127.0.0.1", port), transport=transport, **dev_kw)
        await dev.start()
        devices.append(dev)
        return t, u, cred, dev
    e.onboard = onboard
    e.db = lambda cred: su.fetchrow("SELECT status::text AS status, channel_count, preview_ok_at, last_heartbeat_at, host(last_ip) AS ip FROM app.recorders WHERE id=$1", cred["recorder_id"])

    async def until(cond, timeout=6.0):
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            v = await cond()
            if v:
                return v
            await asyncio.sleep(0.05)
        raise AssertionError("condition not reached in time")
    e.until = until
    yield e
    for d in devices:
        await d.stop()
    await reg.stop()


@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_a_recorder_registers_end_to_end(env, client, su, transport):
    t, u, cred, dev = await env.onboard(transport=transport)
    assert re.fullmatch(r"\d{20}", cred["device_id"]) and cred["protocol"] == "gb28181" and cred["server_id"] == SERVER_ID and cred["domain"] == "3402000000"

    assert await dev.register() == 200
    row = await env.until(lambda: _full(env, cred))
    assert row["status"] == "online" and row["channel_count"] == 2 and row["ip"] == "127.0.0.1"

    chans = (await client.get(f"/v1/recorders/{cred['recorder_id']}/channels", headers=u["headers"])).json()
    assert [c["name"] for c in chans] == ["大门 Gate", "仓库 Godown"]            # Chinese GB2312 names survive intact (the group node is skipped)
    ext = await su.fetch("SELECT channel_no, external_id FROM app.recorder_channels WHERE recorder_id=$1 ORDER BY 1", cred["recorder_id"])
    assert [r["external_id"] for r in ext] == [channel_id(1), channel_id(2)]

    # The registrar asked for live video, got real RTP packets, and hung up politely.
    assert "INVITE" in dev.seen and "ACK" in dev.seen
    await env.until(lambda: _bye(dev))
    assert f"m=video" in dev.invite_sdp and "a=recvonly" in dev.invite_sdp and "y=0" in dev.invite_sdp
    st = (await client.get(f"/v1/recorders/{cred['recorder_id']}/status", headers=u["headers"])).json()
    assert st["checks"] == {"reached": True, "key_ok": True, "channels_found": True, "preview_ok": True}

    assert await dev.keepalive() == 200


async def _full(env, cred):
    r = await env.db(cred)
    return r if r["status"] == "online" and r["channel_count"] == 2 and r["preview_ok_at"] else None


async def _bye(dev):
    return "BYE" in dev.seen


async def test_wrong_key_and_unknown_device_are_refused_alike(env, client, su):
    t, u, cred, dev = await env.onboard()
    assert await dev.register(password="not-the-key") == 403
    assert await dev.register(device_id="34020000001180999999") == 403
    assert (await env.db(cred))["status"] == "never"
    assert dev.catalog_queries == 0


async def test_repeated_failures_get_the_sender_ignored(env):
    t, u, cred, dev = await env.onboard()
    for _ in range(10):
        assert await dev.register(password="wrong") == 403
    assert await dev.register(password="wrong") is None            # silence, not another answer
    assert await dev.register() is None                            # even the right key waits out the block (same address)


async def test_a_captured_login_cannot_be_replayed(env):
    t, u, cred, dev = await env.onboard()
    assert await dev.register() == 200
    again = await dev.register(replay=dev.last_auth_request)       # same bytes, same nonce
    assert again == 401


async def test_only_gb28181_recorders_can_register_here(env):
    t, u, cred, dev = await env.onboard(protocol="isup")
    assert await dev.register() == 403                              # right key, wrong protocol


async def test_suspended_tenant_cannot_register_and_is_dropped_when_suspended(env, su):
    t, u, cred, dev = await env.onboard()
    assert await dev.register() == 200
    await env.until(lambda: _full(env, cred))
    await su.execute("UPDATE app.tenants SET status='suspended' WHERE id=$1", t)
    env.clock.t += env.cfg.heartbeat_interval + 1
    await env.reg.reap_once()                                       # next heartbeat is refused by the backend
    await env.until(lambda: _gone(env, cred))
    other = FakeDevice(cred["device_id"], cred["key"], ("127.0.0.1", env.reg.udp_port))
    await other.start()
    try:
        assert await other.register() == 403
    finally:
        await other.stop()


async def _gone(env, cred):
    return cred["device_id"] not in env.reg.sessions


async def test_silent_recorder_goes_offline_and_unregister_is_honoured(env, su):
    t, u, cred, dev = await env.onboard()
    assert await dev.register() == 200
    await env.until(lambda: _full(env, cred))
    env.clock.t += env.cfg.keepalive_timeout + 1
    await env.reg.reap_once()
    assert (await env.db(cred))["status"] == "offline"

    assert await dev.register() == 200                              # comes back
    await env.until(lambda: _online(env, cred))
    assert await dev.register(expires=0) == 200                     # polite sign-off
    assert (await env.db(cred))["status"] == "offline"


async def _online(env, cred):
    return (await env.db(cred))["status"] == "online"


async def test_keepalives_keep_a_recorder_alive(env):
    t, u, cred, dev = await env.onboard()
    assert await dev.register() == 200
    await env.until(lambda: _full(env, cred))
    for _ in range(3):
        env.clock.t += env.cfg.keepalive_timeout * 0.6
        assert await dev.keepalive() == 200
        await env.reg.reap_once()
    assert cred["device_id"] in env.reg.sessions


async def test_messages_from_unregistered_devices_are_refused(env):
    t, u, cred, dev = await env.onboard()
    assert await dev.keepalive() == 403


async def test_catalogue_in_several_parts_is_assembled(env, su):
    chans = [(channel_id(i), f"Cam {i}", "ON") for i in range(1, 6)]
    t, u, cred, dev = await env.onboard(channels=chans, batches=3)
    assert await dev.register() == 200
    row = await env.until(lambda: _count(env, cred, 5))
    assert row["channel_count"] == 5


async def _count(env, cred, n):
    r = await env.db(cred)
    return r if r["channel_count"] == n else None


async def test_recorder_that_refuses_live_video_is_still_online(env):
    t, u, cred, dev = await env.onboard(refuse_invite=True)
    assert await dev.register() == 200
    await env.until(lambda: _count(env, cred, 2))
    await asyncio.sleep(0.5)
    row = await env.db(cred)
    assert row["status"] == "online" and row["preview_ok_at"] is None


async def test_recorder_that_accepts_but_sends_no_video_does_not_pass_the_check(env):
    t, u, cred, dev = await env.onboard(send_rtp=False)
    env.cfg.probe_timeout = 0.6
    assert await dev.register() == 200
    await env.until(lambda: _count(env, cred, 2))
    await env.until(lambda: _bye(dev))                              # it hung up after the wait
    assert (await env.db(cred))["preview_ok_at"] is None


async def test_garbage_does_not_disturb_the_server(env):
    t, u, cred, dev = await env.onboard()
    loop = asyncio.get_running_loop()
    tr, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=("127.0.0.1", env.reg.udp_port))
    for junk in (b"", b"\x00\xff" * 100, b"GET / HTTP/1.1\r\n\r\n", b"REGISTER", b"X" * 70000, b"MESSAGE sip:a SIP/2.0\r\nContent-Length: abc\r\n\r\n"):
        tr.sendto(junk)
    tr.close()
    await asyncio.sleep(0.2)
    assert await dev.register() == 200


async def test_registrar_reports_503_when_the_backend_is_down(env, client):
    t, u, cred, dev = await env.onboard()

    def boom(request):
        raise httpx.ConnectError("backend down")
    env.reg.backend = BackendClient("http://down", "x" * 40, http=httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://down"))
    assert await dev.register() == 503                              # recorders retry on 503; a 403 would make them give up


async def test_an_xml_bomb_in_a_message_is_answered_with_400(env):
    t, u, cred, dev = await env.onboard()
    assert await dev.register() == 200
    await env.until(lambda: _count(env, cred, 2))
    cid = uuid.uuid4().hex + "@fake"
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><Notify><CmdType>&a;</CmdType></Notify>'
    r = await dev.request(dev._frame(f"MESSAGE sip:{SERVER_ID}@3402000000 SIP/2.0", dev._headers("MESSAGE", cid), bomb, "Application/MANSCDP+xml"), cid)
    assert r.status == 400
