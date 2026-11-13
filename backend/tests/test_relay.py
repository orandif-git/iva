import asyncio
import random
import socket

import httpx
import pytest

from tests.fake_device import FakeDevice, Media, channel_id
from tests.test_registrar_flow import env  # noqa: F401  (the fixture that starts a registrar, its relay and a fake recorder)

DATA = bytes(random.Random(1).randrange(256) for _ in range(30_000))                # stands in for program-stream bytes


def _loopback_alias_available() -> bool:
    """Linux routes all of 127.0.0.0/8 to the loopback; macOS has only 127.0.0.1 unless an alias is added by hand. The two spoofing
    tests below need a *different* source address on this machine, so they are skipped where the alias does not exist."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.2", 0))
        s.close()
        return True
    except OSError:
        return False


requires_loopback_alias = pytest.mark.skipif(not _loopback_alias_available(), reason="this machine has no 127.0.0.2 loopback alias")


async def ready(env, cred, dev):
    env.cfg.probe_retries = 0                                       # no automatic check: the viewer's own request starts the stream
    assert await dev.register() == 200
    await env.until(lambda: _channels(env, cred["device_id"]))


async def _channels(env, device_id):
    s = env.reg.sessions.get(device_id)
    return s is not None and bool(s.channels)


def auth(env):
    return {"Authorization": "Bearer " + env.cfg.service_token}


async def link(env, client, device_id, channel_no=1, headers="auth"):
    return await client.post(f"{env.reg.relay_base}/relay/v1/streams", json={"device_id": device_id, "channel_no": channel_no},
                             headers=auth(env) if headers == "auth" else headers)


async def watch(client, url, timeout=20):
    """Read a play link to the end. Returns (status, bytes)."""
    got = bytearray()
    async with client.stream("GET", url, timeout=timeout) as r:
        if r.status_code != 200:
            return r.status_code, await r.aread()
        async for chunk in r.aiter_bytes():
            got += chunk
    return 200, bytes(got)


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=20) as c:
        yield c


async def view(env, http, cred, channel_no=1):
    r = await link(env, http, cred["device_id"], channel_no)
    assert r.status_code == 201, r.text
    return await watch(http, r.json()["url"])


# ------------------------------------------------------------- the main promise
@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_a_viewer_receives_exactly_the_bytes_the_recorder_sent(env, http, transport):
    env.cfg.relay_media_transport = transport
    env.cfg.relay_stall_seconds = 1.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA, gap=0.003))
    await ready(env, cred, dev)
    status, body = await view(env, http, cred)
    assert status == 200 and body == Media(data=DATA).expected()          # every byte, in order, with the RTP wrapping removed
    assert ("TCP/RTP/AVP" in dev.invites[0]) == (transport == "tcp") and ("a=setup:passive" in dev.invites[0]) == (transport == "tcp")
    assert dev.bye_count == 1                                              # and the recorder was told to stop afterwards


async def test_packets_that_arrive_out_of_order_are_put_back_in_order(env, http):
    env.cfg.relay_stall_seconds = 1.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA, reorder=True, gap=0.003))
    await ready(env, cred, dev)
    status, body = await view(env, http, cred)
    assert status == 200 and body == Media(data=DATA).expected()


async def test_lost_packets_are_skipped_not_waited_for(env, http):
    env.cfg.relay_stall_seconds = 1.0
    m = Media(data=DATA, drop={5, 6, 20}, gap=0.003)
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    r = await link(env, http, cred["device_id"])
    pending = asyncio.create_task(watch(http, r.json()["url"]))
    await asyncio.sleep(0.3)
    stream = env.reg.relay.streams[(cred["device_id"], 1)]
    status, body = await pending
    assert status == 200 and body == m.expected() and stream.reorder.lost == 3


async def test_viewers_share_one_recorder_stream_and_it_ends_after_the_last_leaves(env, http):
    env.cfg.relay_stall_seconds = 5.0
    m = Media(data=DATA * 3, gap=0.02)                                     # about 1.8 s of video
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    first = await link(env, http, cred["device_id"])
    t1 = asyncio.create_task(watch(http, first.json()["url"], timeout=4))
    await asyncio.sleep(0.4)
    second = await link(env, http, cred["device_id"])
    t2 = asyncio.create_task(watch(http, second.json()["url"], timeout=4))
    await asyncio.sleep(0.5)
    assert len(dev.invites) == 1                                           # two viewers, one INVITE: recorders only serve a few streams
    stream = env.reg.relay.streams[(cred["device_id"], 1)]
    assert len(stream.subscribers) == 2
    t1.cancel(); t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    await env.until(lambda: _gone_stream(env, cred))                       # the last viewer left: the stream ends after the grace period
    assert dev.bye_count == 1
    r = await link(env, http, cred["device_id"])
    assert r.status_code == 201
    asyncio.create_task(watch(http, r.json()["url"], timeout=3))
    await env.until(lambda: _invites(dev, 2))                              # a later viewer starts a fresh stream


async def _gone_stream(env, cred):
    return not env.reg.relay.streams


async def _invites(dev, n):
    return len(dev.invites) >= n


async def test_a_second_viewer_joining_part_way_gets_the_later_part_of_the_stream(env, http):
    env.cfg.relay_stall_seconds = 1.0
    m = Media(data=DATA, gap=0.02)
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    a = asyncio.create_task(view(env, http, cred))
    await asyncio.sleep(0.25)
    status, late = await view(env, http, cred)
    _, early = await a
    assert early == m.expected() and 0 < len(late) < len(early) and early.endswith(late)


async def test_a_viewer_who_leaves_early_ends_the_stream(env, http):
    env.cfg.relay_stall_seconds = 5.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA * 3, gap=0.02))
    await ready(env, cred, dev)
    r = await link(env, http, cred["device_id"])
    async with http.stream("GET", r.json()["url"]) as resp:
        got = 0
        async for chunk in resp.aiter_bytes():
            got += len(chunk)
            if got > 3000:
                break                                                      # walk away mid-stream
    await env.until(lambda: _gone_stream(env, cred))
    assert dev.bye_count == 1 and not env.reg.relay.streams


# ---------------------------------------------------------------- permissions
async def test_asking_for_a_stream_needs_the_service_token_and_valid_input(env, http):
    t, u, cred, dev = await env.onboard()
    await ready(env, cred, dev)
    url = f"{env.reg.relay_base}/relay/v1/streams"
    ok = {"device_id": cred["device_id"], "channel_no": 1}
    assert (await http.post(url, json=ok)).status_code == 401
    assert (await http.post(url, json=ok, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await http.get(url)).status_code == 401
    for bad in ({}, {"device_id": 5, "channel_no": 1}, {"device_id": "x", "channel_no": "1"}, {"device_id": "x", "channel_no": 0},
                {"device_id": "x", "channel_no": 999}, {"device_id": "x", "channel_no": True}, {"device_id": "a" * 99, "channel_no": 1}):
        assert (await http.post(url, json=bad, headers=auth(env))).status_code == 422, bad
    assert (await http.post(url, content=b"not json", headers=auth(env))).status_code == 422
    assert (await http.post(url, json={**ok, "device_id": "34020000001180000000"}, headers=auth(env))).json()["error"]["code"] == "device_offline"
    assert (await http.post(url, json={**ok, "channel_no": 9}, headers=auth(env))).json()["error"]["code"] == "unknown_channel"


async def test_a_channel_the_recorder_reports_off_cannot_be_viewed(env, http):
    chans = [(channel_id(1), "On", "ON"), (channel_id(2), "Off", "OFF")]
    t, u, cred, dev = await env.onboard(channels=chans)
    await ready(env, cred, dev)
    assert (await link(env, http, cred["device_id"], 2)).json()["error"]["code"] == "channel_offline"


async def test_a_play_link_works_once_and_then_expires(env, http):
    env.cfg.relay_stall_seconds = 1.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA, gap=0.003))
    await ready(env, cred, dev)
    url = (await link(env, http, cred["device_id"])).json()["url"]
    assert (await watch(http, url))[0] == 200
    again = await watch(http, url)
    assert again[0] == 404 and b"link_invalid" in again[1]
    env.reg.relay.tokens.ttl = 0.2
    stale = (await link(env, http, cred["device_id"])).json()["url"]
    await asyncio.sleep(0.3)
    assert (await watch(http, stale))[0] == 404
    assert (await http.get(f"{env.reg.relay_base}/relay/v1/play/made-up-token")).status_code == 404


async def test_too_many_links_waiting_for_one_stream_are_refused(env, http):
    t, u, cred, dev = await env.onboard()
    await ready(env, cred, dev)
    codes = [(await link(env, http, cred["device_id"])).status_code for _ in range(10)]
    assert codes[:8] == [201] * 8 and codes[8:] == [429, 429]


async def test_a_recorder_may_only_serve_as_many_streams_as_the_limit_allows(env, http):
    env.cfg.relay_max_streams_per_device = 2
    env.cfg.relay_stall_seconds = 5.0
    chans = [(channel_id(i), f"C{i}", "ON") for i in (1, 2, 3)]
    t, u, cred, dev = await env.onboard(channels=chans, media=Media(data=DATA * 5, gap=0.02))
    await ready(env, cred, dev)
    watchers = []
    for ch in (1, 2):
        r = await link(env, http, cred["device_id"], ch)
        watchers.append(asyncio.create_task(watch(http, r.json()["url"], timeout=5)))
        await asyncio.sleep(0.3)
    third = await link(env, http, cred["device_id"], 3)
    assert third.status_code == 429 and third.json()["error"]["code"] == "too_many_streams"
    assert (await link(env, http, cred["device_id"], 1)).status_code == 201           # joining a stream that is already running is fine
    for w in watchers:
        w.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)


async def test_the_whole_relay_has_a_limit_too(env, http):
    env.cfg.relay_max_streams = 1
    env.cfg.relay_stall_seconds = 5.0
    t1, u1, c1, d1 = await env.onboard(media=Media(data=DATA * 5, gap=0.02))
    t2, u2, c2, d2 = await env.onboard(media=Media(data=DATA * 5, gap=0.02))
    await ready(env, c1, d1)
    await ready(env, c2, d2)
    r = await link(env, http, c1["device_id"])
    w = asyncio.create_task(watch(http, r.json()["url"], timeout=5))
    await asyncio.sleep(0.3)
    assert (await link(env, http, c2["device_id"])).status_code == 429
    w.cancel()
    await asyncio.gather(w, return_exceptions=True)


# -------------------------------------------------------------- when things go wrong
async def test_a_recorder_that_goes_silent_ends_the_stream_for_its_viewers(env, http):
    env.cfg.relay_stall_seconds = 1.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA, gap=0.003, stall_after=10))
    await ready(env, cred, dev)
    status, body = await view(env, http, cred)
    assert status == 200 and body == Media(data=DATA).expected()[:10_000]            # what arrived before the silence, then a clean end
    assert dev.bye_count == 1 and not env.reg.relay.streams


async def test_a_recorder_that_refuses_gives_the_viewer_a_clear_error_and_leaks_nothing(env, http):
    t, u, cred, dev = await env.onboard(refuse_invite=True)
    await ready(env, cred, dev)
    r = await link(env, http, cred["device_id"])
    assert r.status_code == 201                                                       # we cannot know yet
    status, body = await watch(http, r.json()["url"])
    assert status == 502 and b"device_refused" in body
    assert not env.reg.relay.streams


async def test_a_recorder_going_offline_ends_its_streams(env, http):
    env.cfg.relay_stall_seconds = 30
    t, u, cred, dev = await env.onboard(media=Media(data=DATA * 6, gap=0.02))
    await ready(env, cred, dev)
    r = await link(env, http, cred["device_id"])
    watcher = asyncio.create_task(watch(http, r.json()["url"], timeout=8))
    await asyncio.sleep(0.4)
    assert env.reg.relay.streams
    env.clock.t += env.cfg.keepalive_timeout + 1
    await env.reg.reap_once()                                                          # the recorder has gone quiet: it is dropped
    status, body = await asyncio.wait_for(watcher, 5)
    assert status == 200 and len(body) < len(Media(data=DATA * 6).expected()) and not env.reg.relay.streams


async def test_a_slow_viewer_is_disconnected_and_the_others_are_unharmed(env):
    env.cfg.relay_stall_seconds = 3.0
    env.cfg.relay_subscriber_buffer = 5000
    m = Media(data=DATA, gap=0.003)
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    stream, fast = await env.reg.relay.acquire(cred["device_id"], 1, subscriber=True)
    _, slow = await env.reg.relay.acquire(cred["device_id"], 1, subscriber=True)      # never reads
    got = bytearray()
    while (chunk := await asyncio.wait_for(fast.get(), 5)) is not None:
        got += chunk
    assert bytes(got) == m.expected()                                                   # the fast viewer received everything, undisturbed...
    assert slow.closed and "keep up" in slow.reason                                     # ...and the slow one was cut off


# ------------------------------------------------------------ untrusted media
@requires_loopback_alias
async def test_only_the_recorders_own_address_may_send_media(env, http):
    env.cfg.relay_stall_seconds = 5.0
    t, u, cred, dev = await env.onboard(send_rtp=False)
    await ready(env, cred, dev)
    stream, sub = await env.reg.relay.acquire(cred["device_id"], 1, subscriber=True)
    port = stream._receiver.port
    spoof = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    spoof.bind(("127.0.0.2", 0))                                                         # a different address on the same machine
    for i in range(10):
        spoof.sendto(FakeDevice.rtp_packet(i, 0, 1, b"evil" * 10), ("127.0.0.1", port))
    await asyncio.sleep(0.3)
    assert stream.packets == 0 and stream._receiver.refused == 10
    env.reg.relay.release(stream, sub)


async def test_a_different_stream_id_and_garbage_are_ignored(env):
    env.cfg.relay_stall_seconds = 5.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA * 4, gap=0.02))
    await ready(env, cred, dev)
    stream, sub = await env.reg.relay.acquire(cred["device_id"], 1, subscriber=True)
    await asyncio.sleep(0.3)
    before = stream.bytes
    me = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)                                # same address as the recorder, but a different stream id
    for i in range(5):
        me.sendto(FakeDevice.rtp_packet(9000 + i, 0, 0x7777, b"intruder"), ("127.0.0.1", stream._receiver.port))
    for junk in (b"", b"\x00" * 30, b"\x80" * 4, b"GET / HTTP/1.1\r\n\r\n"):
        me.sendto(junk, ("127.0.0.1", stream._receiver.port))
    await asyncio.sleep(0.3)
    assert stream.foreign == 5 and stream._receiver.proto.invalid == 4
    received = []
    while not sub.q.empty():
        received.append(sub.q.get_nowait())
    assert b"intruder" not in b"".join(c for c in received if c)                       # nothing from the other source reached the viewer
    assert stream.bytes >= before
    env.reg.relay.release(stream, sub)


@requires_loopback_alias
async def test_a_stranger_cannot_connect_to_the_tcp_media_port(env):
    env.cfg.relay_media_transport = "tcp"
    env.cfg.relay_stall_seconds = 5.0
    t, u, cred, dev = await env.onboard(send_rtp=False)
    await ready(env, cred, dev)
    stream, sub = await env.reg.relay.acquire(cred["device_id"], 1, subscriber=True)
    port = stream._receiver.port
    s = socket.socket()
    s.bind(("127.0.0.2", 0))
    s.setblocking(False)
    await asyncio.get_running_loop().sock_connect(s, ("127.0.0.1", port))
    pkt = FakeDevice.rtp_packet(1, 0, 1, b"evil")
    await asyncio.get_running_loop().sock_sendall(s, len(pkt).to_bytes(2, "big") + pkt)
    await asyncio.sleep(0.3)
    assert stream.packets == 0 and stream._receiver.refused >= 1
    s.close()
    env.reg.relay.release(stream, sub)


# -------------------------------------------------------------------- reporting
async def test_the_stream_listing_reports_health_to_those_allowed_to_see_it(env, http):
    env.cfg.relay_stall_seconds = 5.0
    t, u, cred, dev = await env.onboard(media=Media(data=DATA * 3, gap=0.02))
    await ready(env, cred, dev)
    r = await link(env, http, cred["device_id"])
    w = asyncio.create_task(watch(http, r.json()["url"], timeout=4))
    await asyncio.sleep(0.5)
    info = (await http.get(f"{env.reg.relay_base}/relay/v1/streams", headers=auth(env))).json()["streams"]
    assert len(info) == 1 and info[0]["device_id"] == cred["device_id"] and info[0]["viewers"] == 1 and info[0]["packets"] > 5
    assert info[0]["lost"] == 0 and info[0]["seconds_since_packet"] < 1
    assert (await http.get(f"{env.reg.relay_base}/relay/v1/streams")).status_code == 401
    assert (await http.get(f"{env.reg.relay_base}/healthz")).json() == {"ok": True}
    w.cancel()
    await asyncio.gather(w, return_exceptions=True)
