"""A stand-in for a GB28181 recorder. It computes Digest authentication itself (not with the registrar's code), sends XML in
GB2312 like real Dahua units, answers catalogue queries, accepts live-video requests, and sends real RTP packets."""
import asyncio
import hashlib
import re
import socket
import uuid

from registrar import sip

REALM = "3402000000"
SERVER_ID = "34020000002000000001"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class Media:
    """What a fake recorder sends when asked for live video.
    data:        the program-stream bytes to send (real encoded video makes FFmpeg tests possible); None sends synthetic packets
    reorder:     swap neighbouring packets, as the internet sometimes does
    drop:        indexes of packets that never arrive
    stall_after: go silent after this many packets, with the connection left open
    ssrc:        use this stream id instead of the one the server asked for"""

    def __init__(self, data=None, payload_size=1000, gap=0.01, packets=30, reorder=False, drop=(), stall_after=None, ssrc=None, seq_start=1000):
        self.data, self.payload_size, self.gap, self.packets = data, payload_size, gap, packets
        self.reorder, self.drop, self.stall_after, self.ssrc, self.seq_start = reorder, set(drop), stall_after, ssrc, seq_start

    def payloads(self) -> list:
        if self.data is not None:
            return [self.data[i:i + self.payload_size] for i in range(0, len(self.data), self.payload_size)]
        return [b"\x00\x00\x01\xba" + bytes([i % 256]) * (self.payload_size - 4) for i in range(self.packets)]

    def expected(self) -> bytes:
        """The bytes a correct relay hands on: everything that was sent and not dropped, in the original order."""
        return b"".join(p for i, p in enumerate(self.payloads()) if i not in self.drop)


def channel_id(n: int, type_code: str = "132") -> str:
    return f"{REALM}{type_code}0{n:06d}"


class FakeDevice:
    def __init__(self, device_id, password, server, transport="udp", channels=None, refuse_invite=False, send_rtp=True, batches=1, media=None):
        self.id, self.password, self.server, self.transport = device_id, password, server, transport
        self.channels = channels if channels is not None else [(channel_id(1), "大门 Gate", "ON"), (channel_id(2), "仓库 Godown", "ON")]
        self.refuse_invite, self.send_rtp, self.batches = refuse_invite, send_rtp, batches
        self.media = media or Media()
        self.invites: list = []
        self.bye_count = 0
        self._media_task = None
        self.pending: dict[str, asyncio.Future] = {}
        self.cseq = 0
        self.seen: list[str] = []                    # methods the server sent us
        self.invite_sdp = ""
        self.catalog_queries = 0
        self.port = 0
        self._send = None
        self._tasks = set()
        self._buf = bytearray()
        self._udp = None
        self._writer = None
        self._rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def start(self):
        loop = asyncio.get_running_loop()
        if self.transport == "udp":
            dev = self

            class P(asyncio.DatagramProtocol):
                def datagram_received(self, data, addr):
                    dev._spawn(dev._on_data(data))
            self._udp, _ = await loop.create_datagram_endpoint(P, local_addr=("127.0.0.1", 0), remote_addr=self.server)
            self.port = self._udp.get_extra_info("sockname")[1]
            self._send = self._udp.sendto
        else:
            reader, self._writer = await asyncio.open_connection(*self.server)
            self.port = self._writer.get_extra_info("sockname")[1]
            self._send = self._writer.write
            self._spawn(self._tcp_loop(reader))

    async def stop(self):
        for t in list(self._tasks):
            t.cancel()
        if self._udp:
            self._udp.close()
        if self._writer:
            self._writer.close()
        self._rtp_sock.close()

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _tcp_loop(self, reader):
        try:
            while data := await reader.read(65536):
                self._buf += data
                while (f := sip.extract_frame(self._buf)) is not None:
                    self._spawn(self._on_data(f))
        except (ConnectionError, asyncio.CancelledError):
            pass

    # ---------------------------------------------------------------- building
    def _headers(self, method, call_id=None, expires=None, extra=()):
        self.cseq += 1
        proto = self.transport.upper()
        lines = [f"Via: SIP/2.0/{proto} 127.0.0.1:{self.port};rport;branch=z9hG4bK{uuid.uuid4().hex[:8]}",
                 f"From: <sip:{self.id}@{REALM}>;tag={uuid.uuid4().hex[:6]}", f"To: <sip:{self.id}@{REALM}>",
                 f"Call-ID: {call_id or uuid.uuid4().hex + chr(64) + chr(102) + chr(97) + chr(107) + chr(101)}", f"CSeq: {self.cseq} {method}",
                 f"Contact: <sip:{self.id}@127.0.0.1:{self.port}>", "Max-Forwards: 70", "User-Agent: FakeNVR"]
        if expires is not None:
            lines.append(f"Expires: {expires}")
        return lines + list(extra)

    def _frame(self, start, lines, body=b"", ctype=None):
        if ctype:
            lines = lines + [f"Content-Type: {ctype}"]
        return (start + "\r\n" + "\r\n".join(lines) + f"\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body

    async def request(self, data: bytes, call_id: str, timeout=3.0):
        fut = asyncio.get_running_loop().create_future()
        self.pending[call_id] = fut
        self._send(data)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending.pop(call_id, None)

    # ------------------------------------------------------------------ actions
    async def register(self, password=None, expires=3600, replay: bytes | None = None, device_id=None):
        """Returns the final status code, or None if the server stayed silent. `replay` re-sends captured bytes."""
        did = device_id or self.id
        pw = self.password if password is None else password
        cid = uuid.uuid4().hex + "@fake"
        uri = f"sip:{SERVER_ID}@{REALM}"
        h = self._headers("REGISTER", cid, expires)
        h[1] = f"From: <sip:{did}@{REALM}>;tag=abc"
        r1 = await self.request(self._frame(f"REGISTER {uri} SIP/2.0", h), cid)
        if r1 is None:
            return None
        if r1.status != 401:
            return r1.status
        www = r1.get("www-authenticate")
        nonce = re.search(r'nonce="([^"]+)"', www).group(1)
        realm = re.search(r'realm="([^"]+)"', www).group(1)
        cnonce, nc = uuid.uuid4().hex[:8], "00000001"
        resp = _md5(f"{_md5(f'{did}:{realm}:{pw}')}:{nonce}:{nc}:{cnonce}:auth:{_md5(f'REGISTER:{uri}')}")
        auth = (f'Authorization: Digest username="{did}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}", '
                f'algorithm=MD5, cnonce="{cnonce}", qop=auth, nc={nc}')
        h2 = self._headers("REGISTER", cid, expires, [auth])
        h2[1] = h[1]
        data = self._frame(f"REGISTER {uri} SIP/2.0", h2)
        self.last_auth_request = data
        if replay:                                   # re-send captured bytes: wait on the Call-ID those bytes carry
            cid = re.search(rb"Call-ID: (\S+)", replay).group(1).decode()
        r2 = await self.request(replay or data, cid)
        return r2.status if r2 else None

    async def keepalive(self):
        body = (f'<?xml version="1.0" encoding="GB2312"?>\r\n<Notify>\r\n<CmdType>Keepalive</CmdType>\r\n<SN>1</SN>\r\n'
                f'<DeviceID>{self.id}</DeviceID>\r\n<Status>OK</Status>\r\n</Notify>\r\n').encode()
        cid = uuid.uuid4().hex + "@fake"
        h = self._headers("MESSAGE", cid)
        r = await self.request(self._frame(f"MESSAGE sip:{SERVER_ID}@{REALM} SIP/2.0", h, body, "Application/MANSCDP+xml"), cid)
        return r.status if r else None

    # ------------------------------------------------------ requests from the server
    async def _on_data(self, data: bytes):
        try:
            msg = sip.parse(data)
        except sip.SipParseError:
            return
        if not msg.is_request:
            fut = self.pending.get(msg.get("call-id"))
            if fut and not fut.done() and msg.status >= 200:
                fut.set_result(msg)
            return
        self.seen.append(msg.method)
        if msg.method == "MESSAGE":
            self._ok(msg)
            if b"Catalog" in msg.body:
                self.catalog_queries += 1
                sn = re.search(rb"<SN>(\d+)</SN>", msg.body).group(1).decode()
                await self._send_catalog(sn)
        elif msg.method == "INVITE":
            self.invite_sdp = msg.body.decode()
            self.invites.append(self.invite_sdp)
            if self.refuse_invite:
                self._reply(msg, 488, "Not Acceptable Here")
                return
            sdp = (f"v=0\r\no={self.id} 0 0 IN IP4 127.0.0.1\r\ns=Play\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
                   f"m=video {self.port + 1} RTP/AVP 96\r\na=sendonly\r\na=rtpmap:96 PS/90000\r\n").encode()
            self._reply(msg, 200, "OK", sdp, "Application/SDP")
        elif msg.method == "ACK":
            if self.send_rtp:
                ip = re.search(r"c=IN IP4 (\S+)", self.invite_sdp).group(1)
                port = int(re.search(r"m=video (\d+)", self.invite_sdp).group(1))
                ssrc = re.search(r"y=(\d+)", self.invite_sdp)
                self._media_task = asyncio.create_task(self._send_media(ip, port, "TCP/RTP/AVP" in self.invite_sdp, int(ssrc.group(1)) if ssrc else 0x1234))
                self._tasks.add(self._media_task)
        elif msg.method == "BYE":
            self.bye_count += 1
            if self._media_task:
                self._media_task.cancel()
            self._ok(msg)

    def _reply(self, req, code, reason, body=b"", ctype=None):
        vias = [f"Via: {v}" for v in req.get_all("via")]
        to = req.get("to") + (";tag=dev1" if ";tag=" not in req.get("to") else "")
        lines = vias + [f"From: {req.get('from')}", f"To: {to}", f"Call-ID: {req.get('call-id')}", f"CSeq: {req.get('cseq')}"]
        self._send(self._frame(f"SIP/2.0 {code} {reason}", lines, body, ctype))

    def _ok(self, req):
        self._reply(req, 200, "OK")

    async def _send_catalog(self, sn):
        items = [(cid, name, st) for cid, name, st in self.channels] + [(f"{REALM}2150000001", "Group", "ON")]   # a 215 group node
        per = max(1, -(-len(items) // self.batches))
        for b in range(0, len(items), per):
            chunk = items[b:b + per]
            xml = ('<?xml version="1.0" encoding="GB2312"?>\r\n<Response>\r\n<CmdType>Catalog</CmdType>\r\n'
                   f'<SN>{sn}</SN>\r\n<DeviceID>{self.id}</DeviceID>\r\n<SumNum>{len(items)}</SumNum>\r\n<DeviceList Num="{len(chunk)}">\r\n')
            for cid, name, st in chunk:
                xml += f"<Item>\r\n<DeviceID>{cid}</DeviceID>\r\n<Name>{name}</Name>\r\n<Manufacturer>Fake</Manufacturer>\r\n<Status>{st}</Status>\r\n</Item>\r\n"
            xml += "</DeviceList>\r\n</Response>\r\n"
            cid = uuid.uuid4().hex + "@fake"
            h = self._headers("MESSAGE", cid)
            await self.request(self._frame(f"MESSAGE sip:{SERVER_ID}@{REALM} SIP/2.0", h, xml.encode("gb18030"), "Application/MANSCDP+xml"), cid)

    @staticmethod
    def rtp_packet(seq: int, ts: int, ssrc: int, payload: bytes, pt: int = 96) -> bytes:
        return bytes([0x80, pt]) + (seq & 0xFFFF).to_bytes(2, "big") + (ts & 0xFFFFFFFF).to_bytes(4, "big") + (ssrc & 0xFFFFFFFF).to_bytes(4, "big") + payload

    async def _send_media(self, ip, port, tcp, requested_ssrc):
        m = self.media
        ssrc = m.ssrc if m.ssrc is not None else requested_ssrc
        payloads = m.payloads()
        order = list(range(len(payloads)))
        if m.reorder:
            for i in range(1, len(order) - 1, 4):
                order[i], order[i + 1] = order[i + 1], order[i]
        writer = None
        try:
            if tcp:
                _, writer = await asyncio.open_connection(ip, port)
            for k, idx in enumerate(order):
                if idx not in m.drop:
                    pkt = self.rtp_packet(m.seq_start + idx, idx * 3600, ssrc, payloads[idx])
                    if tcp:
                        writer.write(len(pkt).to_bytes(2, "big") + pkt)
                        await writer.drain()
                    else:
                        self._rtp_sock.sendto(pkt, (ip, port))
                await asyncio.sleep(m.gap)
                if m.stall_after is not None and k + 1 >= m.stall_after:
                    await asyncio.sleep(3600)                      # silent, but the connection stays open
        except (ConnectionError, OSError):
            pass
        finally:
            if writer:
                writer.close()
