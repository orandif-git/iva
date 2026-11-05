"""The keyframe cache: what a viewer who joins part-way is given."""
import asyncio
import random

import httpx
import pytest

from registrar.gop import PACK, GopCache
from tests.fake_device import Media
from tests.test_registrar_flow import env  # noqa: F401
from tests.test_relay import link, ready, watch

R = random.Random(7)


def fill(n):
    return bytes(R.randrange(0x11, 0x7F) for _ in range(n))            # never a zero byte, so no accidental start codes


def pack():
    return PACK + b"\x44" + fill(9)


def pes(nal_header: int, body: int, sps: bool = False) -> bytes:
    nals = b""
    if sps:
        nals += b"\x00\x00\x00\x01\x67" + fill(20) + b"\x00\x00\x00\x01\x68" + fill(6)
    return b"\x00\x00\x01\xe0" + b"\x00\x00" + fill(6) + nals + b"\x00\x00\x00\x01" + bytes([nal_header]) + fill(body)


def gop(p_frames=9, idr_slices=1, sps=True, idr_body=4000):
    out = []
    for k in range(idr_slices):
        out.append(pack() + pes(0x65, idr_body // idr_slices, sps=sps and k == 0))
    for _ in range(p_frames):
        out.append(pack() + pes(0x41, 1500))
    return b"".join(out)


def feed_all(cache: GopCache, data: bytes, size: int):
    for i in range(0, len(data), size):
        cache.feed(data[i:i + size])


@pytest.mark.parametrize("size", [1, 2, 3, 7, 100, 1000])
def test_the_cache_always_starts_at_the_latest_keyframe_whatever_the_chunking(size):
    gops = [gop() for _ in range(3)]
    stream = b"".join(gops)
    c = GopCache(1_000_000)
    feed_all(c, stream, size)
    snap = c.snapshot()
    assert snap == gops[-1] and stream.endswith(snap)                    # exactly the last group of pictures, byte for byte
    assert snap.startswith(PACK) and b"\x00\x00\x00\x01\x67" in snap[:80] and c.keyframes == 3


def test_it_is_continuous_while_a_group_of_pictures_grows():
    c = GopCache(1_000_000)
    first, second = gop(), gop(p_frames=4)
    c.feed(first)
    c.feed(second[:3000])                                                # the next keyframe has begun but its picture is only partly in
    assert c.snapshot() == second[:3000] and c.snapshot().startswith(PACK)
    c.feed(second[3000:])
    assert c.snapshot() == second


def test_nothing_is_replayed_before_the_first_keyframe():
    c = GopCache(1_000_000)
    c.feed(pack() + pes(0x41, 800) + pack() + pes(0x41, 800))            # joined mid-group: only P pictures so far
    assert c.snapshot() == b"" and not c.valid
    c.feed(gop())
    assert c.valid


def test_a_picture_sent_as_several_idr_slices_is_not_cut_in_the_middle():
    g = gop(idr_slices=3, sps=False)                                     # a recorder that never repeats the SPS
    c = GopCache(1_000_000)
    c.feed(gop(sps=False))
    c.feed(g)
    assert c.snapshot() == g and c.snapshot().count(b"\x00\x00\x00\x01\x65") == 3


def test_a_stream_that_is_not_h264_or_has_no_keyframes_costs_nothing_and_changes_nothing():
    c = GopCache(50_000)
    junk = b"".join(pack() + b"\x00\x00\x01\xe0" + fill(1200) for _ in range(100))
    feed_all(c, junk, 1000)
    assert c.snapshot() == b"" and len(c.buf) <= 50_000 and c.overflows >= 1


def test_a_keyframe_interval_longer_than_the_limit_gives_no_replay_rather_than_a_broken_one():
    c = GopCache(20_000)
    c.feed(gop(p_frames=30))                                             # about 50 KB: more than the limit
    assert c.snapshot() == b"" and c.overflows == 1
    short = gop(p_frames=2)
    c.feed(short)
    assert c.snapshot() == short                                         # recovers at the next keyframe


def test_it_can_be_turned_off():
    c = GopCache(0)
    c.feed(gop())
    assert c.snapshot() == b"" and len(c.buf) == 0


def test_a_start_code_split_across_chunks_is_still_found():
    g = gop(sps=True)
    c = GopCache(1_000_000)
    i = g.index(b"\x00\x00\x00\x01\x67") + 2                             # split inside the SPS start code
    c.feed(g[:i]); c.feed(g[i:])
    assert c.snapshot() == g


# ----------------------------------------------------------------------- through the relay
GOPS = [gop(p_frames=9) for _ in range(12)]
STREAM = b"".join(GOPS)                   # about 230 KB, a keyframe every ~19 KB


async def test_a_viewer_who_joins_part_way_starts_at_a_keyframe_and_misses_nothing_after(env):
    env.cfg.relay_stall_seconds = 1.0
    m = Media(data=STREAM, payload_size=1000, gap=0.02)
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    async with httpx.AsyncClient(timeout=30) as http:
        r1 = await link(env, http, cred["device_id"]); assert r1.status_code == 201
        a = asyncio.create_task(watch(http, r1.json()["url"]))
        await asyncio.sleep(1.2)
        r2 = await link(env, http, cred["device_id"]); assert r2.status_code == 201
        status, late = await watch(http, r2.json()["url"])
        _, early = await a
    assert status == 200 and early == m.expected()
    assert early.endswith(late) and 0 < len(late) < len(early)                               # continuous: nothing missing between the replay and the live bytes
    assert late.startswith(PACK) and b"\x00\x00\x00\x01\x67" in late[:80]                    # and it starts on a keyframe, ready to show a picture at once


async def test_with_the_cache_off_a_late_viewer_joins_where_the_stream_is(env):
    env.cfg.relay_stall_seconds = 1.0
    env.cfg.relay_gop_cache_bytes = 0
    m = Media(data=STREAM, payload_size=1000, gap=0.02)
    t, u, cred, dev = await env.onboard(media=m)
    await ready(env, cred, dev)
    async with httpx.AsyncClient(timeout=30) as http:
        a = asyncio.create_task(watch(http, (await link(env, http, cred["device_id"])).json()["url"]))
        await asyncio.sleep(1.2)
        _, late = await watch(http, (await link(env, http, cred["device_id"])).json()["url"])
        _, early = await a
    assert early.endswith(late)
    offsets, pos = [], 0
    for g in GOPS:
        offsets.append(pos); pos += len(g)
    assert all(o % 1000 for o in offsets[1:])                  # no keyframe happens to start on a packet boundary, so only a replay could start on one
    assert not (late.startswith(PACK) and b"\x00\x00\x00\x01\x67" in late[:80])        # off: the late viewer joins wherever the live stream is
