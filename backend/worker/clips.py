"""Short video clips as alert evidence.

The worker keeps the last ~25 seconds of the pictures it analyses, as small JPEGs. When the backend says "this plate raised an alert, send a
clip", the worker waits until the seconds after the moment have arrived, picks the seconds before and after, encodes an H.264 MP4 with FFmpeg
(the format WhatsApp accepts) and uploads it. Clips are made only when asked for, so a camera that raises no alerts costs a JPEG per analysed
picture and nothing else.

What the clip shows is what was analysed: with adaptive frame rate the worker analyses slowly while nothing is in view, so the seconds before a
vehicle appears are sparser than the seconds after. Raise the active frame rate in the admin console for smoother evidence."""
import asyncio
import logging
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

log = logging.getLogger("worker.clips")

OUT_FPS = 5
MAX_GAP = 3.0            # a gap longer than this in the pictures is a break (a reconnect), not something to bridge with a frozen frame
KEEP_SECONDS = 25.0


class FrameRing:
    def __init__(self, keep_seconds: float = KEEP_SECONDS):
        self.keep = keep_seconds
        self.frames: deque = deque()          # (timestamp, jpeg bytes), oldest first
        self._lk = threading.Lock()           # pictures are added from analysis threads and read from the event loop

    def add(self, ts: float, jpeg: bytes) -> None:
        with self._lk:
            if self.frames and ts <= self.frames[-1][0]:
                return                        # a repeated or out-of-order timestamp adds nothing
            self.frames.append((ts, jpeg))
            while self.frames and self.frames[0][0] < ts - self.keep:
                self.frames.popleft()

    @property
    def newest(self) -> float | None:
        with self._lk:
            return self.frames[-1][0] if self.frames else None

    def window(self, start: float, end: float) -> list:
        with self._lk:
            return [f for f in self.frames if start <= f[0] <= end]


def pick_segment(frames: list, center: float, max_gap: float = MAX_GAP) -> list:
    """Frames are split wherever the picture was interrupted; keep the unbroken stretch that holds (or is nearest to) the moment."""
    if not frames:
        return []
    segs, cur = [], [frames[0]]
    for f in frames[1:]:
        if f[0] - cur[-1][0] > max_gap:
            segs.append(cur)
            cur = []
        cur.append(f)
    segs.append(cur)
    return min(segs, key=lambda s: 0 if s[0][0] <= center <= s[-1][0] else min(abs(s[0][0] - center), abs(s[-1][0] - center)))


def resample(frames: list, fps: int = OUT_FPS) -> list[bytes]:
    """A steady `fps` video from pictures that arrived at uneven times: each output step shows the latest picture taken at or before it."""
    if not frames:
        return []
    t0, t1 = frames[0][0], frames[-1][0]
    n = max(1, int((t1 - t0) * fps) + 1)
    out, i = [], 0
    for k in range(n):
        t = t0 + k / fps
        while i + 1 < len(frames) and frames[i + 1][0] <= t + 1e-6:
            i += 1
        out.append(frames[i][1])
    return out


async def encode_mp4(jpegs: list[bytes], *, fps: int = OUT_FPS, ffmpeg: str = "ffmpeg", max_bytes: int = 15_000_000, timeout: float = 60.0) -> bytes | None:
    """H.264 (main profile, yuv420p, no audio, moov first) so it plays in WhatsApp and every browser. One retry smaller if it comes out too big."""
    if not jpegs:
        return None
    for crf, width in ((27, 960), (33, 640)):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "clip.mp4"
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
                   "-vf", f"scale='trunc(min({width},iw)/2)*2':-2,format=yuv420p", "-c:v", "libx264", "-profile:v", "main", "-preset", "veryfast", "-crf", str(crf),
                   "-r", str(fps), "-movflags", "+faststart", "-an", str(out)]
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(proc.communicate(b"".join(jpegs)), timeout)
            except (OSError, asyncio.TimeoutError) as e:
                log.warning("clip encoding failed: %s", type(e).__name__)
                try:
                    proc.kill()
                except Exception:
                    pass
                return None
            if proc.returncode != 0 or not out.exists():
                log.warning("ffmpeg could not encode the clip: %s", err.decode("utf-8", "replace")[-200:])
                return None
            data = out.read_bytes()
        if len(data) <= max_bytes:
            return data
        log.info("clip is %d bytes, trying again smaller", len(data))
    return None


class ClipRecorder:
    def __init__(self, sink, *, ffmpeg: str = "ffmpeg", fps: int = OUT_FPS, keep: float = KEEP_SECONDS, max_bytes: int = 15_000_000, quality: int = 75,
                 extra_wait: float = 6.0, concurrency: int = 2, retry_delays=(2.0, 5.0), sleep=asyncio.sleep, clock=time.monotonic):
        self.sink, self.ffmpeg, self.fps, self.max_bytes, self.quality = sink, ffmpeg, fps, max_bytes, quality
        self.ring = FrameRing(keep)
        self.extra_wait, self.retry_delays, self._sleep, self._clock = extra_wait, retry_delays, sleep, clock
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: set = set()
        self.closing = False
        self.stats = {"requested": 0, "uploaded": 0, "failed": 0, "frames_buffered": 0}

    def add(self, frame, ts: datetime) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if ok:
            self.ring.add(ts.timestamp(), buf.tobytes())
            self.stats["frames_buffered"] += 1

    def request(self, camera_id: str, seen_at: str, alert_id: str, before: float = 5.0, after: float = 4.0) -> None:
        """Called when the backend answers a plate batch with 'send a clip for this one'."""
        try:
            center = datetime.fromisoformat(seen_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return
        self.stats["requested"] += 1
        task = asyncio.get_running_loop().create_task(self._make(alert_id, center, before, after))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _make(self, alert_id: str, center: float, before: float, after: float) -> None:
        try:
            started = self._clock()
            while not self.closing and (self.ring.newest or 0) < center + after and self._clock() - started < after + self.extra_wait:
                await self._sleep(0.25)       # the seconds after the moment may not have arrived yet
            frames = pick_segment(self.ring.window(center - before, center + after), center)
            if len(frames) < 2:
                log.warning("no pictures around the moment, so no clip for alert %s", alert_id)
                self.stats["failed"] += 1
                return
            async with self._sem:
                data = await encode_mp4(resample(frames, self.fps), fps=self.fps, ffmpeg=self.ffmpeg, max_bytes=self.max_bytes)
            if data is None:
                self.stats["failed"] += 1
                return
            for attempt in range(len(self.retry_delays) + 1):
                outcome = await self.sink.upload_clip(alert_id, data)
                if outcome == "ok":
                    self.stats["uploaded"] += 1
                    return
                if outcome == "drop" or attempt == len(self.retry_delays):
                    break
                await self._sleep(self.retry_delays[attempt])
            self.stats["failed"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("clip for alert %s failed", alert_id)
            self.stats["failed"] += 1

    def close(self) -> None:
        """The stream has ended: stop waiting for more pictures and use what there is."""
        self.closing = True

    async def drain(self, timeout: float = 30.0) -> None:
        self.close()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)
