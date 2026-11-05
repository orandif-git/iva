import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("registrar.backend")


class BackendUnavailable(Exception):
    """The backend could not be reached or failed. Recorders get a 503 and retry, instead of being rejected for good."""


@dataclass
class Credentials:
    device_id: str
    key: str
    protocol: str


class BackendClient:
    def __init__(self, base_url: str, token: str, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(5.0))
        self._h = {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self._http.request(method, path, headers=self._h, **kw)
        except httpx.HTTPError as e:
            raise BackendUnavailable(str(e))
        if r.status_code in (401, 500, 502, 503, 504):        # 401 means the registrar itself is misconfigured: loud, not silent
            log.error("backend %s %s -> %s", method, path.split("/")[-1], r.status_code)
            raise BackendUnavailable(f"backend returned {r.status_code}")
        return r

    async def credentials(self, device_id: str) -> Credentials | None:
        """None when the recorder is unknown or its tenant is suspended (the recorder is refused)."""
        r = await self._call("GET", f"/internal/v1/recorders/{device_id}/credentials")
        if r.status_code in (403, 404):
            return None
        r.raise_for_status()
        j = r.json()
        return Credentials(j["device_id"], j["key"], j["protocol"])

    async def heartbeat(self, device_id: str, ip: str, channels: list, firmware: str | None) -> bool:
        r = await self._call("POST", f"/internal/v1/recorders/{device_id}/heartbeat",
                             json={"ip": ip, "channels": channels[:256], "firmware": firmware})
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return bool(r.json().get("allowed"))

    async def offline(self, device_id: str) -> None:
        await self._call("POST", f"/internal/v1/recorders/{device_id}/offline")

    async def preview_ok(self, device_id: str) -> None:
        await self._call("POST", f"/internal/v1/recorders/{device_id}/preview-ok")
