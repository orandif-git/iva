"""Dahua gateway: accepts recorders that register with us ("Auto Register"), signs in to them, and keeps the backend told what is online.

How a recorder is recognised
  The customer types three things on the recorder: our address and port, the Sub-Device ID (our recorder's device id) and a viewer-only user
  whose password is the recorder's key. The Sub-Device ID only says WHICH recorder this claims to be. It is not a secret, so it proves
  nothing: the recorder counts as ours only if signing in with the stored key succeeds. A wrong key never gets in.

Protecting the recorder
  A recorder locks the viewer account after a few wrong passwords, and an unconfigured one re-registers every few seconds. After a refused
  sign-in we therefore leave that recorder alone for a while, longer each time."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from registrar.backend_client import BackendClient, BackendUnavailable

from .driver import Driver

log = logging.getLogger("dahua.gateway")
_IDENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_CHANNELS = 256


@dataclass(eq=False)
class Session:
    device_id: str
    ip: str
    port: int
    login_id: int
    serial: str
    channels: list
    connected_at: float = field(default_factory=time.monotonic)
    preview_reported: bool = False


class Gateway:
    def __init__(self, cfg, backend: BackendClient, driver: Driver, clock=time.monotonic):
        self.cfg, self.backend, self.driver, self.clock = cfg, backend, driver, clock
        self.sessions: dict[str, Session] = {}
        self._pending: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set = set()
        self._backoff: dict[str, tuple[float, float]] = {}      # ident -> (ignore until, last wait)
        self._login_slots = asyncio.Semaphore(8)
        self.streams = None                                      # set by whoever builds the StreamManager
        self.dropped = 0

    # ------------------------------------------------------------------ start / stop
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._pending = asyncio.Queue(self.cfg.max_pending_registrations)
        if not self.driver.start(self.cfg.listen_bind, self.cfg.listen_port, self._on_register, self._on_disconnect):
            raise RuntimeError(f"could not listen on port {self.cfg.listen_port} (is another program using it?)")
        self._spawn(self._intake())
        self._spawn(self._heartbeats())
        log.info("dahua gateway listening on %s:%s", self.cfg.listen_bind, self.cfg.listen_port)

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self.streams:
            await self.streams.stop_all("the gateway is stopping")
        for s in list(self.sessions.values()):
            await asyncio.to_thread(self._safe, self.driver.logout, s.login_id)
        self.sessions.clear()
        await asyncio.to_thread(self._safe, self.driver.stop)

    @staticmethod
    def _safe(fn, *a) -> None:
        try:
            fn(*a)
        except Exception:
            log.debug("driver call failed", exc_info=True)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------- SDK threads -> event loop
    def _on_register(self, ip: str, port: int, ident: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._enqueue, ip, port, ident)

    def _enqueue(self, ip: str, port: int, ident: str) -> None:
        try:
            self._pending.put_nowait((ip, port, ident))
        except asyncio.QueueFull:
            self.dropped += 1                                   # a flood: the recorders that matter will register again

    def _on_disconnect(self, login_id: int) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: self._spawn(self._lost(login_id)))

    # -------------------------------------------------------------------- registrations
    async def _intake(self) -> None:
        while True:
            ip, port, ident = await self._pending.get()
            self._spawn(self._handle(ip, port, ident))

    def _blocked(self, ident: str) -> bool:
        until, _ = self._backoff.get(ident, (0.0, 0.0))
        return self.clock() < until

    def _penalise(self, ident: str) -> None:
        _, last = self._backoff.get(ident, (0.0, 0.0))
        wait = min(self.cfg.login_backoff_max, max(self.cfg.login_backoff_min, last * 2))
        if len(self._backoff) > 5000:                           # ids come from the network: bound the table
            self._backoff.clear()
        self._backoff[ident] = (self.clock() + wait, wait)

    async def _handle(self, ip: str, port: int, ident: str) -> None:
        if not _IDENT.match(ident) or self._blocked(ident):
            return
        async with self._login_slots:
            try:
                creds = await self.backend.credentials(ident)
            except BackendUnavailable:
                return                                          # the recorder registers again shortly
            if creds is None or creds.protocol != "auto_register":
                log.info("registration from %s with an id we do not serve: %r", ip, ident[:32])
                self._penalise(ident)
                return
            try:
                res = await asyncio.wait_for(asyncio.to_thread(self.driver.login, ip, port, ident, self.cfg.viewer_user, creds.key), self.cfg.login_timeout)
            except asyncio.TimeoutError:
                log.warning("sign-in to %s timed out", ident)
                self._penalise(ident)
                return
            except Exception:
                log.exception("sign-in to %s failed", ident)
                self._penalise(ident)
                return
        if not res.login_id:
            log.warning("sign-in to %s from %s refused: %s", ident, ip, res.error)     # never log the key
            self._penalise(ident)
            return
        self._backoff.pop(ident, None)
        n = max(0, min(res.channels, MAX_CHANNELS))
        session = Session(ident, ip, port, res.login_id, res.serial,
                          [{"channel_no": i + 1, "name": f"Channel {i + 1}", "external_id": str(i), "online": True} for i in range(n)])
        old = self.sessions.get(ident)
        self.sessions[ident] = session
        if old is not None:                                     # the recorder came back on a new connection
            if self.streams:
                await self.streams.stop_device(ident, "the recorder reconnected")
            await asyncio.to_thread(self._safe, self.driver.logout, old.login_id)
        log.info("recorder %s online from %s with %d channels (serial %s)", ident, ip, n, res.serial or "?")
        await self._heartbeat(session)

    # ------------------------------------------------------------------ status to backend
    async def _heartbeat(self, s: Session) -> None:
        chans = [{"channel_no": c["channel_no"], "name": c["name"], "external_id": c["external_id"]} for c in s.channels]
        try:
            allowed = await self.backend.heartbeat(s.device_id, s.ip, chans, None)
        except BackendUnavailable:
            return
        if not allowed:                                         # suspended customer, or the recorder was deleted
            await self._drop(s, "not allowed by the backend")

    async def _heartbeats(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.heartbeat_interval)
            for s in list(self.sessions.values()):
                if self.sessions.get(s.device_id) is s:
                    await self._heartbeat(s)

    async def _drop(self, s: Session, reason: str, tell_backend: bool = False) -> None:
        if self.sessions.get(s.device_id) is not s:
            return
        del self.sessions[s.device_id]
        log.info("recorder %s dropped: %s", s.device_id, reason)
        if self.streams:
            await self.streams.stop_device(s.device_id, reason)
        await asyncio.to_thread(self._safe, self.driver.logout, s.login_id)
        if tell_backend:
            try:
                await self.backend.offline(s.device_id)
            except BackendUnavailable:
                pass                                            # the backend's own sweeper marks it offline

    async def _lost(self, login_id: int) -> None:
        s = next((x for x in self.sessions.values() if x.login_id == login_id), None)
        if s is not None:
            await self._drop(s, "the recorder disconnected", tell_backend=True)

    def first_picture(self, s: Session) -> None:
        """Live video arrived from this recorder: tell the backend once, which ticks the last box on the customer's connect screen."""
        if self._loop is None or s.preview_reported:
            return
        s.preview_reported = True
        self._spawn(self._report_preview(s))

    async def _report_preview(self, s: Session) -> None:
        try:
            await self.backend.preview_ok(s.device_id)
        except BackendUnavailable:
            s.preview_reported = False                          # try again with the next stream

    def session_unusable(self, s: Session) -> None:
        """Live video could not be started: the connection is probably dead. Forget it; a live recorder registers again within seconds."""
        if self._loop is not None and self.sessions.get(s.device_id) is s:
            self._spawn(self._drop(s, "live video could not be started", tell_backend=True))
