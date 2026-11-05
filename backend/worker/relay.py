"""Reading a recorder's live video through the registrar's video relay (for recorders that registered with us over GB28181)."""
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class RelaySource:
    """A camera whose video is fetched from the relay, not from an RTSP address."""
    device_id: str
    channel_no: int


class RelayError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class RelayClient:
    """Asks the relay for a play link. Each link works once and expires in about a minute, so the long-lived token below is only ever
    sent to the relay itself and never ends up on an FFmpeg command line or in a process list."""

    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None, timeout: float = 10.0):
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._h = {"Authorization": f"Bearer {token}"}

    def play_url(self, device_id: str, channel_no: int) -> str:
        try:
            r = self._http.post("/relay/v1/streams", json={"device_id": device_id, "channel_no": channel_no}, headers=self._h)
        except httpx.HTTPError as e:
            raise RelayError("relay_unreachable", f"cannot reach the video relay ({type(e).__name__})")
        if r.status_code == 201:
            return r.json()["url"]
        try:
            err = r.json()["error"]
            raise RelayError(err["code"], err["message"])
        except (ValueError, KeyError, TypeError):
            raise RelayError("relay_error", f"the video relay answered {r.status_code}")


def make_relay_reader(source: RelaySource, relay: RelayClient, width: int, height: int, fps: float, **kw):
    """A reader for a relay stream. A fresh single-use link is requested for EVERY connection, including each reconnect.
    The relay's stream carries no frame-rate information we can probe cheaply, so we ask for `fps` and rely on FFmpeg's fps filter."""
    from .live import StreamReader, build_command

    def command() -> list:
        return build_command(relay.play_url(source.device_id, source.channel_no), width, height, fps, input_format="mpeg")

    return StreamReader(width, height, fps, command, **kw)
