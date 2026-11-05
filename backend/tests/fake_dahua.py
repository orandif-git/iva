"""Stand-ins for Dahua's NetSDK (the Driver the gateway uses) and for the backend, so the gateway is tested without Dahua's libraries."""
import itertools
import threading
import time

from dahua.driver import LoginResult
from registrar.backend_client import BackendUnavailable, Credentials


class FakeDriver:
    def __init__(self, recorders: dict | None = None, listen_ok: bool = True):
        self.recorders = recorders or {}          # ident -> {"key", "channels", "serial"}
        self.listen_ok = listen_ok
        self.logins: list = []                    # (ident, user, password)
        self.logged_out: list = []
        self.played: list = []                    # (login_id, channel_index, main)
        self.stopped_plays: list = []
        self.refuse_play = False
        self.chunks = [bytes([i]) * 1000 for i in range(1, 40)]
        self.chunk_delay = 0.01
        self.stopped = False
        self._ids = itertools.count(100)
        self._threads: dict = {}
        self._lock = threading.Lock()

    def start(self, bind, port, on_register, on_disconnect):
        self.bind, self.port, self._reg, self._disc = bind, port, on_register, on_disconnect
        return self.listen_ok

    # --- what the SDK would do on its own threads
    def register(self, ident: str, ip: str = "203.0.113.7", port: int = 40000) -> None:
        threading.Thread(target=self._reg, args=(ip, port, ident)).start()

    def disconnect(self, login_id: int) -> None:
        threading.Thread(target=self._disc, args=(login_id,)).start()

    # --- Driver interface
    def login(self, ip, port, ident, user, password):
        self.logins.append((ident, user, password))
        rec = self.recorders.get(ident)
        if not rec or rec["key"] != password or user != "giview":
            return LoginResult(error="wrong user or password")
        return LoginResult(login_id=next(self._ids), channels=rec["channels"], serial=rec.get("serial", "SER123"))

    def play(self, login_id, channel_index, main, on_data):
        if self.refuse_play:
            return 0
        pid = next(self._ids)
        self.played.append((login_id, channel_index, main))
        stop = threading.Event()

        def feed():
            for c in self.chunks:
                if stop.is_set():
                    return
                on_data(c)
                time.sleep(self.chunk_delay)
            while not stop.is_set():                # a live stream does not end by itself
                time.sleep(0.05)
        t = threading.Thread(target=feed, daemon=True)
        self._threads[pid] = (t, stop)
        t.start()
        return pid

    def stop_play(self, play_id):
        self.stopped_plays.append(play_id)
        if play_id in self._threads:
            self._threads[play_id][1].set()

    def logout(self, login_id):
        self.logged_out.append(login_id)

    def stop(self):
        self.stopped = True
        for _, ev in self._threads.values():
            ev.set()


class FakeBackend:
    def __init__(self, recorders: dict | None = None):
        self.recorders = recorders or {}          # device_id -> {"key", "protocol", "allowed"}
        self.heartbeats: list = []
        self.offlined: list = []
        self.previews: list = []
        self.down = False

    async def credentials(self, device_id):
        if self.down:
            raise BackendUnavailable("down")
        r = self.recorders.get(device_id)
        return Credentials(device_id, r["key"], r.get("protocol", "auto_register")) if r else None

    async def heartbeat(self, device_id, ip, channels, firmware):
        if self.down:
            raise BackendUnavailable("down")
        self.heartbeats.append((device_id, ip, channels))
        return self.recorders.get(device_id, {}).get("allowed", True)

    async def preview_ok(self, device_id):
        if self.down:
            raise BackendUnavailable("down")
        self.previews.append(device_id)

    async def offline(self, device_id):
        self.offlined.append(device_id)
