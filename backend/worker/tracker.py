"""Multi-object tracker in the style of ByteTrack: confident detections are matched first, then weak ones are used to keep
tracks alive through blur and partial hiding. Matching is by overlap with a constant-velocity prediction.

Motion is predicted from real elapsed time, not from frame counts, because the analysis rate changes (adaptive mode) and frames
can be dropped. A track also ends after a gap in seconds, so it lasts as long at 2 frames per second as at 25."""
from dataclasses import dataclass, field
from itertools import count

from .geometry import iou

MAX_EXTRAPOLATE = 1.0     # seconds. Beyond this a straight-line guess is more likely wrong than right


@dataclass
class Detection:
    box: tuple              # x1, y1, x2, y2 in pixels
    score: float
    label: str


@dataclass(eq=False)
class Track:
    id: int
    label: str
    box: tuple
    score: float
    hits: int = 1
    missed: int = 0
    confirmed: bool = False
    first_ts: float = 0.0
    last_ts: float = 0.0
    centers: list = field(default_factory=list)       # [(ts, cx, cy)] pixel centres, bounded

    @property
    def center(self):
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)

    def predicted_box(self, ts: float):
        """Where the box should be at time `ts`, assuming it keeps its last speed (for at most MAX_EXTRAPOLATE seconds)."""
        if len(self.centers) < 2:
            return self.box
        (t0, x0, y0), (t1, x1, y1) = self.centers[-2], self.centers[-1]
        if t1 <= t0:
            return self.box
        h = min(max(ts - t1, 0.0), MAX_EXTRAPOLATE)
        dx, dy = (x1 - x0) / (t1 - t0) * h, (y1 - y0) / (t1 - t0) * h
        b = self.box
        return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


class Tracker:
    def __init__(self, high=0.45, low=0.15, match_iou=0.25, max_gap=2.0, min_hits=2, max_history=200):
        self.high, self.low, self.match_iou, self.max_gap, self.min_hits, self.max_history = high, low, match_iou, max_gap, min_hits, max_history
        self.tracks: list[Track] = []
        self.finished: list[Track] = []                # tracks that ended since the caller last drained this
        self._ids = count(1)

    @staticmethod
    def _greedy(tracks, dets, min_iou, ts):
        pairs = sorted(((iou(t.predicted_box(ts), d.box), ti, di) for ti, t in enumerate(tracks) for di, d in enumerate(dets)), reverse=True)
        used_t, used_d, out = set(), set(), []
        for v, ti, di in pairs:
            if v < min_iou:
                break
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti); used_d.add(di); out.append((ti, di))
        return out, [i for i in range(len(tracks)) if i not in used_t], [i for i in range(len(dets)) if i not in used_d]

    def update(self, dets: list[Detection], ts: float) -> list[Track]:
        """Returns the confirmed tracks that were seen in this frame."""
        high = [d for d in dets if d.score >= self.high]
        low = [d for d in dets if self.low <= d.score < self.high]
        seen: set = set()

        m1, rest_t, rest_h = self._greedy(self.tracks, high, self.match_iou, ts)
        for ti, di in m1:
            self._apply(self.tracks[ti], high[di], ts); seen.add(ti)
        remaining = [self.tracks[i] for i in rest_t]
        m2, still_t, _ = self._greedy(remaining, low, self.match_iou, ts)       # weak detections only rescue existing tracks
        for ti, di in m2:
            self._apply(remaining[ti], low[di], ts); seen.add(self.tracks.index(remaining[ti]))
        for i in range(len(self.tracks)):
            if i not in seen:
                self.tracks[i].missed += 1
        for di in rest_h:                                                    # only confident leftovers start new tracks
            d = high[di]
            t = Track(next(self._ids), d.label, d.box, d.score, first_ts=ts, last_ts=ts)
            t.centers.append((ts, *t.center))
            t.confirmed = self.min_hits <= 1
            self.tracks.append(t)

        alive = []
        for t in self.tracks:
            if ts - t.last_ts > self.max_gap:
                if t.confirmed:
                    self.finished.append(t)
            else:
                alive.append(t)
        self.tracks = alive
        return [t for t in self.tracks if t.confirmed and t.missed == 0]

    def _apply(self, t: Track, d: Detection, ts: float) -> None:
        t.box, t.score, t.missed, t.hits, t.last_ts = d.box, d.score, 0, t.hits + 1, ts
        t.centers.append((ts, *t.center))
        if len(t.centers) > self.max_history:
            del t.centers[0]
        if t.hits >= self.min_hits:
            t.confirmed = True

    def drain_finished(self) -> list[Track]:
        out, self.finished = self.finished, []
        return out

    def flush(self) -> list[Track]:
        """End of stream: every confirmed track ends now."""
        self.finished += [t for t in self.tracks if t.confirmed]
        self.tracks = []
        return self.drain_finished()
