"""RTP media from a recorder: parsing, putting packets back in order, and the two ways a recorder can send it to us.

  UDP  one datagram per RTP packet. Simple, but a lost datagram damages a picture and packets can arrive out of order.
  TCP  the recorder connects to us and sends packets with a 2-byte length in front of each (RFC 4571). Nothing is lost,
       and it passes firewalls better, which makes it the safer choice across the internet.

Everything that arrives is untrusted network input: it is checked, and only the recorder's own address is accepted."""
import asyncio
import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RtpPacket:
    seq: int
    timestamp: int
    ssrc: int
    marker: bool
    payload_type: int
    payload: bytes


def parse_rtp(data: bytes) -> RtpPacket | None:
    """The packet, or None if it is not valid RTP version 2. Handles CSRC lists, header extensions and padding."""
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    off = 12 + 4 * (data[0] & 0x0F)
    if len(data) < off:
        return None
    if data[0] & 0x10:                                     # header extension
        if len(data) < off + 4:
            return None
        off += 4 + 4 * int.from_bytes(data[off + 2:off + 4], "big")
        if off > len(data):
            return None
    end = len(data)
    if data[0] & 0x20:                                     # padding: the last byte says how many bytes to ignore
        pad = data[-1]
        if pad == 0 or pad > end - off:
            return None
        end -= pad
    return RtpPacket(int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:8], "big"), int.from_bytes(data[8:12], "big"),
                     bool(data[1] & 0x80), data[1] & 0x7F, bytes(data[off:end]))


class Reorder:
    """Hands packets on in sequence order. A packet that arrives early is held for a short while; if the missing one never comes the gap
    is counted as lost and the stream carries on. Sequence numbers wrap at 65536."""

    def __init__(self, window: int = 16, max_wait: float = 0.25, clock=time.monotonic, resync_after: int = 100):
        self.window, self.max_wait, self.clock, self.resync_after = window, max_wait, clock, resync_after
        self.expected: int | None = None
        self._held: dict = {}
        self._old_run = 0
        self.lost = self.duplicates = self.resyncs = 0

    def push(self, pkt: RtpPacket) -> list:
        if self.expected is None:
            self.expected = pkt.seq
        diff = (pkt.seq - self.expected) & 0xFFFF
        if diff >= 0x8000:                                 # one we already passed on (or a very late one)
            self.duplicates += 1
            self._old_run += 1
            if self._old_run >= self.resync_after:         # everything is "old": the recorder restarted its numbering
                self.expected, self._old_run, self._held = pkt.seq, 0, {}
                self.resyncs += 1
                return self.push(pkt)
            return []
        self._old_run = 0
        if diff == 0:
            self.expected = (self.expected + 1) & 0xFFFF
            return [pkt] + self._drain()
        if pkt.seq in self._held:
            self.duplicates += 1
            return []
        self._held[pkt.seq] = (self.clock(), pkt)
        return self._skip() if len(self._held) > self.window else []

    def flush_expired(self) -> list:
        """Called regularly: if something has waited too long for a missing packet, give up on the missing one."""
        if self._held and self.clock() - min(t for t, _ in self._held.values()) >= self.max_wait:
            return self._skip()
        return []

    def _drain(self) -> list:
        out = []
        while self.expected in self._held:
            out.append(self._held.pop(self.expected)[1])
            self.expected = (self.expected + 1) & 0xFFFF
        return out

    def _skip(self) -> list:
        nxt = min(self._held, key=lambda s: (s - self.expected) & 0xFFFF)
        self.lost += (nxt - self.expected) & 0xFFFF
        self.expected = nxt
        return self._drain()


def _ports(port_min: int, port_max: int) -> list:
    ports = list(range(port_min + (port_min % 2), port_max + 1, 2))          # RTP traditionally uses even ports
    random.shuffle(ports)
    return ports[:80]


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, expect_ip: str, on_packet):
        self.expect_ip, self.on_packet = expect_ip, on_packet
        self.refused = self.invalid = 0

    def datagram_received(self, data, addr):
        if addr[0] != self.expect_ip:                      # not the recorder: ignore (anyone can send UDP to a port)
            self.refused += 1
            return
        pkt = parse_rtp(data)
        if pkt is None:
            self.invalid += 1
            return
        self.on_packet(pkt)


class UdpReceiver:
    def __init__(self, expect_ip: str, on_packet):
        self.proto = _UdpProtocol(expect_ip, on_packet)
        self._transport = None
        self.port = 0

    async def start(self, bind: str, port_min: int, port_max: int) -> int:
        loop = asyncio.get_running_loop()
        for port in _ports(port_min, port_max):
            try:
                self._transport, _ = await loop.create_datagram_endpoint(lambda: self.proto, local_addr=(bind, port))
                self.port = port
                return port
            except OSError:
                continue
        raise OSError("no free media port")

    @property
    def refused(self) -> int:
        return self.proto.refused

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None


class TcpReceiver:
    """Listens for ONE connection from the recorder (we told it to connect to us). Packets arrive as: 2-byte length, then the RTP packet."""

    def __init__(self, expect_ip: str, on_packet, on_closed=lambda: None):
        self.expect_ip, self.on_packet, self.on_closed = expect_ip, on_packet, on_closed
        self.refused = self.invalid = 0
        self.port = 0
        self._server = None
        self._writer = None
        self._closing = False

    async def start(self, bind: str, port_min: int, port_max: int) -> int:
        for port in _ports(port_min, port_max):
            try:
                self._server = await asyncio.start_server(self._handle, bind, port)
                self.port = port
                return port
            except OSError:
                continue
        raise OSError("no free media port")

    async def _handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if (peer and peer[0] != self.expect_ip) or self._writer is not None:      # a stranger, or a second connection
            self.refused += 1
            writer.close()
            return
        self._writer = writer
        try:
            while True:
                size = int.from_bytes(await reader.readexactly(2), "big")
                data = await reader.readexactly(size)
                pkt = parse_rtp(data)
                if pkt is None:
                    self.invalid += 1
                    continue
                self.on_packet(pkt)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            if not self._closing:
                self.on_closed()

    def close(self) -> None:
        self._closing = True
        if self._server:
            self._server.close()
            self._server = None
        if self._writer:
            self._writer.close()
