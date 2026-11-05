"""Reading a recorder's live video through the registrar's video relay (for recorders that registered with us over GB28181)."""
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class RelaySource:
    """A camera whose video is fetched from a relay, not from an RTSP address.
    kind "gb28181": the registrar's relay (MPEG program stream). kind "auto_register": the Dahua gateway (DHAV, main or sub stream)."""
    device_id: str
    channel_no: int
    kind: str = "gb28181"
    stream: str | None = None


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

    def play_url(self, device_id: str, channel_no: int, stream: str | None = None) -> str:
        body = {"device_id": device_id, "channel_no": channel_no, **({"stream": stream} if stream else {})}
        try:
            r = self._http.post("/relay/v1/streams", json=body, headers=self._h)
        except httpx.HTTPError as e:
            raise RelayError("relay_unreachable", f"cannot reach the video relay ({type(e).__name__})")
        if r.status_code == 201:
            return r.json()["url"]
        try:
            err = r.json()["error"]
            raise RelayError(err["code"], err["message"])
        except (ValueError, KeyError, TypeError):
            raise RelayError("relay_error", f"the video relay answered {r.status_code}")


class Relays:
    """The registrar's relay and the Dahua gateway, picked by the camera's source kind."""

    def __init__(self, gb: RelayClient, dahua: RelayClient):
        self.gb, self.dahua = gb, dahua

    def for_source(self, source: RelaySource) -> RelayClient:
        return self.dahua if source.kind == "auto_register" else self.gb


_FORMATS = {"gb28181": "mpeg", "auto_register": "dhav"}      # what FFmpeg cannot guess from a live HTTP stream


def make_relay_reader(source: RelaySource, relay: RelayClient, width: int, height: int, fps: float, **kw):
    """A reader for a relay stream. A fresh single-use link is requested for EVERY connection, including each reconnect.
    The relay's stream carries no frame-rate information we can probe cheaply, so we ask for `fps` and rely on FFmpeg's fps filter."""
    from .live import StreamReader, build_command

    client = relay.for_source(source) if isinstance(relay, Relays) else relay

    def command() -> list:
        return build_command(client.play_url(source.device_id, source.channel_no, source.stream), width, height, fps,
                             input_format=_FORMATS.get(source.kind, "mpeg"))

    return StreamReader(width, height, fps, command, **kw)
