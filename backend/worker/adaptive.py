"""Adaptive frame rate.

Most of the day a gate or entrance is empty, and analysing an empty picture five times a second is wasted compute. Adaptive mode
analyses slowly while nothing is there (the idle rate) and speeds up the moment a person or vehicle appears (the active rate),
then slows down again after a short hold. A cheap frame-difference check can wake it between idle samples.

Fixed mode analyses at one constant rate.

The controller works in stream time (the timestamps on frames), never the wall clock, so it behaves the same on a live camera and on
a video file replayed as fast as possible."""
from dataclasses import dataclass

import cv2
import numpy as np

IDLE, ACTIVE = "idle", "active"


@dataclass(frozen=True)
class Policy:
    mode: str = "adaptive"          # "adaptive" or "fixed"
    idle_fps: float = 2.0
    active_fps: float = 5.0
    fixed_fps: float = 5.0

    @classmethod
    def from_backend(cls, j: dict) -> "Policy":
        return cls(j.get("mode", "adaptive"), float(j.get("idle_fps", 2)), float(j.get("active_fps", 5)), float(j.get("fixed_fps", 5)))

    @property
    def capture_fps(self) -> float:
        """The fastest the stream ever has to be read."""
        return self.fixed_fps if self.mode == "fixed" else self.active_fps


class MotionDetector:
    """Notices that something moved, cheaply: compares a tiny grey copy of each frame with a slowly updating background.
    It only has to be right often enough. A false alarm just means one extra detector run, a miss is caught by the idle sampling."""

    def __init__(self, size=(64, 36), pixel_delta: float = 18.0, min_fraction: float = 0.012, blend: float = 0.25):
        self.size, self.delta, self.min_fraction, self.blend = size, pixel_delta, min_fraction, blend
        self._bg = None

    def reset(self) -> None:
        self._bg = None

    def update(self, frame) -> bool:
        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
        if self._bg is None:
            self._bg = g
            return False
        moved = float((np.abs(g - self._bg) > self.delta).mean()) >= self.min_fraction
        self._bg = (1 - self.blend) * self._bg + self.blend * g       # follows slow changes such as the light fading
        return moved


class RateController:
    """Decides, for each frame that arrives, whether it should be analysed."""

    def __init__(self, policy: Policy, capture_fps: float | None = None, hold_seconds: float = 3.0, motion_min_interval: float = 0.5):
        self.policy, self.hold, self.motion_gap = policy, hold_seconds, motion_min_interval
        self.capture_fps = capture_fps or policy.capture_fps
        self.state = ACTIVE if policy.mode == "fixed" else IDLE
        self._next_due: float | None = None
        self._last_relevant = -1e18
        self._last_motion_run = -1e18
        self._last_ts: float | None = None
        self.seconds = {IDLE: 0.0, ACTIVE: 0.0}
        self.processed = 0

    # ---------------------------------------------------------------- the rate
    @property
    def target_fps(self) -> float:
        p = self.policy
        if p.mode == "fixed":
            return p.fixed_fps
        return p.active_fps if self.state == ACTIVE else min(p.idle_fps, p.active_fps)

    def set_policy(self, policy: Policy, capture_fps: float | None = None) -> None:
        """A new policy from the admin console. Takes effect on the next frame."""
        self.policy = policy
        self.capture_fps = capture_fps or policy.capture_fps
        self.state = ACTIVE if policy.mode == "fixed" else (self.state if self.state in (IDLE, ACTIVE) else IDLE)
        self._next_due = None

    def reset_after_gap(self) -> None:
        """The stream was interrupted: forget what the scene looked like and start slowly again."""
        self.state = ACTIVE if self.policy.mode == "fixed" else IDLE
        self._next_due = self._last_ts = None
        self._last_relevant = -1e18

    # ------------------------------------------------------------ per-frame use
    def should_process(self, ts: float, motion: bool = False) -> bool:
        if self._last_ts is not None:
            self.seconds[self.state] += max(0.0, min(ts - self._last_ts, 5.0))      # time spent in each state, for the average
        self._last_ts = ts
        interval = 1.0 / self.target_fps
        slack = 0.5 / max(self.capture_fps, 1e-6)                                    # a frame may be half a frame early and still count
        if self._next_due is None:                                                   # the first frame, or the first after a gap
            self._next_due = ts + interval
        elif ts + slack >= self._next_due:                                           # its turn on the schedule
            nxt = self._next_due + interval
            self._next_due = nxt if nxt > ts else ts + interval                      # after a long stall, do not rush to catch up
        elif motion and self.state == IDLE and ts - self._last_motion_run >= self.motion_gap:
            self._last_motion_run = ts                                               # movement between idle samples: look now
            self._next_due = ts + interval
        else:
            return False
        self.processed += 1
        return True

    def observe(self, obs, ts: float) -> None:
        """Called after a frame was analysed, with what it showed."""
        if self.policy.mode == "fixed":
            self.state = ACTIVE
            return
        if obs.relevant or obs.tracks or obs.visits:
            self._last_relevant = ts
            if self.state == IDLE:
                self.state = ACTIVE
                self._next_due = ts + 1.0 / self.target_fps                          # speed up from the very next frame
        elif self.state == ACTIVE and ts - self._last_relevant >= self.hold:
            self.state = IDLE
            self._next_due = ts + 1.0 / self.target_fps

    # --------------------------------------------------------------- reporting
    def average_fps(self) -> float | None:
        total = self.seconds[IDLE] + self.seconds[ACTIVE]
        return self.processed / total if total > 0 else None

    def reset_stats(self) -> None:
        self.seconds = {IDLE: 0.0, ACTIVE: 0.0}
        self.processed = 0
