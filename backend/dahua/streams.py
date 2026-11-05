"""Live video from Dahua recorders for the AI workers. Same HTTP contract as the registrar's relay (registrar/relay.py), so a worker asks
for a single-use play link and FFmpeg reads the bytes. The bytes are the recorder's own container (DHAV), read with `-f dhav`.

A recorder serves only a few streams at once, so one stream is shared by every viewer and ends a little after the last one leaves.
A viewer who joins part-way sees a picture from the next keyframe (Dahua recorders send one about every two seconds)."""
import asyncio
import logging
import time

from registrar.relay import RelayError, Subscriber, TokenStore

log = logging.getLogger("dahua.streams")


class DahuaStream:
    def __init__(self, mgr: "StreamManager", session, key: tuple):
        self.mgr, self.session, self.key = mgr, session, key
        self.state = "starting"
        self.subscribers: set = set()
        self.holders = 0
        self.chunks = self.bytes = 0
        self.started_at: float | None = None
        self.last_data: float | None = None
        self.reason: str | None = None
        self.ended = asyncio.Event()
        self._play_id = 0
        self.preroll: list | None = []        # the SDK starts delivering before the first viewer is attached; keep that start for them
        self._watch_task = None
        self._idle_task = None

    async def start(self) -> None:
        loop, mgr = asyncio.get_running_loop(), self.mgr
        # The SDK calls this on its own thread, so hand the bytes over to the event loop.
        play_id = await asyncio.to_thread(mgr.driver.play, self.session.login_id, self.key[1] - 1, self.key[2] == "main",
                                          lambda raw: loop.call_soon_threadsafe(self._deliver, raw))
        if not play_id:
            mgr.on_play_failed(self.session)
            raise RelayError("device_refused", 502, "The recorder refused the live-video request.")
        self._play_id, self.state, self.started_at = play_id, "live", mgr.now()
        self._watch_task = asyncio.create_task(self._watch())
        log.info("stream up device=%s channel=%s stream=%s", *self.key)

    def _deliver(self, raw: bytes) -> None:
        if self.state not in ("starting", "live"):
            return
        self.last_data = self.mgr.now()
        if self.preroll is not None:
            self.preroll.append(raw)
        self.chunks += 1
        self.bytes += len(raw)
        for sub in list(self.subscribers):
            if not sub.offer(raw):
                self.subscribers.discard(sub)
                log.info("viewer disconnected: %s (device=%s)", sub.reason, self.key[0])

    def refs(self) -> int:
        return len(self.subscribers) + self.holders

    def subscribe(self) -> Subscriber:
        sub = Subscriber(self.mgr.cfg.relay_subscriber_buffer, self.mgr.now())
        if self.preroll:
            sub.offer(b"".join(self.preroll))
        self.preroll = None                                # only the first viewer needs it; later viewers join live
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)
        sub.close("done")

    async def _watch(self) -> None:
        cfg = self.mgr.cfg
        while self.state == "live":
            await asyncio.sleep(0.2)
            now = self.mgr.now()
            if self.preroll is not None and now - self.started_at > 3:      # nobody came for it: do not keep stale video
                self.preroll = None
            if now - (self.last_data if self.last_data is not None else self.started_at) > cfg.relay_stall_seconds:
                await self.stop(f"no video received for {cfg.relay_stall_seconds:g} s")
                return
            for sub in list(self.subscribers):           # a link that was handed out but never read from
                if not sub.started and now - sub.created > 30:
                    self.unsubscribe(sub)

    async def stop(self, reason: str, hang_up: bool = True) -> None:
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
        self.mgr.forget(self)
        if was_live and hang_up and self._play_id:
            try:
                await asyncio.to_thread(self.mgr.driver.stop_play, self._play_id)
            except Exception:                              # the recorder may already be gone
                log.debug("stop_play failed device=%s", self.key[0], exc_info=True)
        self.state = "stopped"
        self.ended.set()
        log.info("stream ended device=%s channel=%s stream=%s reason=%s bytes=%d", *self.key, reason, self.bytes)

    def info(self) -> dict:
        now = self.mgr.now()
        return {"device_id": self.key[0], "channel_no": self.key[1], "stream": self.key[2], "state": self.state, "viewers": len(self.subscribers),
                "holders": self.holders, "chunks": self.chunks, "bytes": self.bytes,
                "seconds_up": None if self.started_at is None else round(now - self.started_at, 1),
                "seconds_since_data": None if self.last_data is None else round(now - self.last_data, 1)}


class StreamManager:
    """Same surface as registrar.relay.StreamManager, which is what build_app talks to."""
    media_type = "video/x-dhav"

    def __init__(self, gateway, driver, cfg, clock=time.monotonic):
        self.gateway, self.driver, self.cfg, self.clock = gateway, driver, cfg, clock
        self.streams: dict = {}
        self.tokens = TokenStore(cfg.relay_token_ttl, clock)
        self._locks: dict = {}

    def now(self) -> float:
        return self.clock()

    def forget(self, st: DahuaStream) -> None:
        if self.streams.get(st.key) is st:
            del self.streams[st.key]

    def on_play_failed(self, session) -> None:
        self.gateway.session_unusable(session)

    @staticmethod
    def _key(device_id: str, channel_no: int, stream: str | None) -> tuple:
        return (device_id, channel_no, stream or "sub")

    def check(self, device_id: str, channel_no: int):
        s = self.gateway.sessions.get(device_id)
        if s is None:
            raise RelayError("device_offline", 404, "That recorder is not connected.")
        if not any(c["channel_no"] == channel_no for c in s.channels):
            raise RelayError("unknown_channel", 404, "The recorder has no such channel.")
        return s

    def _check_limits(self, device_id: str, key: tuple) -> None:
        live = {k for k, st in self.streams.items() if st.state in ("starting", "live")}
        if key in live:
            return
        if len(live) >= self.cfg.relay_max_streams:
            raise RelayError("too_many_streams", 429, "The gateway is at its limit of streams.")
        if sum(1 for k in live if k[0] == device_id) >= self.cfg.relay_max_streams_per_device:
            raise RelayError("too_many_streams", 429, "That recorder is already serving as many live streams as it may.")

    def preflight(self, device_id: str, channel_no: int, stream: str | None = None) -> None:
        self.check(device_id, channel_no)
        self._check_limits(device_id, self._key(device_id, channel_no, stream))

    async def acquire(self, device_id: str, channel_no: int, stream: str | None = None, *, subscriber: bool = False, hold: bool = False):
        s = self.check(device_id, channel_no)
        key = self._key(device_id, channel_no, stream)
        async with self._locks.setdefault(key, asyncio.Lock()):
            st = self.streams.get(key)
            if st is None or st.state not in ("starting", "live") or st.session is not s:
                self._check_limits(device_id, key)
                st = DahuaStream(self, s, key)
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

    def release(self, st: DahuaStream, sub: Subscriber | None = None, hold: bool = False) -> None:
        if sub is not None:
            st.unsubscribe(sub)
        if hold and st.holders > 0:
            st.holders -= 1
        if st.refs() == 0 and st.state == "live" and st._idle_task is None:
            st._idle_task = asyncio.create_task(self._idle_stop(st))

    async def _idle_stop(self, st: DahuaStream) -> None:
        await asyncio.sleep(self.cfg.relay_idle_grace)
        st._idle_task = None
        if st.refs() == 0 and st.state == "live":
            await st.stop("no one is watching")

    async def stop_device(self, device_id: str, reason: str) -> None:
        for st in [s for k, s in self.streams.items() if k[0] == device_id]:
            await st.stop(reason, hang_up=False)           # the recorder's connection is gone: nothing to hang up

    async def stop_all(self, reason: str) -> None:
        await asyncio.gather(*(st.stop(reason) for st in list(self.streams.values())), return_exceptions=True)
