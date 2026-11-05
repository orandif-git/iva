"""Video relay: lets an AI worker read a recorder's live video.

The recorder sends video as an MPEG program stream inside RTP packets. We ask it for a stream (INVITE), take the packets in, put them
back in order, remove the RTP wrapping and give the plain program-stream bytes to whoever asks over HTTP. FFmpeg reads those bytes
directly (`-f mpeg -i http://...`), so no media server is needed.

  worker --POST /relay/v1/streams (service token)--> relay   "I want device X, channel N"  -> a single-use play link
  worker --GET  /relay/v1/play/<token>------------> relay   the program stream, for as long as the worker keeps reading

Behaviour worth knowing:
  - One recorder stream is shared by every viewer, and a recorder only serves a few at once, so streams are capped per recorder.
  - A stream starts when the first viewer arrives and ends `relay_idle_grace` seconds after the last one leaves.
  - A viewer who joins part-way is first given the video since the last keyframe (see gop.py), so it starts at once; if the keyframes
    cannot be recognised it sees a picture from the next keyframe (recorders send one every second or two).
  - A viewer that cannot keep up is disconnected instead of being fed damaged video. The worker reconnects and picks up again.
  - Play links are single-use and expire in a minute, so the long-lived service token never appears in a process list or log."""
import asyncio
import contextlib
import hmac
import logging
import random
import secrets
import time
from collections import OrderedDict

import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import gb28181
from .gop import GopCache
from .rtp import Reorder, TcpReceiver, UdpReceiver

log = logging.getLogger("registrar.relay")


class RelayError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


class Subscriber:
    """One viewer's queue of bytes. If it falls too far behind it is closed rather than fed damaged video."""

    def __init__(self, max_bytes: int, now: float):
        self.q: asyncio.Queue = asyncio.Queue()
        self.buffered, self.max, self.created = 0, max_bytes, now
        self.closed, self.reason, self.started = False, None, False

    def offer(self, chunk: bytes) -> bool:
        if self.closed:
            return False
        if self.buffered + len(chunk) > self.max:
            self.close("the viewer could not keep up")
            return False
        self.buffered += len(chunk)
        self.q.put_nowait(chunk)
        return True

    def close(self, reason: str) -> None:
        if not self.closed:
            self.closed, self.reason = True, reason
            self.q.put_nowait(None)                      # end marker, queued behind anything still unread

    async def get(self):
        self.started = True
        item = await self.q.get()
        if item is None:
            self.q.put_nowait(None)
            return None
        self.buffered -= len(item)
        return item


class TokenStore:
    """Single-use play links. Bounded, so a flood of requests cannot grow memory."""

    def __init__(self, ttl: float, clock=time.monotonic, max_items: int = 10_000, per_key: int = 8):
        self.ttl, self.clock, self.max, self.per_key = ttl, clock, max_items, per_key
        self._d: OrderedDict = OrderedDict()

    def issue(self, key) -> str:
        now = self.clock()
        while self._d and (len(self._d) >= self.max or next(iter(self._d.values()))[0] < now):
            self._d.popitem(last=False)
        if sum(1 for exp, k in self._d.values() if k == key and exp >= now) >= self.per_key:
            raise RelayError("too_many_requests", 429, "Too many play links are waiting for this stream.")
        token = secrets.token_urlsafe(24)
        self._d[token] = (now + self.ttl, key)
        return token

    def consume(self, token: str):
        item = self._d.pop(token, None)
        return item[1] if item and item[0] >= self.clock() else None


class RelayStream:
    def __init__(self, mgr: "StreamManager", session, key: tuple, channel_id: str):
        self.mgr, self.session, self.key, self.channel_id = mgr, session, key, channel_id
        self.state = "starting"
        self.subscribers: set = set()
        self.holders = 0
        self.packets = self.bytes = self.foreign = 0
        self.ssrc = None
        self.reorder = Reorder(mgr.cfg.relay_reorder_window, clock=mgr.clock)
        self.gop = GopCache(mgr.cfg.relay_gop_cache_bytes)
        self.started_at: float | None = None
        self.last_packet: float | None = None
        self.reason: str | None = None
        self.ended = asyncio.Event()
        self._waiters: list = []
        self._receiver = None
        self._dialog = None
        self._watch_task = None
        self._idle_task = None

    # ----------------------------------------------------------------- start
    async def start(self) -> None:
        cfg, host, s = self.mgr.cfg, self.mgr.host, self.session
        tcp = cfg.relay_media_transport == "tcp"
        try:
            self._receiver = (TcpReceiver(s.peer.addr[0], self._on_rtp, self._on_tcp_closed) if tcp else UdpReceiver(s.peer.addr[0], self._on_rtp))
            port = await self._receiver.start(cfg.sip_bind, cfg.rtp_port_min, cfg.rtp_port_max)
        except OSError:
            self._receiver = None
            raise RelayError("no_media_port", 503, "No free media port on the relay.")
        ssrc = f"0{cfg.server_id[3:8]}{random.randrange(10000):04d}"
        sdp = gb28181.play_sdp(cfg.server_id, cfg.public_ip, port, ssrc, "tcp" if tcp else "udp")
        try:
            resp, dlg = await host._request(s, "INVITE", self.channel_id, sdp, "Application/SDP",
                                            [("subject", f"{self.channel_id}:{ssrc},{cfg.server_id}:0")])
        except BaseException:
            self._close_media()
            raise
        if resp is None:
            self._close_media()
            raise RelayError("device_timeout", 504, "The recorder did not answer the live-video request.")
        if resp.status != 200:
            self._close_media()
            raise RelayError("device_refused", 502, f"The recorder refused the live-video request (status {resp.status}).")
        host._send_in_dialog(s, "ACK", self.channel_id, dlg, 0)
        self._dialog, self.state, self.started_at = dlg, "live", self.mgr.now()
        self._watch_task = asyncio.create_task(self._watch())
        log.info("stream up device=%s channel=%s transport=%s", self.key[0], self.key[1], "tcp" if tcp else "udp")

    # ----------------------------------------------------------------- media in
    def _on_rtp(self, pkt) -> None:
        if self.state != "live":
            return
        if self.ssrc is None:
            self.ssrc = pkt.ssrc                       # lock onto the first source: anything else is not our stream
        elif pkt.ssrc != self.ssrc:
            self.foreign += 1
            return
        self.last_packet = self.mgr.now()
        for p in self.reorder.push(pkt):
            self._deliver(p)

    def _deliver(self, pkt) -> None:
        if not pkt.payload:
            return
        self.packets += 1
        self.bytes += len(pkt.payload)
        self.gop.feed(pkt.payload)
        for n, fut in list(self._waiters):
            if self.packets >= n and not fut.done():
                fut.set_result(True)
        for sub in list(self.subscribers):
            if not sub.offer(pkt.payload):
                self.subscribers.discard(sub)
                log.info("viewer disconnected: %s (device=%s)", sub.reason, self.key[0])

    def _on_tcp_closed(self) -> None:
        if self.state == "live":
            self.mgr.spawn(self.stop("the recorder closed the media connection"))

    async def wait_packets(self, n: int, timeout: float) -> bool:
        """True once `n` packets of video have arrived, False on timeout or if the stream ends first."""
        if self.packets >= n:
            return True
        fut = asyncio.get_running_loop().create_future()
        entry = (n, fut)
        self._waiters.append(entry)
        try:
            return bool(await asyncio.wait_for(fut, timeout))
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.remove(entry)

    # ------------------------------------------------------------------ viewers
    def refs(self) -> int:
        return len(self.subscribers) + self.holders

    def subscribe(self) -> Subscriber:
        sub = Subscriber(self.mgr.cfg.relay_subscriber_buffer, self.mgr.now())
        replay = self.gop.snapshot()
        if replay and len(replay) <= sub.max // 2:          # start at the last keyframe, then continue with the live bytes
            sub.offer(replay)
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)
        sub.close("done")

    # --------------------------------------------------------------- watchdog
    async def _watch(self) -> None:
        cfg = self.mgr.cfg
        while self.state == "live":
            await asyncio.sleep(0.1)
            for p in self.reorder.flush_expired():
                self._deliver(p)
            now = self.mgr.now()
            if now - (self.last_packet if self.last_packet is not None else self.started_at) > cfg.relay_stall_seconds:
                await self.stop(f"no video received for {cfg.relay_stall_seconds:g} s")
                return
            for sub in list(self.subscribers):           # a link that was handed out but never read from
                if not sub.started and now - sub.created > 30:
                    self.unsubscribe(sub)

    # -------------------------------------------------------------------- stop
    def _close_media(self) -> None:
        if self._receiver:
            self._receiver.close()
            self._receiver = None

    async def stop(self, reason: str, send_bye: bool = True) -> None:
        if self.state in ("stopping", "stopped"):
            return
        was_live = self.state == "live"
        self.state, self.reason = "stopping", reason
        for t in (self._watch_task, self._idle_task):
            if t is not None and t is not asyncio.current_task():
                t.cancel()
        for sub in list(self.subscribers):
            sub.close(reason)
        self.subscribers.clear()
        for _, fut in self._waiters:
            if not fut.done():
                fut.set_result(False)
        self.mgr.forget(self)
        self._close_media()
        if was_live and send_bye and self._dialog and self.mgr.host.sessions.get(self.key[0]) is self.session:
            try:
                await self.mgr.host._bye(self.session, self.channel_id, self._dialog)
            except Exception:                             # the recorder may already be gone
                log.debug("BYE failed device=%s", self.key[0], exc_info=True)
        self.state = "stopped"
        self.ended.set()
        log.info("stream ended device=%s channel=%s reason=%s packets=%d lost=%d", self.key[0], self.key[1], reason, self.packets, self.reorder.lost)

    def info(self) -> dict:
        now = self.mgr.now()
        return {"device_id": self.key[0], "channel_no": self.key[1], "state": self.state, "viewers": len(self.subscribers), "holders": self.holders,
                "packets": self.packets, "bytes": self.bytes, "lost": self.reorder.lost, "duplicates": self.reorder.duplicates, "foreign_source": self.foreign,
                "seconds_up": None if self.started_at is None else round(now - self.started_at, 1),
                "seconds_since_packet": None if self.last_packet is None else round(now - self.last_packet, 1)}


class StreamManager:
    def __init__(self, host, cfg, clock=time.monotonic):
        self.host, self.cfg, self.clock = host, cfg, clock
        self.streams: dict = {}
        self.tokens = TokenStore(cfg.relay_token_ttl, clock)
        self._locks: dict = {}
        self._tasks: set = set()

    def now(self) -> float:
        return self.clock()

    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def forget(self, st: RelayStream) -> None:
        if self.streams.get(st.key) is st:
            del self.streams[st.key]

    # ----------------------------------------------------------------- checks
    def check(self, device_id: str, channel_no: int):
        s = self.host.sessions.get(device_id)
        if s is None:
            raise RelayError("device_offline", 404, "That recorder is not connected.")
        ch = next((c for c in s.channels if c["channel_no"] == channel_no), None)
        if ch is None:
            raise RelayError("unknown_channel", 404, "The recorder has no such channel.")
        if not ch["online"]:
            raise RelayError("channel_offline", 404, "The recorder reports that channel as off.")
        return s, ch

    def _check_limits(self, device_id: str, key: tuple) -> None:
        live = {k for k, st in self.streams.items() if st.state in ("starting", "live")}
        if key in live:
            return
        if len(live) >= self.cfg.relay_max_streams:
            raise RelayError("too_many_streams", 429, "The relay is at its limit of streams.")
        if sum(1 for k in live if k[0] == device_id) >= self.cfg.relay_max_streams_per_device:
            raise RelayError("too_many_streams", 429, "That recorder is already serving as many live streams as it may.")

    def preflight(self, device_id: str, channel_no: int) -> None:
        self.check(device_id, channel_no)
        self._check_limits(device_id, (device_id, channel_no))

    # --------------------------------------------------------------- acquiring
    async def acquire(self, device_id: str, channel_no: int, *, subscriber: bool = False, hold: bool = False):
        """Returns (stream, subscriber or None). Starts the stream if it is not running."""
        s, ch = self.check(device_id, channel_no)
        key = (device_id, channel_no)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            st = self.streams.get(key)
            if st is None or st.state not in ("starting", "live") or st.session is not s:
                self._check_limits(device_id, key)
                st = RelayStream(self, s, key, ch["external_id"])
                self.streams[key] = st
                try:
                    await st.start()
                except BaseException:
                    self.forget(st)
                    raise
            if st._idle_task is not None:
                st._idle_task.cancel()
                st._idle_task = None
            if hold:
                st.holders += 1
            sub = st.subscribe() if subscriber else None
        return st, sub

    def release(self, st: RelayStream, sub: Subscriber | None = None, hold: bool = False) -> None:
        if sub is not None:
            st.unsubscribe(sub)
        if hold and st.holders > 0:
            st.holders -= 1
        if st.refs() == 0 and st.state == "live" and st._idle_task is None:
            st._idle_task = asyncio.create_task(self._idle_stop(st))

    async def _idle_stop(self, st: RelayStream) -> None:
        await asyncio.sleep(self.cfg.relay_idle_grace)
        st._idle_task = None
        if st.refs() == 0 and st.state == "live":
            await st.stop("no one is watching")

    async def stop_device(self, device_id: str, reason: str) -> None:
        for st in [s for k, s in self.streams.items() if k[0] == device_id]:
            await st.stop(reason, send_bye=False)          # the recorder is gone: there is no one to say goodbye to

    async def stop_all(self, reason: str) -> None:
        await asyncio.gather(*(st.stop(reason) for st in list(self.streams.values())), return_exceptions=True)
        for t in list(self._tasks):
            t.cancel()


# ------------------------------------------------------------------------------------ HTTP
def _error(e: RelayError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status)


def build_app(mgr: StreamManager, service_token: str, base_url) -> Starlette:
    def authorised(request) -> bool:
        h = request.headers.get("authorization", "")
        return h.lower().startswith("bearer ") and hmac.compare_digest(h[7:].strip().encode(), service_token.encode())

    async def create(request):
        if not authorised(request):
            return _error(RelayError("invalid_token", 401, "Not allowed."))
        try:
            body = await request.json()
            device_id, channel_no = body["device_id"], body["channel_no"]
            if not (isinstance(device_id, str) and 0 < len(device_id) <= 64 and isinstance(channel_no, int) and not isinstance(channel_no, bool) and 1 <= channel_no <= 256):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return _error(RelayError("validation_error", 422, "Send device_id (text) and channel_no (1 to 256)."))
        try:
            mgr.preflight(device_id, channel_no)
            token = mgr.tokens.issue((device_id, channel_no))
        except RelayError as e:
            return _error(e)
        return JSONResponse({"url": f"{base_url()}/relay/v1/play/{token}", "expires_in": mgr.cfg.relay_token_ttl}, status_code=201)

    async def play(request):
        key = mgr.tokens.consume(request.path_params["token"])
        if key is None:
            return _error(RelayError("link_invalid", 404, "This play link is not valid, or has been used or expired."))
        try:
            st, sub = await mgr.acquire(key[0], key[1], subscriber=True)
        except RelayError as e:
            return _error(e)

        async def body():
            try:
                while (chunk := await sub.get()) is not None:
                    yield chunk
            finally:
                mgr.release(st, sub)                          # safe to repeat; also runs if the viewer disconnects

        return StreamingResponse(body(), media_type="video/mp2p", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                                 background=BackgroundTask(mgr.release, st, sub))

    async def listing(request):
        if not authorised(request):
            return _error(RelayError("invalid_token", 401, "Not allowed."))
        return JSONResponse({"streams": [st.info() for st in mgr.streams.values()]})

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/relay/v1/streams", create, methods=["POST"]), Route("/relay/v1/streams", listing, methods=["GET"]),
                             Route("/relay/v1/play/{token}", play, methods=["GET"]), Route("/healthz", health, methods=["GET"])])


class QuietServer(uvicorn.Server):
    """uvicorn without its own signal handling: the registrar decides how to shut down."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield
