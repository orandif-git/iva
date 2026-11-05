"""Live camera input over RTSP, through an FFmpeg subprocess.

FFmpeg does the network work and decoding and hands us raw pictures of one exact size, so cutting the byte stream into frames is
simply "read exactly width x height x 3 bytes". (Asking for JPEG and cutting at a fixed size, as a similar open-source project
does, corrupts frames: JPEG frames vary in length.)

What this module adds around FFmpeg:
  - reconnects forever, with growing delays, so a network blip never ends the worker;
  - a watchdog that restarts a connection that is open but silent;
  - a short queue that drops the oldest picture when analysis falls behind, so we always look at the present;
  - every picture stamped with the time it ARRIVED, and a counter that changes at each reconnect so the caller knows tracking was interrupted;
  - camera passwords removed from everything that is logged or reported."""
import json
import logging
import random
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

log = logging.getLogger("worker.live")
_USERINFO = re.compile(r"(rtsps?://)[^@\s/]+@", re.I)
_RTSP = ("rtsp://", "rtsps://")
_NETWORK = _RTSP + ("http://", "https://")


def mask_url(text: str | None) -> str:
    """Replace the user name and password in any RTSP address inside `text`."""
    return _USERINFO.sub(r"\1***@", text or "")


_flag_cache: dict = {}


def rtsp_timeout_flag(ffmpeg: str = "ffmpeg") -> str:
    """FFmpeg 5 and later call the RTSP socket timeout -timeout; older versions call it -stimeout. Ask the installed one."""
    if ffmpeg not in _flag_cache:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-h", "demuxer=rtsp"], capture_output=True, text=True, timeout=10).stdout
            _flag_cache[ffmpeg] = "-stimeout" if "stimeout" in out else "-timeout"
        except (OSError, subprocess.SubprocessError):
            _flag_cache[ffmpeg] = "-timeout"
    return _flag_cache[ffmpeg]


def _input_options(url: str, ffmpeg: str, timeout_s: float) -> list:
    return ["-rtsp_transport", "tcp", rtsp_timeout_flag(ffmpeg), str(int(timeout_s * 1_000_000))] if url.lower().startswith(_RTSP) else []


def build_command(url: str, width: int, height: int, fps: float, *, ffmpeg: str = "ffmpeg", timeout_s: float = 5.0, input_format: str | None = None) -> list:
    """FFmpeg arguments that output raw BGR pictures of exactly width x height at `fps`.
    input_format="mpeg" reads an MPEG program stream, which is what the video relay serves (FFmpeg cannot guess it from a live HTTP stream)."""
    # A camera delivers pictures at its own pace. A file would be read as fast as the disk allows, so play files at real speed (-re):
    # that makes a video file behave like a camera, which is how the live path is tried out without one.
    realtime = [] if url.lower().startswith(_NETWORK) else ["-re"]
    fmt = ["-f", input_format, "-probesize", "1000000", "-analyzeduration", "1500000"] if input_format else []
    http_timeout = ["-rw_timeout", str(int(timeout_s * 1_000_000))] if url.lower().startswith(("http://", "https://")) else []
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *_input_options(url, ffmpeg, timeout_s), *http_timeout,
            "-fflags", "nobuffer", "-flags", "low_delay", *realtime, *fmt, "-i", url, "-an", "-sn", "-dn",
            "-vf", f"fps={fps:g},scale={width}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def probe_stream(url: str, *, ffprobe: str = "ffprobe", timeout_s: float = 10.0) -> dict | None:
    """The stream's own frame rate and size, or None if it cannot be read. Used so we never ask for more frames than the camera makes."""
    cmd = [ffprobe, "-v", "error", *_input_options(url, ffprobe.replace("ffprobe", "ffmpeg"), timeout_s), "-select_streams", "v:0",
           "-show_entries", "stream=avg_frame_rate,width,height", "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 2)
        s = json.loads(out.stdout)["streams"][0]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return None
    num, _, den = str(s.get("avg_frame_rate", "0/0")).partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {"fps": fps if 0.5 <= fps <= 120 else None, "width": s.get("width"), "height": s.get("height")}


@dataclass
class Frame:
    image: np.ndarray            # height x width x 3, BGR
    ts: datetime                 # when it arrived
    epoch: int                   # changes at every reconnect


class LatestQueue:
    """A small thread-safe queue. When it is full the OLDEST picture is thrown away, so a slow consumer sees the present."""

    def __init__(self, maxlen: int = 4):
        self.maxlen, self.dropped = maxlen, 0
        self._d: deque = deque()
        self._cv = threading.Condition()

    def put(self, item) -> None:
        with self._cv:
            if len(self._d) >= self.maxlen:
                self._d.popleft()
                self.dropped += 1
            self._d.append(item)
            self._cv.notify()

    def get(self, timeout: float):
        with self._cv:
            if not self._d:
                self._cv.wait(timeout)
            return self._d.popleft() if self._d else None

    def __len__(self) -> int:
        return len(self._d)


class StreamReader:
    def __init__(self, width: int, height: int, fps: float, command, *, max_queue: int = 4, stall_seconds: float = 8.0,
                 backoff=(1, 2, 4, 8, 16, 30), stable_seconds: float = 10.0, jitter: float = 0.2,
                 clock=time.monotonic, now=lambda: datetime.now(timezone.utc)):
        """command: a function returning the argument list to start the stream. It is called again for every (re)connection."""
        self.width, self.height, self.fps = width, height, fps
        self.effective_fps = fps                       # may end up lower if the camera makes fewer frames than requested
        self._cmd, self._n = command, width * height * 3
        self.queue = LatestQueue(max_queue)
        self.stall, self.backoff, self.stable, self.jitter = stall_seconds, backoff, stable_seconds, jitter
        self._clock, self._now = clock, now
        self.state, self.last_error = "connecting", None
        self.frames = self.reconnects = self.epoch = 0
        self._down_since: float | None = clock()
        self._stop = threading.Event()
        self._proc = None
        self._progress = clock()
        self._threads: list = []

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        for target in (self._run, self._watch):
            t = threading.Thread(target=target, daemon=True, name=f"reader-{target.__name__}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._kill(self._proc)
        for t in self._threads:
            t.join(timeout=5)
        self.state = "stopped"

    def get(self, timeout: float = 0.5) -> Frame | None:
        return self.queue.get(timeout)

    def down_seconds(self) -> float:
        """How long the stream has been unavailable (0 while streaming)."""
        return 0.0 if self._down_since is None else self._clock() - self._down_since

    # --------------------------------------------------------------------- loop
    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            started, got = self._clock(), False
            tail: deque = deque(maxlen=8)
            proc = None
            try:
                proc = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, bufsize=0)
                self._proc, self._progress = proc, self._clock()
                threading.Thread(target=self._drain, args=(proc, tail), daemon=True).start()
                while not self._stop.is_set():
                    buf = self._read_frame(proc)
                    if buf is None:
                        break
                    if not got:
                        got = True
                        self.epoch += 1
                        self.state, self._down_since = "streaming", None
                        log.info("stream up (picture %dx%d at %g fps)", self.width, self.height, self.effective_fps)
                    self.frames += 1
                    self.queue.put(Frame(np.frombuffer(buf, np.uint8).reshape(self.height, self.width, 3), self._now(), self.epoch))
            except Exception as e:                      # a failure to start FFmpeg at all, for example
                tail.append(str(e))
            finally:
                self._kill(proc)
            if self._stop.is_set():
                break
            if self._down_since is None:
                self._down_since = self._clock()
            lines = [ln for ln in tail if ln.strip()]
            self.last_error = mask_url(lines[-1])[:200] if lines else "the stream ended"
            attempt = 0 if (got and self._clock() - started >= self.stable) else attempt + 1
            self.reconnects += 1
            self.state = "reconnecting"
            delay = self._delay(attempt)
            log.warning("stream lost (%s). Reconnecting in %.1f s", self.last_error, delay)
            self._stop.wait(delay * (1 + self.jitter * random.random()))

    def _delay(self, attempt: int) -> float:
        """Seconds to wait before reconnect number `attempt`: the schedule grows, then stays at its last value."""
        return float(self.backoff[min(max(attempt, 1), len(self.backoff)) - 1])

    def _read_frame(self, proc):
        buf = bytearray(self._n)
        view, got = memoryview(buf), 0
        while got < self._n:
            r = proc.stdout.readinto(view[got:])
            if not r:
                return None
            got += r
            self._progress = self._clock()
        return buf

    def _drain(self, proc, tail: deque) -> None:
        try:
            for line in iter(proc.stderr.readline, b""):
                tail.append(line.decode("utf-8", "replace").strip())
        except (OSError, ValueError):
            pass

    def _watch(self) -> None:
        """A connection can stay open and send nothing. If no data arrives for `stall` seconds, kill it so the loop reconnects."""
        while not self._stop.wait(0.25):
            p = self._proc
            if p is not None and p.poll() is None and self._clock() - self._progress > self.stall:
                log.warning("no data for %.0f s: restarting the connection", self.stall)
                self._progress = self._clock()
                self._kill(p)

    @staticmethod
    def _kill(proc) -> None:
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass


def make_stream_reader(url: str, width: int, height: int, fps: float, *, ffmpeg: str = "ffmpeg", probe: bool = True, **kw) -> StreamReader:
    """The real reader: FFmpeg on `url`. Before each connection it asks the camera what frame rate it really makes."""
    holder: dict = {}

    def command() -> list:
        use = fps
        if probe:
            info = probe_stream(url, ffprobe=ffmpeg.replace("ffmpeg", "ffprobe"))
            if info and info["fps"]:
                use = min(fps, float(int(info["fps"])) or fps)         # never ask for more frames than the camera makes
        holder["reader"].effective_fps = use
        return build_command(url, width, height, use, ffmpeg=ffmpeg)

    reader = StreamReader(width, height, fps, command, **kw)
    holder["reader"] = reader
    return reader
