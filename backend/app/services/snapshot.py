"""One still picture from a camera, for the screen where the customer draws the counting line.

The picture comes the same way the AI worker gets video: ask the right relay for a play link (Dahua gateway or the registrar's relay for
recorders, or the camera's own address for an RTSP camera) and let FFmpeg take the first frame. Only a few seconds of video are read.
A picture is kept for 20 seconds and only one is taken at a time per camera, so a page that refreshes cannot load the recorder."""
import asyncio
import logging
import time

import httpx

from ..config import get_settings
from ..errors import ApiError
from .rtsp import mask

log = logging.getLogger("gateinsight.snapshot")
_cache: dict = {}            # camera id -> (taken at, jpeg)
_locks: dict = {}
MAX_BYTES = 2_000_000
GRAB_TIMEOUT = 25


async def _play_url(base: str, device_id: str, channel_no: int, stream: str | None) -> str:
    s = get_settings()
    body = {"device_id": device_id, "channel_no": channel_no, **({"stream": stream} if stream else {})}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{base}/relay/v1/streams", json=body, headers={"Authorization": f"Bearer {s.service_token}"})
    except httpx.HTTPError:
        raise ApiError(503, "video_unavailable", "The video service is not reachable right now.")
    if r.status_code == 201:
        return r.json()["url"]
    if r.status_code in (404, 429, 502, 504):
        raise ApiError(409, "camera_offline", "The recorder is not sending video for this camera right now. Check that it is online.")
    raise ApiError(503, "video_unavailable", "The video service could not start this camera.")


async def source_for(row, decrypt_source) -> tuple[str, str | None]:
    """(address to read, input format). `row` carries the camera and its recorder."""
    s = get_settings()
    if row["source_ciphertext"]:
        return decrypt_source(), None
    if row["protocol"] == "auto_register":
        return await _play_url(s.dahua_relay_url, row["device_id"], row["channel_no"], "sub"), "dhav"
    if row["protocol"] == "gb28181":
        return await _play_url(s.gb_relay_url, row["device_id"], row["channel_no"], None), "mpeg"
    raise ApiError(409, "no_video_source", "This camera has no video source yet.")


async def grab(url: str, fmt: str | None) -> bytes:
    s = get_settings()
    opts = ["-f", fmt, "-probesize", "2000000", "-analyzeduration", "3000000"] if fmt else []
    if url.lower().startswith("rtsp"):
        opts = ["-rtsp_transport", "tcp"]
    cmd = [s.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", *opts, "-i", url, "-frames:v", "1",
           "-vf", "scale='min(960,iw)':-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError:
        raise ApiError(503, "video_unavailable", "Taking a picture is not available on this server.")
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout=GRAB_TIMEOUT)
    except asyncio.TimeoutError:
        p.kill()
        await p.wait()
        raise ApiError(504, "snapshot_timeout", "The camera did not send a picture in time. Try again in a moment.")
    if p.returncode != 0 or not out.startswith(b"\xff\xd8") or len(out) > MAX_BYTES:
        log.info("snapshot failed: %s", mask(err.decode(errors="replace")[-200:], url))
        raise ApiError(502, "snapshot_failed", "Could not get a picture from this camera.")
    return out


def _keep() -> float:
    from ..config import get_settings
    return max(1.0, get_settings().snapshot_cache_seconds)


async def get(camera_id, row, decrypt_source, now=time.monotonic, max_age: float | None = None) -> bytes:
    """`max_age` asks for a fresher picture than the usual 20 seconds (live view); one picture is still taken at a time per camera."""
    keep = _keep() if max_age is None else max_age
    hit = _cache.get(camera_id)
    if hit and now() - hit[0] < keep:
        return hit[1]
    async with _locks.setdefault(camera_id, asyncio.Lock()):
        hit = _cache.get(camera_id)
        if hit and now() - hit[0] < keep:
            return hit[1]
        started = time.monotonic()
        try:
            url, fmt = await source_for(row, decrypt_source)
            jpeg = await grab(url, fmt)
        except Exception as e:                                           # how long a failure took is as useful as how long a picture took
            log.info("snapshot %s failed after %.1f s (%s)", camera_id, time.monotonic() - started, getattr(e, "code", type(e).__name__))
            raise
        log.info("snapshot %s took %.1f s", camera_id, time.monotonic() - started)
        if len(_cache) > 500:
            _cache.clear()
        _cache[camera_id] = (now(), jpeg)
        return jpeg
