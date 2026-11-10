"""Delivering events to the backend.

Footfall is sent as the running total for each minute, and the backend keeps the larger figure, so resending is always safe.
If the backend is unavailable the events are kept (up to a limit) and retried with growing delays. Nothing is silently lost:
anything dropped is counted."""
import asyncio
import logging
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone

import httpx

log = logging.getLogger("worker.sink")

# If the backend says one of these, this camera is not allowed to send data, so the worker should stop instead of retrying.
FATAL_REASONS = {"unknown_camera", "tenant_suspended", "footfall_not_enabled", "plates_not_enabled"}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class BackendSink:
    def __init__(self, client: httpx.AsyncClient, token: str, max_buffer: int = 5000, clock=time.monotonic, sleep=asyncio.sleep):
        self.client, self._h = client, {"Authorization": f"Bearer {token}"}
        self.max_buffer, self.clock, self.sleep = max_buffer, clock, sleep
        self.footfall: dict = {}                       # (camera_id, minute) -> [in, out]; later values replace earlier ones
        self.plates: deque = deque()
        self.zones: deque = deque()                    # what was seen in marked areas
        self.stats: Counter = Counter()
        self.rejected: Counter = Counter()
        self.fatal: str | None = None
        self.fatal_cameras: dict = {}                  # camera_id -> why the backend will never accept data from it
        self.shared = False                            # True when several cameras use this sink: one camera's refusal then stops only that camera
        self.clip_handler = None                       # set by the pipeline: called as (camera_id, seen_at, alert_id, before, after)
        self.clip_handlers: dict = {}                  # camera_id -> handler, when the sink is shared
        self._lk = threading.Lock()                    # analysis may run in threads while the sender runs on the event loop
        self._flush_lock = asyncio.Lock()
        self._failures = 0
        self._next = 0.0

    # ------------------------------------------------------------------ queueing
    def set_footfall(self, camera_id: str, minute: datetime, people_in: int, people_out: int) -> None:
        with self._lk:
            if camera_id in self.fatal_cameras:
                return
            self.footfall[(camera_id, minute)] = [people_in, people_out]
            while len(self.footfall) > self.max_buffer:
                self.footfall.pop(next(iter(self.footfall)))
                self.stats["dropped"] += 1

    def add_plate(self, camera_id: str, event: dict, snapshot: bytes | None = None) -> None:
        """`snapshot` is the evidence picture (JPEG). It is uploaded just before the plate is reported, so alerts can include it."""
        item = {"camera_id": camera_id, **event}
        if snapshot:
            item["_jpeg"], item["_tries"] = snapshot, 0          # private keys: never sent to the backend, freed with the item
        with self._lk:
            if camera_id in self.fatal_cameras:
                return
            self.plates.append(item)
            while len(self.plates) > self.max_buffer:
                self.plates.popleft()
                self.stats["dropped"] += 1

    def add_zone(self, camera_id: str, sample: dict, snapshot: bytes | None = None) -> None:
        """One report for a marked area: {zone_id, at, people, wait_seconds, phone_seconds}. A `snapshot` is uploaded first so the alert can show it."""
        item = {"camera_id": camera_id, **sample}
        if snapshot:
            item["_jpeg"], item["_tries"] = snapshot, 0
        with self._lk:
            if camera_id in self.fatal_cameras:
                return
            self.zones.append(item)
            while len(self.zones) > self.max_buffer:
                self.zones.popleft()
                self.stats["dropped"] += 1

    @property
    def pending(self) -> int:
        return len(self.footfall) + len(self.plates) + len(self.zones)

    def view(self, camera_id: str) -> "SinkView":
        """What one camera's pipeline uses when several cameras share this sink."""
        self.shared = True
        return SinkView(self, camera_id)

    # ------------------------------------------------------------------- sending
    async def _post(self, path: str, events: list):
        """The parsed answer, or None when it should be retried later."""
        try:
            r = await self.client.post(path, json={"events": events}, headers=self._h)
        except httpx.HTTPError as e:
            log.warning("backend unreachable: %s", e)
            return None
        if r.status_code in (401, 403):
            self.fatal = "not_authorised"
            log.error("the backend refused this worker's token")
            return None
        if r.status_code == 422:                       # a batch the backend will never accept: drop it rather than loop forever
            log.error("backend rejected a batch as invalid: %s", r.text[:300])
            self.stats["dropped"] += len(events)
            return {"accepted": 0, "duplicates": 0, "rejected": [], "dropped": True}
        if r.status_code != 200:
            log.warning("backend returned %s", r.status_code)
            return None
        return r.json()

    async def _upload_picture(self, item: dict) -> None:
        """Best effort: a plate read is worth more than its picture, so after three failed uploads the read goes without one."""
        jpeg = item.get("_jpeg")
        if jpeg is None or item.get("evidence_id"):
            return
        try:
            r = await self.client.post("/internal/v1/evidence", params={"camera_id": item["camera_id"]}, content=jpeg,
                                       headers={**self._h, "Content-Type": "image/jpeg"})
        except httpx.HTTPError as e:
            log.warning("could not upload a picture: %s", e)
            item["_tries"] += 1
        else:
            if r.status_code == 201:
                item["evidence_id"] = r.json()["evidence_id"]
                item.pop("_jpeg", None)
                self.stats["pictures_sent"] += 1
                return
            log.warning("picture upload refused with %s", r.status_code)
            item["_tries"] += 3 if r.status_code in (401, 403, 404, 413, 415) else 1       # a refusal that will never succeed is not retried
        if item["_tries"] >= 3:
            item.pop("_jpeg", None)
            self.stats["pictures_dropped"] += 1

    async def upload_clip(self, alert_id: str, data: bytes) -> str:
        """Returns 'ok', 'retry' (try again later) or 'drop' (the backend will never accept it)."""
        try:
            r = await self.client.post(f"/internal/v1/alerts/{alert_id}/clip", content=data, headers={**self._h, "Content-Type": "video/mp4"}, timeout=60)
        except httpx.HTTPError as e:
            log.warning("could not upload a clip: %s", e)
            return "retry"
        if r.status_code in (200, 201):
            return "ok"
        log.warning("clip upload refused with %s", r.status_code)
        return "retry" if r.status_code >= 500 or r.status_code == 429 else "drop"

    def _note(self, res: dict, cams: list | None = None) -> None:
        for rej in res.get("rejected", []):
            self.rejected[rej["reason"]] += 1
            if rej["reason"] in FATAL_REASONS:
                cam = cams[rej["index"]] if cams and 0 <= rej["index"] < len(cams) else None
                if cam is not None:
                    self.fatal_cameras[cam] = rej["reason"]
                if not self.shared:
                    self.fatal = rej["reason"]
        self.stats["duplicates"] += res.get("duplicates", 0)

    def _drop_fatal(self) -> None:
        """Nothing more is sent for a camera the backend refuses, so its waiting data is discarded instead of retried for ever."""
        with self._lk:
            if not self.fatal_cameras:
                return
            for k in [k for k in self.footfall if k[0] in self.fatal_cameras]:
                del self.footfall[k]
            if any(p["camera_id"] in self.fatal_cameras for p in self.plates):
                self.plates = deque(p for p in self.plates if p["camera_id"] not in self.fatal_cameras)
            if any(z["camera_id"] in self.fatal_cameras for z in self.zones):
                self.zones = deque(z for z in self.zones if z["camera_id"] not in self.fatal_cameras)

    async def flush(self, force: bool = False) -> bool:
        """Try to send everything pending. Returns True when nothing is left to send. Only one flush runs at a time."""
        if not force and self._flush_lock.locked():
            return self.pending == 0
        async with self._flush_lock:
            return await self._flush(force)

    async def _flush(self, force: bool) -> bool:
        if self.fatal or (not force and self.clock() < self._next):
            return self.pending == 0
        self._drop_fatal()
        ok = True
        with self._lk:
            items = list(self.footfall.items())
        for i in range(0, len(items), 1000):
            chunk = items[i:i + 1000]
            body = [{"camera_id": cam, "bucket_start": iso(m), "people_in": v[0], "people_out": v[1]} for (cam, m), v in chunk]
            res = await self._post("/internal/v1/events/footfall", body)
            if res is None:
                ok = False
                break
            self._note(res, [cam for (cam, _), _v in chunk])
            self.stats["footfall_sent"] += res.get("accepted", 0)
            with self._lk:
                for key, v in chunk:                   # keep a minute that changed while the request was in flight
                    if self.footfall.get(key) == v:
                        del self.footfall[key]
        while ok and self.plates and not self.fatal:
            with self._lk:
                chunk = list(self.plates)[:200]
            for item in chunk:
                await self._upload_picture(item)
            if any("_jpeg" in it for it in chunk):              # a picture is still being retried: hold the plates, try again after the usual delay
                ok = False
                break
            res = await self._post("/internal/v1/events/plates", [{k: v for k, v in it.items() if not k.startswith("_")} for it in chunk])
            if res is None:
                ok = False
                break
            self._note(res, [it["camera_id"] for it in chunk])
            self.stats["plates_sent"] += res.get("accepted", 0)
            for c in res.get("clips", []):                 # the backend wants a video for some of these reads
                if 0 <= c["index"] < len(chunk):
                    cam = chunk[c["index"]]["camera_id"]
                    handler = self.clip_handlers.get(cam) or (None if self.shared else self.clip_handler)
                    if handler:
                        try:
                            handler(cam, chunk[c["index"]]["seen_at"], c["alert_id"], c.get("seconds_before", 5), c.get("seconds_after", 4))
                        except Exception:
                            log.exception("could not start a clip")
            sent = {id(it) for it in chunk}
            with self._lk:
                self.plates = deque(p for p in self.plates if id(p) not in sent)
            self._drop_fatal()
        while ok and self.zones and not self.fatal:
            with self._lk:
                chunk = list(self.zones)[:200]
            for item in chunk:
                await self._upload_picture(item)
            if any("_jpeg" in it for it in chunk):              # a picture is still being retried: hold the reports a little
                ok = False
                break
            res = await self._post("/internal/v1/events/zones", [{k: v for k, v in it.items() if not k.startswith("_")} for it in chunk])
            if res is None:
                ok = False
                break
            self._note(res, [it["camera_id"] for it in chunk])
            self.stats["zones_sent"] += res.get("accepted", 0)
            sent = {id(it) for it in chunk}
            with self._lk:
                self.zones = deque(z for z in self.zones if id(z) not in sent)
            self._drop_fatal()
        if ok:
            self._failures, self._next = 0, 0.0
        elif not self.fatal:
            self._failures += 1
            self.stats["retries"] += 1
            self._next = self.clock() + min(30.0, 2.0 ** self._failures)
        return self.pending == 0

    async def close(self, attempts: int = 6) -> int:
        """Last chance to deliver. Returns how many events could not be sent."""
        for i in range(attempts):
            if await self.flush(force=True) or self.fatal:
                break
            await self.sleep(min(2 ** i, 5))
        return self.pending


class SinkView:
    """One camera's window onto a shared sink: the same interface a pipeline and the live runner expect from a sink."""

    def __init__(self, sink: BackendSink, camera_id: str):
        self._sink, self.camera_id = sink, camera_id

    def set_footfall(self, *a, **k):
        self._sink.set_footfall(*a, **k)

    def add_plate(self, *a, **k):
        self._sink.add_plate(*a, **k)

    def add_zone(self, *a, **k):
        self._sink.add_zone(*a, **k)

    @property
    def fatal(self):
        return self._sink.fatal or self._sink.fatal_cameras.get(self.camera_id)

    @property
    def clip_handler(self):
        return self._sink.clip_handlers.get(self.camera_id)

    @clip_handler.setter
    def clip_handler(self, fn):
        self._sink.clip_handlers[self.camera_id] = fn

    @property
    def stats(self):
        return self._sink.stats

    @property
    def rejected(self):
        return self._sink.rejected

    @property
    def pending(self):
        return self._sink.pending

    async def upload_clip(self, alert_id, data):
        return await self._sink.upload_clip(alert_id, data)

    async def flush(self, force=False):
        return await self._sink.flush(force)

    async def close(self, attempts: int = 6) -> int:
        """This camera is stopping. Send what can be sent now; the shared sink keeps retrying for the others, so wait only briefly."""
        for i in range(min(attempts, 3)):
            if await self._sink.flush(force=True) or self.fatal:
                break
            await self._sink.sleep(min(2 ** i, 2))
        return self._sink.pending


class MemorySink:
    """Same interface, keeps everything in memory. For dry runs and tests."""

    def __init__(self):
        self.footfall: dict = {}
        self.plates: list = []
        self.zones: list = []
        self.fatal = None
        self.stats: Counter = Counter()

    def set_footfall(self, camera_id, minute, people_in, people_out):
        self.footfall[(camera_id, minute)] = [people_in, people_out]

    clip_handler = None

    async def upload_clip(self, alert_id, data):
        self.clips = getattr(self, "clips", [])
        self.clips.append((alert_id, data))
        return "ok"

    def add_plate(self, camera_id, event, snapshot=None):
        self.plates.append({"camera_id": camera_id, **event})

    def add_zone(self, camera_id, sample, snapshot=None):
        self.zones.append({"camera_id": camera_id, **sample, "has_picture": bool(snapshot)})

    async def flush(self, force=False):
        return True

    async def close(self, attempts=0):
        return 0
