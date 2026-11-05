"""The worker's conversation with the backend: settings, stream address, and health reports."""
import logging

import httpx

from .relay import RelaySource

log = logging.getLogger("worker.backend")


class BackendUnavailable(Exception):
    pass


class FleetBackend:
    """Asks the backend which cameras this worker process should run."""

    def __init__(self, client: httpx.AsyncClient, token: str):
        self.client, self._h = client, {"Authorization": f"Bearer {token}"}

    async def cameras(self, shard: int, of: int) -> list[str]:
        try:
            r = await self.client.get("/internal/v1/workers/cameras", params={"shard": shard, "of": of}, headers=self._h)
        except httpx.HTTPError as e:
            raise BackendUnavailable(str(e))
        if r.status_code != 200:
            raise BackendUnavailable(f"backend returned {r.status_code}")
        return list(r.json()["cameras"])


class WorkerBackend:
    def __init__(self, client: httpx.AsyncClient, token: str, camera_id: str):
        self.client, self.camera_id, self._h = client, camera_id, {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.client.request(method, f"/internal/v1/cameras/{self.camera_id}/{path}", headers=self._h, **kw)
        except httpx.HTTPError as e:
            raise BackendUnavailable(str(e))
        if r.status_code in (401, 500, 502, 503, 504):
            raise BackendUnavailable(f"backend returned {r.status_code}")
        return r

    async def config(self) -> dict | None:
        """The camera's settings. None if the camera no longer exists. Raises BackendUnavailable when the backend cannot be reached."""
        r = await self._call("GET", "config")
        return r.json() if r.status_code == 200 else None

    async def source(self):
        """Where the video comes from: an RTSP address (text), a RelaySource, or None if there is none (or the camera may not be analysed).
        Never log the result: an address contains the camera password."""
        r = await self._call("GET", "source")
        if r.status_code != 200:
            return None
        j = r.json()
        return RelaySource(j["device_id"], j["channel_no"]) if j.get("kind") == "gb28181" else j["url"]

    async def status(self, state: str, analysis_fps: float | None, error: str | None, dropped: int | None = None) -> bool | None:
        """Report health. Returns whether the worker should keep going, or None if the report could not be delivered."""
        try:
            r = await self._call("POST", "frame-status", json={"state": state, "analysis_fps": None if analysis_fps is None else round(analysis_fps, 2),
                                                               "dropped": dropped, "error": (error or None) and error[:200]})
        except BackendUnavailable as e:
            log.debug("health report not delivered: %s", e)
            return None
        if r.status_code == 404:
            return False
        return bool(r.json().get("active")) if r.status_code == 200 else None
