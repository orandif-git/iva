"""Smooth live video through go2rtc (WebRTC), with the picture stream kept as the fallback.

The browser or phone sends an SDP offer; this module points go2rtc at the camera and returns its answer. Two things are done here so
the app does not have to know them:

* The camera's address is the same one a single picture uses (`snapshot.source_for`): the camera itself, the Dahua gateway, or the
  registrar's relay for a recorder behind a customer's router. The relay's play links are single use, so the source is registered
  again for every new offer — a reconnecting player gets a fresh link instead of a dead one.
* The video is turned into H.264/MPEG-TS by FFmpeg before go2rtc sees it. WebRTC only carries H.264 (many cameras send H.265), and
  the relay's MPEG-PS needs a demuxer. Doing it in the `exec:` source keeps go2rtc's own configuration out of the customer's data.

If go2rtc is not configured or is not answering, the caller gets an ordinary error and the app keeps the picture stream; a customer
never loses the camera because the smooth path is down.
"""
import logging
import shlex
from uuid import UUID

import httpx

from ..config import get_settings
from ..errors import ApiError
from . import snapshot
from .rtsp import mask

log = logging.getLogger("gateinsight.go2rtc")
STREAM_PREFIX = "iv"


def stream_name(camera_id: UUID | str) -> str:
    """go2rtc's name for one camera. The camera id is unique across customers, so two shops never share a stream."""
    return f"{STREAM_PREFIX}_{str(camera_id).replace('-', '')}"


def source_command(url: str, fmt: str | None) -> str:
    """The `exec:` line go2rtc runs. FFmpeg reads the camera the same way the still-picture code does and writes MPEG-TS to stdout.

    H.264 baseline with no B-frames is what every browser and phone can decode in WebRTC. It is a little more CPU than copying the
    camera's own stream, which is why only live views take this path (AI view keeps its pictures)."""
    s = get_settings()
    opts = ["-f", fmt, "-probesize", "2000000", "-analyzeduration", "3000000"] if fmt else []
    if url.lower().startswith("rtsp"):
        opts = ["-rtsp_transport", "tcp"]
    cmd = [
        s.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin",
        *opts, "-i", url,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-profile:v", "baseline", "-level", "3.1", "-pix_fmt", "yuv420p", "-g", "30", "-sc_threshold", "0",
        "-f", "mpegts", "pipe:1",
    ]
    return "exec:" + " ".join(shlex.quote(part) for part in cmd)


async def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(base_url=s.go2rtc_url.rstrip("/"), timeout=httpx.Timeout(20, connect=5))


def configured() -> bool:
    return bool(get_settings().go2rtc_url)


async def whep(camera_id: UUID, row, decrypt, offer: str) -> str:
    """Register the camera with go2rtc and return the SDP answer to the caller's offer."""
    if not configured():
        raise ApiError(503, "smooth_video_unavailable", "Smooth video is not set up on this server yet. The picture view still works.")
    url, fmt = await snapshot.source_for(row, decrypt)
    name = stream_name(camera_id)
    try:
        async with await _client() as c:
            r = await c.put("/api/streams", params={"name": name, "src": source_command(url, fmt)})
            if r.status_code >= 300:
                log.warning("go2rtc would not take the stream for %s: %s %s", name, r.status_code, r.text[:200])
                raise ApiError(503, "smooth_video_unavailable", "Smooth video could not be started. The picture view still works.")
            r = await c.post("/api/webrtc", params={"src": name}, content=offer.encode(),
                             headers={"Content-Type": "application/sdp"})
    except httpx.HTTPError:
        # The address is not logged: it can carry a one-time relay token.
        log.warning("go2rtc is not reachable")
        raise ApiError(503, "smooth_video_unavailable", "Smooth video is not reachable right now. The picture view still works.")
    if r.status_code >= 300 or not r.text.lstrip().startswith("v="):
        log.info("go2rtc refused the offer for %s: %s", name, mask(r.text[-200:], url))
        raise ApiError(503, "smooth_video_unavailable", "Smooth video could not be started. The picture view still works.")
    return r.text
