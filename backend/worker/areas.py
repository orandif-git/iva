"""Marked areas: how many people are in each one, how long the longest-waiting person has been there, and whether a phone is in use.

The worker only measures. The limits live on the area at the backend, and the backend decides whether anything is worth an alert, so a customer who changes
a limit does not have to wait for a restart. What is measured, every `every` seconds per area:

  area_count   people     the number of followed people whose floor point is inside the shape
  queue_area   people     the same, plus wait_seconds: the longest time a person still in the shape has been in it
  staff_area   people     the same, plus phone_seconds: how long a phone has been in use inside the shape without a break (a break of `grace` seconds ends it)

A phone counts as in use when the detector sees a phone whose centre is inside the upper part of a person's box, that person being inside the shape. It is a
guess from a small object in a picture, so the alert says "possible". A picture is taken once a phone has been in use for the area's `seconds`, so the alert
can show what was seen. The monitor never identifies anyone: it counts and times, and names the area."""
import logging
from dataclasses import dataclass, field
from datetime import datetime

import cv2

from .geometry import point_in_polygon

log = logging.getLogger("worker.areas")
UPPER = 0.6           # the part of a person's box, from the top, where a phone in a hand or at the ear can be. Lower than this is counter height: a phone lying there is not in use


@dataclass(frozen=True)
class AreaConfig:
    id: str
    kind: str
    name: str
    points: tuple            # ((x, y), ...) normalised 0..1
    params: tuple = ()       # sorted (key, value) pairs, so two equal configurations compare equal

    @classmethod
    def from_backend(cls, a: dict) -> "AreaConfig":
        return cls(str(a["id"]), a["kind"], a.get("name") or "area", tuple(tuple(p) for p in a["points"]), tuple(sorted((a.get("params") or {}).items())))

    def param(self, key, default=None):
        return dict(self.params).get(key, default)


@dataclass
class _State:
    entered: dict = field(default_factory=dict)       # track id -> when it was first seen inside (queue)
    gone: dict = field(default_factory=dict)          # track id -> when it was last seen inside
    phone_since: float | None = None
    phone_last: float = 0.0
    sent_at: float = -1e9                             # when this area last reported
    shot_for: float | None = None                     # the phone episode a picture was already taken for
    over_since: float | None = None                   # since when the queue or the crowd has been at or above its limit
    shot_at: float | None = None                      # when a picture was last taken during that


class AreaMonitor:
    def __init__(self, areas: list[AreaConfig], every: float = 10.0, grace: float = 5.0, leave_after: float = 8.0, warmup: float = 0.0):
        self.areas = list(areas)
        self.every, self.grace, self.leave_after, self.warmup = every, grace, leave_after, warmup
        self.started: float | None = None           # nobody is confirmed in the first moments, so the first report waits `warmup` seconds
        self.state = {a.id: _State() for a in self.areas}

    @staticmethod
    def _point(tr, w: int, h: int, kind: str):
        x1, y1, x2, y2 = tr.box
        return (((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h) if kind == "staff_area" else (((x1 + x2) / 2) / w, y2 / h)     # a person at a counter is hidden below the waist: use the middle

    @staticmethod
    def phone_in_use(person_box, phones) -> bool:
        x1, y1, x2, y2 = person_box
        top = y1 + (y2 - y1) * UPPER
        return any(x1 <= (p.box[0] + p.box[2]) / 2 <= x2 and y1 <= (p.box[1] + p.box[3]) / 2 <= top for p in phones)

    def update(self, ts: float, w: int, h: int, people: list, phones: list) -> list[dict]:
        """`people` are confirmed tracks, `phones` are detections. Returns the reports that are due now (each: area, people, wait_seconds, phone_seconds, take_picture, inside)."""
        out = []
        if self.started is None:
            self.started = ts
        for a in self.areas:
            st = self.state[a.id]
            inside = [t for t in people if point_in_polygon(self._point(t, w, h, a.kind), a.points)]
            n = len(inside)
            wait = phone = None
            if a.kind == "queue_area":
                for t in inside:
                    st.entered.setdefault(t.id, ts)
                    st.gone.pop(t.id, None)
                for tid in list(st.entered):
                    if tid not in {t.id for t in inside}:                       # let a person be missed for a moment without starting their wait again
                        st.gone.setdefault(tid, ts)
                        if ts - st.gone[tid] > self.leave_after:
                            st.entered.pop(tid, None); st.gone.pop(tid, None)
                wait = max((ts - st.entered[t.id] for t in inside), default=0.0)
            if a.kind == "staff_area":
                using = any(self.phone_in_use(t.box, phones) for t in inside)
                if using:
                    st.phone_last = ts
                    if st.phone_since is None:
                        st.phone_since = ts
                elif st.phone_since is not None and ts - st.phone_last > self.grace:
                    st.phone_since, st.shot_for = None, None                  # the phone is away: the next time starts again
                phone = (ts - st.phone_since) if st.phone_since is not None else 0.0
            if ts - st.sent_at < self.every or ts - self.started < self.warmup:
                continue
            st.sent_at = ts
            picture = bool(a.kind == "staff_area" and phone and phone >= float(a.param("seconds", 20)) and st.shot_for != st.phone_since)
            if picture:
                st.shot_for = st.phone_since
            # A queue or a crowd at its limit: a picture when it first gets there, and another every `picture_minutes` (the customer sets it) while it stays,
            # so the alert always has a recent one to show.
            limit = int(a.param("length", 5)) if a.kind == "queue_area" else (int(a.param("limit", 0)) if a.kind == "area_count" else 0)
            every_min = int(a.param("picture_minutes", 15))                  # the customer's choice: how often to take another; 0 means no pictures at all
            if limit >= 1 and n >= limit:
                if st.over_since is None:
                    st.over_since = ts
                if every_min > 0 and (st.shot_at is None or ts - st.shot_at >= every_min * 60):
                    picture, st.shot_at = True, ts
            elif limit >= 1:
                st.over_since = st.shot_at = None                       # back under the limit: the next time starts again
            out.append({"area": a, "people": n, "wait_seconds": wait, "phone_seconds": phone, "take_picture": picture, "inside": inside})
        return out

    def forget(self) -> None:
        """The picture was interrupted: nobody can be followed across the gap."""
        self.state = {a.id: _State() for a in self.areas}
        self.started = None


def picture(frame, area: AreaConfig, people: list, phones: list) -> bytes | None:
    """The evidence picture: the area, the people inside it and the phone. Nothing about who the person is."""
    import numpy as np
    img = frame.copy()
    h, w = img.shape[:2]
    scale = max(1.0, w / 640)
    th = max(1, round(scale))
    cv2.polylines(img, [np.array([(int(x * w), int(y * h)) for x, y in area.points], np.int32)], True, (244, 198, 53), th + 1)
    for t in people:
        cv2.rectangle(img, tuple(int(c) for c in t.box[:2]), tuple(int(c) for c in t.box[2:]), (80, 200, 80), th)
    for p in phones:
        cv2.rectangle(img, tuple(int(c) for c in p.box[:2]), tuple(int(c) for c in p.box[2:]), (60, 60, 255), th + 1)
    cv2.putText(img, area.name, (6, int(18 * scale)), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (255, 255, 255), th)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None
