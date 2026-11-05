import asyncio

import pytest

from registrar.relay import RelayError, Subscriber, TokenStore
from registrar.rtp import Reorder, RtpPacket, parse_rtp


def pkt(seq, payload=b"x", ssrc=1, ts=0, marker=False):
    return RtpPacket(seq & 0xFFFF, ts, ssrc, marker, 96, payload)


def raw(seq=1, ts=2, ssrc=3, payload=b"hello", cc=0, ext=None, pad=0, marker=False, version=2):
    b0 = (version << 6) | (0x20 if pad else 0) | (0x10 if ext is not None else 0) | cc
    h = bytes([b0, (0x80 if marker else 0) | 96]) + seq.to_bytes(2, "big") + ts.to_bytes(4, "big") + ssrc.to_bytes(4, "big") + b"\xAA" * (4 * cc)
    if ext is not None:
        h += (0xBEDE).to_bytes(2, "big") + (len(ext) // 4).to_bytes(2, "big") + ext
    return h + payload + (b"\x00" * (pad - 1) + bytes([pad]) if pad else b"")


# ------------------------------------------------------------------- parsing
def test_a_plain_packet_is_read_correctly():
    p = parse_rtp(raw(seq=500, ts=90000, ssrc=0xDEADBEEF, payload=b"abc", marker=True))
    assert (p.seq, p.timestamp, p.ssrc, p.marker, p.payload_type, p.payload) == (500, 90000, 0xDEADBEEF, True, 96, b"abc")


def test_long_padding_that_fits_inside_the_packet_is_valid():
    assert parse_rtp(raw(pad=200, payload=b"ab")).payload == b"ab"


def test_csrc_list_header_extension_and_padding_are_skipped():
    assert parse_rtp(raw(payload=b"data", cc=2)).payload == b"data"
    assert parse_rtp(raw(payload=b"data", ext=b"\x01\x02\x03\x04\x05\x06\x07\x08")).payload == b"data"
    assert parse_rtp(raw(payload=b"data", pad=4)).payload == b"data"
    assert parse_rtp(raw(payload=b"data", cc=1, ext=b"\x00" * 4, pad=3)).payload == b"data"       # all three together


@pytest.mark.parametrize("bad", [b"", b"\x80" * 5, raw(version=1), raw(version=0), raw(cc=3)[:20],
                                 raw(ext=b"\x00" * 8)[:16], bytes([0xA0, 96]) + b"\x00" * 10 + b"abc" + bytes([250]), bytes([0xA0, 96]) + b"\x00" * 10 + b"\x00"])
def test_damaged_or_foreign_packets_are_rejected(bad):
    assert parse_rtp(bad) is None


def test_a_header_extension_that_claims_to_be_longer_than_the_packet_is_rejected():
    lying = bytes([0x90, 96]) + b"\x00" * 10 + (0xBEDE).to_bytes(2, "big") + (500).to_bytes(2, "big") + b"short"
    assert parse_rtp(lying) is None


# ------------------------------------------------------------------ re-ordering
def feed(r, seqs):
    out = []
    for s in seqs:
        out += [p.seq for p in r.push(pkt(s))]
    return out


def test_packets_in_order_pass_straight_through():
    assert feed(Reorder(), range(100, 110)) == list(range(100, 110))


def test_swapped_neighbours_are_put_back_in_order():
    r = Reorder()
    assert feed(r, [10, 12, 11, 13, 15, 14, 16]) == [10, 11, 12, 13, 14, 15, 16] and r.lost == 0


def test_a_lost_packet_is_skipped_once_enough_later_ones_have_arrived():
    r = Reorder(window=4)
    out = feed(r, [1, 2, 4, 5, 6, 7, 8, 9])                                    # 3 never comes
    assert out == [1, 2, 4, 5, 6, 7, 8, 9] and r.lost == 1


def test_a_lost_packet_is_skipped_after_a_short_wait_even_if_nothing_more_arrives():
    t = [0.0]
    r = Reorder(max_wait=0.25, clock=lambda: t[0])
    assert feed(r, [1, 2, 4]) == [1, 2] and r.flush_expired() == []            # 4 is held for a moment, in case 3 is just late
    t[0] = 0.3
    assert [p.seq for p in r.flush_expired()] == [4] and r.lost == 1


def test_duplicates_are_dropped():
    r = Reorder()
    assert feed(r, [1, 2, 2, 3, 1, 3]) == [1, 2, 3] and r.duplicates == 3


def test_sequence_numbers_wrap_around_at_65536():
    r = Reorder()
    assert feed(r, [65534, 65535, 0, 1, 3, 2, 4]) == [65534, 65535, 0, 1, 2, 3, 4] and r.lost == 0
    r2 = Reorder(window=3)
    assert feed(r2, [65535, 1, 2, 3, 4]) == [65535, 1, 2, 3, 4] and r2.lost == 1       # the one lost is number 0, across the wrap


def test_a_recorder_that_restarts_its_numbering_is_picked_up_again():
    r = Reorder(resync_after=20)
    feed(r, range(5000, 5010))
    out = feed(r, range(100, 160))                                              # the recorder's counter jumped back
    assert out[-1] == 159 and r.resyncs == 1 and len(out) > 40


def test_a_huge_jump_forward_is_counted_as_loss_not_a_crash():
    r = Reorder(window=3)
    out = feed(r, [1, 2, 30000, 30001, 30002, 30003, 30004])
    assert out[:2] == [1, 2] and out[-1] == 30004 and r.lost == 29997


# ------------------------------------------------------------------- viewers
async def test_a_viewer_that_falls_too_far_behind_is_closed_not_fed_damaged_video():
    s = Subscriber(max_bytes=100, now=0)
    assert s.offer(b"a" * 60) and not s.offer(b"b" * 60) and s.closed and s.reason
    assert await s.get() == b"a" * 60                                           # what it already had is still delivered
    assert await s.get() is None and await s.get() is None                       # then it ends, and keeps ending


async def test_reading_makes_room_again():
    s = Subscriber(max_bytes=100, now=0)
    for _ in range(5):
        assert s.offer(b"x" * 80)
        assert await s.get() == b"x" * 80


# -------------------------------------------------------------------- links
def test_play_links_are_single_use_and_expire():
    t = [0.0]
    store = TokenStore(ttl=60, clock=lambda: t[0])
    tok = store.issue(("dev", 1))
    assert store.consume(tok) == ("dev", 1) and store.consume(tok) is None
    tok = store.issue(("dev", 1))
    t[0] = 61
    assert store.consume(tok) is None and store.consume("made-up") is None


def test_links_are_unguessable_and_limited_per_stream_and_in_total():
    store = TokenStore(ttl=60, per_key=3, max_items=50)
    toks = {store.issue(("a", 1)) for _ in range(3)}
    assert len(toks) == 3 and all(len(t) >= 30 for t in toks)
    with pytest.raises(RelayError) as e:
        store.issue(("a", 1))
    assert e.value.status == 429
    for i in range(500):
        store.issue(("k", i))
    assert len(store._d) <= 50                                                   # memory stays bounded
