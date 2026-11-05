import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2


class SourceError(Exception):
    pass


class VideoFileSource:
    """Plays a video file as if it were a camera: frames sampled down to the target rate, each with a timestamp.

    By default the clip is placed so that it ends "now". Pass start_time to pin it, for repeatable runs."""

    def __init__(self, path, target_fps: float = 5.0, start_time: datetime | None = None, realtime: bool = False):
        self.path = Path(path)
        if not self.path.is_file():
            raise SourceError(f"video not found: {self.path}")
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise SourceError(f"cannot open video (unsupported or damaged): {self.path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.src_fps = fps if 1.0 <= fps <= 240.0 else 25.0
        self.total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.width, self.height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        self.duration = self.total / self.src_fps if self.total else 0.0
        self.step = max(1, round(self.src_fps / target_fps))
        self.effective_fps = self.src_fps / self.step
        self.start = (start_time or datetime.now(timezone.utc) - timedelta(seconds=self.duration)).astimezone(timezone.utc)
        self.realtime = realtime

    def frames(self):
        """Yields (frame, timestamp)."""
        cap = cv2.VideoCapture(str(self.path))
        t0, idx = time.monotonic(), 0
        try:
            while cap.grab():
                if idx % self.step == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    offset = idx / self.src_fps
                    if self.realtime:
                        wait = offset - (time.monotonic() - t0)
                        if wait > 0:
                            time.sleep(wait)
                    yield frame, self.start + timedelta(seconds=offset)
                idx += 1
        finally:
            cap.release()
