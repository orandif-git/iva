"""GB28181 registrar: accepts SIP registrations from recorders, keeps them alive, learns their channels, and reports
everything to the backend. Recorders connect OUT to this server, so customers need no static IP, VPN or router changes.

Per recorder: REGISTER with Digest auth (password = the key shown to the customer) -> we query the channel catalogue ->
we report heartbeats to the backend -> we confirm real video flows by asking for a short live stream and counting RTP packets."""
import asyncio
import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import uvicorn

from . import gb28181, sip
from .backend_client import BackendClient, BackendUnavailable
from .config import RegistrarSettings
from .relay import QuietServer, RelayError, StreamManager, build_app

log = logging.getLogger("registrar")
ALLOW = "REGISTER, MESSAGE, BYE, OPTIONS, ACK"


@dataclass(eq=False)
class Peer:
    kind: str                 # "udp" or "tcp"
    addr: tuple
    send: object              # callable(bytes)


@dataclass(eq=False)
class Session:
    device_id: str
    peer: Peer
    expires_at: float
    last_keepalive: float
    channels: list = field(default_factory=list)
    firmware: str | None = None
    up: bool = False                         # the backend has accepted at least one heartbeat
    last_hb: float = 0.0
    last_catalog_req: float = -1e9
    catalog_inflight: bool = False
    catalog_acc: dict = field(default_factory=dict)
    catalog_event: asyncio.Event = field(default_factory=asyncio.Event)
    preview_ok: bool = False
    probe_attempts: int = 0
    probe_running: bool = False
    last_probe: float = -1e9


@dataclass
class Dialog:
    call_id: str
    local_from: str
    remote_to: str
    uri: str
    cseq: int


class Registrar:
    def __init__(self, cfg: RegistrarSettings, backend: BackendClient, clock=time.monotonic):
        self.cfg, self.backend, self.now = cfg, backend, clock
        self.sessions: dict[str, Session] = {}
        self.nonces = sip.NonceStore(clock=clock)
        self.limiter = sip.FailureLimiter(clock=clock)
        self.pending: dict[str, tuple] = {}          # call-id -> (future, expected source ip)
        self._cseq = 0
        self._sn = 0
        self._tasks: set = set()
        self._udp = self._tcp = self._reaper = None
        self.udp_port = self.tcp_port = 0
        self.relay = StreamManager(self, cfg)               # real time on purpose: media timing does not follow the test clock
        self._http = self._http_task = None
        self.relay_port, self.relay_base = 0, ""

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._udp, _ = await loop.create_datagram_endpoint(lambda: _UdpProtocol(self), local_addr=(self.cfg.sip_bind, self.cfg.sip_port))
        self.udp_port = self._udp.get_extra_info("sockname")[1]
        self._tcp = await asyncio.start_server(self._tcp_client, self.cfg.sip_bind, self.cfg.sip_port)
        self.tcp_port = self._tcp.sockets[0].getsockname()[1]
        self._reaper = asyncio.create_task(self._reap_loop())
        await self._start_relay_http()
        log.info("registrar listening udp=%s tcp=%s realm=%s", self.udp_port, self.tcp_port, self.cfg.realm)

    async def stop(self) -> None:
        # Sessions are not reported offline here: a restart should not flap every recorder. The backend's sweeper
        # marks anything that does not come back within its timeout as offline.
        if self._reaper:
            self._reaper.cancel()
        await self.relay.stop_all("the registrar is stopping")
        if self._http:
            self._http.should_exit = self._http.force_exit = True
            try:
                await asyncio.wait_for(self._http_task, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        for t in list(self._tasks):
            t.cancel()
        if self._udp:
            self._udp.close()
        if self._tcp:
            self._tcp.close()
            await self._tcp.wait_closed()

    async def _start_relay_http(self) -> None:
        c = self.cfg
        if c.relay_http_port < 0:
            return
        app = build_app(self.relay, c.service_token, lambda: self.relay_base)
        self._http = QuietServer(uvicorn.Config(app, host=c.relay_http_bind, port=c.relay_http_port, log_level="warning", access_log=False, lifespan="off"))
        self._http_task = asyncio.create_task(self._http.serve())
        for _ in range(250):
            if self._http.started:
                break
            if self._http_task.done():
                self._http_task.result()
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("the relay's HTTP server did not start")
        self.relay_port = self._http.servers[0].sockets[0].getsockname()[1]
        host = c.public_ip if c.relay_http_bind in ("0.0.0.0", "::") else c.relay_http_bind
        self.relay_base = c.relay_public_base.rstrip("/") or f"http://{host}:{self.relay_port}"
        log.info("video relay listening on %s", self.relay_base)

    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)

        def _done(task):
            self._tasks.discard(task)
            if not task.cancelled() and task.exception():
                log.error("task failed", exc_info=task.exception())
        t.add_done_callback(_done)

    # ------------------------------------------------------------------ transports
    async def _tcp_client(self, reader, writer) -> None:
        addr = tuple(writer.get_extra_info("peername")[:2])
        peer = Peer("tcp", addr, lambda b: None if writer.is_closing() else writer.write(b))
        buf = bytearray()
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), timeout=self.cfg.keepalive_timeout * 2)
                if not data:
                    break
                buf += data
                while True:
                    frame = sip.extract_frame(buf)
                    if frame is None:
                        break
                    self.spawn(self.handle_raw(frame, peer))
        except (sip.SipParseError, ConnectionError, asyncio.TimeoutError, OSError):
            pass
        finally:
            writer.close()
            for s in list(self.sessions.values()):
                if s.peer is peer:                      # the connection that carried this recorder is gone
                    await self._drop(s, "connection closed")

    # --------------------------------------------------------------- dispatching
    async def handle_raw(self, data: bytes, peer: Peer) -> None:
        try:
            msg = sip.parse(data)
        except sip.SipParseError as e:
            log.debug("dropped malformed message from %s: %s", peer.addr[0], e)
            return
        if not msg.is_request:
            self._on_response(msg, peer)
            return
        if self.limiter.blocked(peer.addr[0]):
            return                                       # too many failed logins: say nothing
        m = msg.method
        if m == "REGISTER":
            await self._on_register(msg, peer)
        elif m == "MESSAGE":
            await self._on_message(msg, peer)
        elif m == "OPTIONS":
            self._reply(msg, peer, 200, "OK", [("allow", ALLOW)])
        elif m == "BYE":
            self._reply(msg, peer, 200, "OK")
        elif m == "ACK":
            return
        else:
            self._reply(msg, peer, 405, "Method Not Allowed", [("allow", ALLOW)])

    def _reply(self, req, peer, code, reason, extra=None, body=b"", ctype=None) -> None:
        peer.send(sip.make_response(req, code, reason, peer.addr, extra, body, ctype).to_bytes())

    # ----------------------------------------------------------------- REGISTER
    async def _on_register(self, msg: sip.SipMessage, peer: Peer) -> None:
        ip = peer.addr[0]
        user = sip.uri_user(msg.get("from"))
        auth = sip.parse_digest(msg.get("authorization"))
        if not user or not auth:
            self._challenge(msg, peer)
            return
        if not self.nonces.consume(auth.get("nonce", ""), ip):
            self._challenge(msg, peer, stale=True)       # unknown, reused, expired, or issued to another address
            return
        qop, nc, cnonce = auth.get("qop"), auth.get("nc"), auth.get("cnonce")
        if (auth.get("username") != user or auth.get("realm") != self.cfg.realm or auth.get("uri") != msg.uri
                or qop not in (None, "auth") or (qop and not (nc and cnonce))):
            self.limiter.fail(ip)
            self._reply(msg, peer, 403, "Forbidden")
            return
        try:
            creds = await self.backend.credentials(user)
        except BackendUnavailable:
            self._reply(msg, peer, 503, "Service Unavailable", [("retry-after", "30")])
            return
        expected = sip.digest_response(user, self.cfg.realm, creds.key, "REGISTER", msg.uri, auth["nonce"], qop, nc, cnonce) if creds else ""
        ok = bool(creds) and creds.protocol == "gb28181" and hmac.compare_digest(expected, (auth.get("response") or "").lower())
        if not ok:                                       # unknown device, suspended tenant and wrong key all look the same
            self.limiter.fail(ip)
            log.info("registration refused device=%s ip=%s", user, ip)
            self._reply(msg, peer, 403, "Forbidden")
            return

        expires = self._expires(msg)
        existing = self.sessions.get(user)
        if expires == 0:
            if existing:
                await self._drop(existing, "unregistered")
            self._reply(msg, peer, 200, "OK", [("expires", "0"), ("date", self._date())])
            return
        now = self.now()
        is_new = existing is None or existing.peer.addr[0] != ip
        if is_new:
            s = Session(user, peer, now + expires, now)
            self.sessions[user] = s
            log.info("recorder registered device=%s ip=%s transport=%s", user, ip, peer.kind)
        else:
            s = existing
            s.peer, s.expires_at, s.last_keepalive = peer, now + expires, now
        extra = [("expires", str(expires)), ("date", self._date())]
        if msg.get("contact"):
            extra.append(("contact", msg.get("contact")))
        self._reply(msg, peer, 200, "OK", extra)
        if is_new:
            self.spawn(self._bring_up(s))

    def _challenge(self, msg, peer, stale: bool = False) -> None:
        nonce = self.nonces.issue(peer.addr[0])
        hdr = f'Digest realm="{self.cfg.realm}", qop="auth", nonce="{nonce}", algorithm=MD5' + (", stale=true" if stale else "")
        self._reply(msg, peer, 401, "Unauthorized", [("www-authenticate", hdr)])

    def _expires(self, msg) -> int:
        raw = msg.get("expires")
        if raw is None:
            import re
            m = re.search(r"expires=(\d+)", msg.get("contact") or "")
            raw = m.group(1) if m else "3600"
        try:
            return max(0, min(int(raw), self.cfg.max_register_expires))
        except ValueError:
            return 3600

    def _date(self) -> str:
        tz = timezone(timedelta(minutes=self.cfg.device_utc_offset_minutes))
        return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S.000")

    # ------------------------------------------------------------------ MESSAGE
    async def _on_message(self, msg: sip.SipMessage, peer: Peer) -> None:
        user = sip.uri_user(msg.get("from"))
        s = self.sessions.get(user or "")
        if not s or s.peer.addr[0] != peer.addr[0]:       # only registered devices, and only from the address that registered
            self._reply(msg, peer, 403, "Forbidden")
            return
        s.peer = peer if peer.kind == s.peer.kind else s.peer
        try:
            data = gb28181.parse_manscdp(msg.body)
        except gb28181.Gb28181Error as e:
            log.debug("bad MANSCDP from %s: %s", user, e)
            self._reply(msg, peer, 400, "Bad Request")
            return
        self._reply(msg, peer, 200, "OK")
        cmd, root = data["cmd"], data["root"]
        if root == "Notify" and cmd == "Keepalive":
            s.last_keepalive = self.now()
        elif root == "Response" and cmd == "Catalog":
            self._on_catalog(s, data)
        elif root == "Response" and cmd == "DeviceInfo":
            s.firmware = (data["fields"].get("FirmWare") or data["fields"].get("Firmware") or "")[:80] or s.firmware

    def _on_catalog(self, s: Session, data: dict) -> None:
        if len(s.catalog_acc) > 8:
            s.catalog_acc.clear()
        acc = s.catalog_acc.setdefault(data["sn"], {"sum": data["sum_num"], "items": {}})
        for it in data["items"]:
            if it.get("DeviceID") and len(acc["items"]) < 1024:
                acc["items"][it["DeviceID"]] = it
        if acc["sum"] is None or len(acc["items"]) >= acc["sum"]:
            s.channels = gb28181.select_channels(list(acc["items"].values()))
            s.catalog_acc.clear()
            s.catalog_event.set()
            self.spawn(self._after_catalog(s))

    async def _after_catalog(self, s: Session) -> None:
        await self._heartbeat(s)
        await self._maybe_probe(s)

    # ------------------------------------------------------- server-initiated requests
    def _transport_port(self, peer: Peer) -> tuple[str, int]:
        return ("UDP", self.udp_port) if peer.kind == "udp" else ("TCP", self.tcp_port)

    def _build(self, s: Session, method: str, to_user: str, call_id: str, cseq: int, from_hdr: str, to_hdr: str,
               extra=None, body=b"", ctype=None) -> sip.SipMessage:
        proto, port = self._transport_port(s.peer)
        host, uri = self.cfg.public_ip, f"sip:{to_user}@{s.peer.addr[0]}:{s.peer.addr[1]}"
        h = [("via", f"SIP/2.0/{proto} {host}:{port};rport;branch=z9hG4bK{sip.new_token(5)}"), ("max-forwards", "70"),
             ("from", from_hdr), ("to", to_hdr), ("call-id", call_id), ("cseq", f"{cseq} {method}"),
             ("contact", f"<sip:{self.cfg.server_id}@{host}:{port}>"), ("user-agent", "GateInsight-Registrar")] + (extra or [])
        if ctype:
            h.append(("content-type", ctype))
        return sip.SipMessage(f"{method} {uri} SIP/2.0", h, body)

    async def _request(self, s: Session, method: str, to_user: str, body=b"", ctype=None, extra=None, timeout_steps=(0.5, 1.0, 2.0, 4.0)):
        self._cseq += 1
        call_id = f"{sip.new_token(8)}@{self.cfg.public_ip}"
        frm = f"<sip:{self.cfg.server_id}@{self.cfg.realm}>;tag={sip.new_token(4)}"
        to = f"<sip:{to_user}@{self.cfg.realm}>"
        req = self._build(s, method, to_user, call_id, self._cseq, frm, to, extra, body, ctype)
        resp = await self._send_wait(s.peer, req, call_id)
        dlg = Dialog(call_id, frm, (resp.get("to") if resp else to) or to, req.uri, self._cseq)
        return resp, dlg

    async def _send_wait(self, peer: Peer, req: sip.SipMessage, call_id: str, steps=(0.5, 1.0, 2.0, 4.0)):
        fut = asyncio.get_running_loop().create_future()
        self.pending[call_id] = (fut, peer.addr[0])
        data = req.to_bytes()
        try:
            peer.send(data)
            for i, step in enumerate(steps if peer.kind == "udp" else (sum(steps),)):
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), step)
                except asyncio.TimeoutError:
                    if peer.kind == "udp" and i < len(steps) - 1:
                        peer.send(data)                    # UDP can lose packets: retransmit
            return None
        finally:
            self.pending.pop(call_id, None)

    def _on_response(self, msg: sip.SipMessage, peer: Peer) -> None:
        entry = self.pending.get(msg.get("call-id") or "")
        if not entry or msg.status < 200:                   # provisional answers (100, 180) change nothing
            return
        fut, ip = entry
        if peer.addr[0] == ip and not fut.done():           # ignore answers from any other address
            fut.set_result(msg)

    # ---------------------------------------------------------------- session work
    async def _bring_up(self, s: Session) -> None:
        await self._query_catalog(s)
        if not s.up and self.sessions.get(s.device_id) is s:
            await self._heartbeat(s)                        # recorder is online even if it reported no channels yet

    async def _query_catalog(self, s: Session) -> None:
        if s.catalog_inflight:
            return
        s.catalog_inflight = True
        try:
            s.last_catalog_req = self.now()
            s.catalog_event.clear()
            self._sn += 1
            resp, _ = await self._request(s, "MESSAGE", s.device_id, gb28181.catalog_query(self._sn, s.device_id), "Application/MANSCDP+xml")
            if resp is None or resp.status >= 300:
                log.info("catalog query unanswered device=%s", s.device_id)
                return
            try:
                await asyncio.wait_for(s.catalog_event.wait(), self.cfg.catalog_timeout)
            except asyncio.TimeoutError:
                log.info("catalog not received device=%s", s.device_id)
        finally:
            s.catalog_inflight = False

    async def _heartbeat(self, s: Session) -> None:
        if self.sessions.get(s.device_id) is not s:
            return
        chans = [{"channel_no": c["channel_no"], "name": c["name"], "external_id": c["external_id"]} for c in s.channels]
        try:
            allowed = await self.backend.heartbeat(s.device_id, s.peer.addr[0], chans, s.firmware)
        except BackendUnavailable as e:
            log.warning("heartbeat failed device=%s: %s", s.device_id, e)
            return
        s.last_hb = self.now()
        if not allowed:
            await self._drop(s, "refused by backend", notify=False)
            return
        s.up = True

    async def _maybe_probe(self, s: Session) -> None:
        """Proves video really flows, by watching the first few packets of a relay stream. The same stream is then kept for a short grace
        period, so a worker that arrives straight afterwards does not make the recorder start a second one."""
        if s.preview_ok or s.probe_running or s.probe_attempts >= self.cfg.probe_retries or self.sessions.get(s.device_id) is not s:
            return
        ch = next((c for c in s.channels if c["online"]), None)
        if ch is None:
            return
        s.probe_running = True
        s.probe_attempts += 1
        s.last_probe = self.now()
        stream = None
        try:
            stream, _ = await self.relay.acquire(s.device_id, ch["channel_no"], hold=True)
            ok = await stream.wait_packets(self.cfg.probe_packets, self.cfg.probe_timeout)
            if ok and self.sessions.get(s.device_id) is s:
                s.preview_ok = True
                try:
                    await self.backend.preview_ok(s.device_id)
                except BackendUnavailable:
                    s.preview_ok = False                    # tell the backend next time
                log.info("video confirmed device=%s", s.device_id)
            else:
                log.info("no video received device=%s", s.device_id)
        except RelayError as e:
            log.info("live request failed device=%s: %s", s.device_id, e.message)
        finally:
            if stream is not None:
                self.relay.release(stream, hold=True)
            s.probe_running = False

    def _send_in_dialog(self, s, method, to_user, dlg: Dialog, cseq_bump: int):
        req = self._build(s, method, to_user, dlg.call_id, dlg.cseq + cseq_bump, dlg.local_from, dlg.remote_to)
        req.start = f"{method} {dlg.uri} SIP/2.0"
        s.peer.send(req.to_bytes())
        return req

    async def _bye(self, s, to_user, dlg: Dialog) -> None:
        self._cseq += 1
        req = self._build(s, "BYE", to_user, dlg.call_id, dlg.cseq + 1, dlg.local_from, dlg.remote_to)
        req.start = f"BYE {dlg.uri} SIP/2.0"
        await self._send_wait(s.peer, req, dlg.call_id, steps=(0.5, 1.0))

    async def _drop(self, s: Session, reason: str, notify: bool = True) -> None:
        if self.sessions.get(s.device_id) is not s:
            return
        del self.sessions[s.device_id]
        log.info("recorder offline device=%s reason=%s", s.device_id, reason)
        await self.relay.stop_device(s.device_id, "the recorder went offline")
        if notify:
            try:
                await self.backend.offline(s.device_id)
            except BackendUnavailable:
                log.warning("could not report offline device=%s (the backend sweeper will)", s.device_id)

    # ------------------------------------------------------------------ maintenance
    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.reap_interval)
            try:
                await self.reap_once()
            except Exception:
                log.exception("maintenance failed")

    async def reap_once(self) -> None:
        now = self.now()
        for s in list(self.sessions.values()):
            if now >= s.expires_at:
                await self._drop(s, "registration expired")
            elif now - s.last_keepalive > self.cfg.keepalive_timeout:
                await self._drop(s, "keepalive timeout")
            else:
                if s.up and now - s.last_hb >= self.cfg.heartbeat_interval:
                    self.spawn(self._heartbeat(s))
                if not s.channels and not s.catalog_inflight and now - s.last_catalog_req >= 30:
                    self.spawn(self._query_catalog(s))
                if s.channels and not s.preview_ok and now - s.last_probe >= 60:
                    self.spawn(self._maybe_probe(s))


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, reg: Registrar):
        self.reg = reg
        self.t = None

    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        a = tuple(addr[:2])
        self.reg.spawn(self.reg.handle_raw(data, Peer("udp", a, lambda b: self.t.sendto(b, a))))
