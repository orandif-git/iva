"""Moving pictures for live view: the camera's video, turned into a stream of JPEG pictures a few times a second.

FFmpeg reads the video the same way the single-picture grab does (see snapshot.py), scales it down and writes JPEGs to a pipe; each is sent on as
one part of a `multipart/x-mixed-replace` response, which browsers and the app can both read. It stops the moment the viewing session ends,
the person leaves, or the camera stops sending. Pictures, not H.264, so it needs no player and works through every proxy.
Only a few streams run at once on one server, so a crowd of viewers cannot starve the AI workers of CPU."""
import asyncio
import logging
from typing import AsyncIterator

from ..config import get_settings
from ..db import db
from .rtsp import mask

log = logging.getLogger("gateinsight.live_stream")
SOI, EOI = b"\xff\xd8", b"\xff\xd9"
BOUNDARY = "frame"
STALL_SECONDS = 15              # no picture for this long: the camera has stopped, end the stream
MAX_BUFFER = 4_000_000
_open = 0


def pop_jpegs(buf: bytearray) -> list[bytes]:
    """Remove and return every complete JPEG at the front of the buffer. A JPEG ends at the first FFD9, because FF inside the data is always escaped."""
    out = []
    while True:
        s = buf.find(SOI)
        if s < 0:
            buf.clear()
            return out
        e = buf.find(EOI, s + 2)
        if e < 0:
            del buf[:s]
            if len(buf) > MAX_BUFFER:
                buf.clear()
            return out
        out.append(bytes(buf[s:e + 2]))
        del buf[:e + 2]


def part(jpeg: bytes) -> bytes:
    return b"--" + BOUNDARY.encode() + b"\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"


class Slot:
    """One of the server's stream places. Giving it back twice is harmless, so every exit path can do it."""
    def __init__(self) -> None:
        self.held = True

    def release(self) -> None:
        global _open
        if self.held:
            self.held = False
            _open = max(0, _open - 1)


def slot() -> Slot | None:
    """Take one of the server's stream places, if there is one."""
    global _open
    if _open >= get_settings().live_stream_max:
        return None
    _open += 1
    return Slot()


def open_count() -> int:
    return _open


async def ffmpeg_frames(url: str, fmt: str | None) -> AsyncIterator[bytes]:
    """JPEG pictures from the camera until it stops. Replaced in tests."""
    s = get_settings()
    opts = ["-f", fmt, "-probesize", "2000000", "-analyzeduration", "3000000"] if fmt else []
    if url.lower().startswith("rtsp"):
        opts = ["-rtsp_transport", "tcp"]
    cmd = [s.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", *opts, "-i", url,
           "-an", "-vf", f"fps={s.live_stream_fps},scale='min(960,iw)':-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    buf = bytearray()
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(p.stdout.read(65536), timeout=STALL_SECONDS)
            except asyncio.TimeoutError:
                log.info("live stream stalled: %s", mask("no picture", url))
                return
            if not chunk:
                return
            buf += chunk
            for j in pop_jpegs(buf):
                yield j
    finally:
        if p.returncode is None:
            p.kill()
        await p.wait()


async def still_open(session_id) -> bool:
    """Is the viewing session still running? Looked up on its own short connection, so a long stream never holds one."""
    async with db.admin_tx() as c:
        return bool(await c.fetchval("SELECT ended_at IS NULL AND expires_at > now() FROM app.live_sessions WHERE id=$1", session_id))


async def stream(session_id, url: str, fmt: str | None, place: Slot, frames=None) -> AsyncIterator[bytes]:
    """The response body: one part per picture, for as long as the session is open. `place` is given back when it ends, however it ends."""
    frames = frames or ffmpeg_frames
    last_check = asyncio.get_event_loop().time()
    try:
        async for jpeg in frames(url, fmt):
            yield part(jpeg)
            now = asyncio.get_event_loop().time()
            if now - last_check >= 1.0:
                last_check = now
                if not await still_open(session_id):
                    return
    finally:
        place.release()
