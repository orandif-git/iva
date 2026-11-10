"""One camera's analysis: detect, track, count people across a line, and read plates of vehicles in a zone."""
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2

from .areas import AreaConfig, AreaMonitor, picture
from .footfall import LineCounter
from .geometry import point_in_polygon
from .plates import vote
from .sink import iso
from .tracker import Tracker

log = logging.getLogger("worker.pipeline")
VEHICLE_LABELS = {"car", "motorcycle", "bus", "truck"}


@dataclass
class CameraConfig:
    camera_id: str
    name: str = "camera"
    use_footfall: bool = False
    use_anpr: bool = False
    line: tuple | None = None          # (a, b, in_direction or None), normalised 0..1
    zone: list | None = None           # plate zone polygon, normalised. None means the whole picture
    zone_direction: tuple | None = None
    clips: bool = False                # keep recent pictures so a short video can be made when an alert asks for one
    areas: tuple = ()                  # marked areas (AreaConfig): people in an area, queues, phone use in a staff area

    @classmethod
    def from_backend(cls, j: dict) -> "CameraConfig":
        line = zone = zdir = None
        for z in j.get("zones", []):
            if z["kind"] == "count_line" and len(z["points"]) == 2:
                line = (tuple(z["points"][0]), tuple(z["points"][1]), tuple(z["in_direction"]) if z.get("in_direction") else None)
            elif z["kind"] == "anpr_zone" and len(z["points"]) >= 3:
                zone, zdir = [tuple(p) for p in z["points"]], (tuple(z["in_direction"]) if z.get("in_direction") else None)
        areas = tuple(AreaConfig.from_backend(a) for a in j.get("areas", []) if len(a.get("points", [])) >= 3)
        return cls(j["camera_id"], j.get("name", "camera"), bool(j.get("use_footfall")), bool(j.get("use_anpr")), line, zone, zdir, bool(j.get("clips")), areas)


@dataclass
class Observation:
    """What one processed frame showed, for the adaptive rate controller."""
    relevant: bool = False      # a person (when counting) or a vehicle (when reading plates) is in view
    tracks: int = 0             # people and vehicles currently being followed
    visits: int = 0             # vehicle visits still open


@dataclass
class Visit:
    """One vehicle's time in the plate zone."""
    track_id: int
    label: str
    path: list = field(default_factory=list)            # [(x, y)] normalised
    reads: list = field(default_factory=list)           # [(text, confidence)]
    attempts: int = 0
    best_conf: float = -1.0
    best_ts: datetime | None = None
    last_read: float = -1e9
    snapshot: bytes | None = None


class CameraPipeline:
    def __init__(self, cfg: CameraConfig, detector, plate_reader, sink, *, snapshot_dir=None, run_id: str = "run", anchor: str = "center",
                 min_read_interval: float = 0.35, max_reads: int = 6, min_vehicle_area: float = 0.02, ffmpeg: str = "ffmpeg"):
        self.cfg, self.detector, self.reader, self.sink = cfg, detector, plate_reader, sink
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self.run_id, self.anchor = run_id, anchor
        # Plate reads are spaced by time, not by frame count, so the behaviour is the same at 2, 5 or 25 frames per second.
        self.min_read_interval, self.max_reads, self.min_area = min_read_interval, max_reads, min_vehicle_area
        self.people = Tracker()
        self.vehicles = Tracker(high=0.4)
        self.counter = LineCounter(*cfg.line) if (cfg.use_footfall and cfg.line) else None
        self.areas = AreaMonitor(list(cfg.areas), warmup=3.0) if cfg.areas else None
        self.minutes: dict = {}
        self.visits: dict[int, Visit] = {}
        self.clips = None
        if cfg.clips and cfg.use_anpr:
            from .clips import ClipRecorder
            self.clips = ClipRecorder(sink, ffmpeg=ffmpeg)
            if hasattr(sink, "clip_handler"):
                sink.clip_handler = self.clips.request
        self.frame_no = 0
        self.totals = {"in": 0, "out": 0}
        self.stats = {"frames": 0, "detect_ms": 0.0, "plate_ms": 0.0, "plate_reads": 0, "plates_emitted": 0, "skipped_plates": 0}

    # ---------------------------------------------------------------------- frames
    def process(self, frame, ts: datetime) -> Observation:
        self.frame_no += 1
        self.stats["frames"] += 1
        if self.clips is not None:
            self.clips.add(frame, ts)
        h, w = frame.shape[:2]
        if not (self.counter or self.areas or (self.cfg.use_anpr and self.reader)):
            return Observation()
        dets = self.detector.detect(frame)
        self.stats["detect_ms"] += getattr(self.detector, "last_ms", 0.0)
        relevant = any((d.label == "person" and (self.counter or self.areas)) or (d.label in VEHICLE_LABELS and self.cfg.use_anpr and self.reader) for d in dets)

        if self.counter or self.areas:
            for tr in self.people.update([d for d in dets if d.label == "person"], ts.timestamp()):
                if self.counter:
                    x, y = tr.center if self.anchor == "center" else ((tr.box[0] + tr.box[2]) / 2, tr.box[3])
                    event = self.counter.update(tr.id, (x / w, y / h))
                    if event:
                        self._count(ts, event)
            for tr in self.people.drain_finished():
                if self.counter:
                    self.counter.forget(tr.id)
        if self.areas:
            self._areas(frame, ts, dets, w, h)

        if self.cfg.use_anpr and self.reader:
            for tr in self.vehicles.update([d for d in dets if d.label in VEHICLE_LABELS], ts.timestamp()):
                self._vehicle(tr, frame, ts)
            for tr in self.vehicles.drain_finished():
                self._emit(tr.id)
        return Observation(relevant, len(self.people.tracks) + len(self.vehicles.tracks), len(self.visits))

    def _areas(self, frame, ts: datetime, dets: list, w: int, h: int) -> None:
        people = [t for t in self.people.tracks if t.confirmed]
        phones = [d for d in dets if d.label == "cell phone"]
        for r in self.areas.update(ts.timestamp(), w, h, people, phones):
            a = r["area"]
            sample = {"zone_id": a.id, "at": iso(ts), "people": r["people"], "wait_seconds": r["wait_seconds"], "phone_seconds": r["phone_seconds"]}
            self.sink.add_zone(self.cfg.camera_id, sample, picture(frame, a, r["inside"], phones) if r["take_picture"] else None)
            self.stats["area_reports"] = self.stats.get("area_reports", 0) + 1

    def annotate(self, frame, fps: float | None = None) -> bytes | None:
        """The picture the detector just looked at, with what it found drawn on it: boxes with track numbers, the counting line with its
        IN direction, the plate zone, and the running count. Called only while someone is looking, so it costs nothing otherwise."""
        import numpy as np
        img = frame.copy()
        h, w = img.shape[:2]
        px = lambda p: (int(p[0] * w), int(p[1] * h))
        scale = max(1.0, w / 640)
        th = max(1, round(scale))
        if self.cfg.zone:
            cv2.polylines(img, [np.array([px(p) for p in self.cfg.zone], np.int32)], True, (244, 198, 53), th + 1)
        if self.cfg.line:
            a, b, d = self.cfg.line
            cv2.line(img, px(a), px(b), (63, 210, 255), th + 1)
            if d:
                m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                cv2.arrowedLine(img, px(m), px((m[0] + d[0] * 0.08, m[1] + d[1] * 0.08)), (255, 255, 255), th + 1, tipLength=0.35)
                cv2.putText(img, "IN", px((m[0] + d[0] * 0.1, m[1] + d[1] * 0.1)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), th)
        for tr, color, tag in [(t, (80, 200, 80), "") for t in self.people.tracks] + [(t, (244, 198, 53), t.label + " ") for t in self.vehicles.tracks]:
            x1, y1, x2, y2 = (int(v) for v in tr.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), color if tr.confirmed else (160, 160, 160), th)
            cv2.putText(img, f"{tag}#{tr.id} {tr.score:.0%}", (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.42 * scale, color, th)
        bar = f"people in view {len(self.people.tracks)}"
        if self.counter:
            bar += f"   IN {self.totals['in']}  OUT {self.totals['out']}"
        if fps:
            bar += f"   analysing {fps:.1f} fps"
        cv2.rectangle(img, (0, 0), (w, int(22 * scale)), (0, 0, 0), -1)
        cv2.putText(img, bar, (6, int(16 * scale)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), th)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        return buf.tobytes() if ok else None

    def reset(self) -> None:
        """The picture was interrupted (a reconnect or a settings change). Nobody can be followed across the gap, so close every open
        vehicle visit and forget every track. Counting then carries on as if new."""
        self.finish()

    async def drain_clips(self, timeout: float = 30.0) -> None:
        """Wait for clips that are being made. Call after the last plate batch has been sent."""
        if self.clips is not None:
            await self.clips.drain(timeout)

    def finish(self) -> None:
        """End of the stream: close every open vehicle visit and send what is pending."""
        for tr in self.people.flush():
            if self.counter:
                self.counter.forget(tr.id)
        for tr in self.vehicles.flush():
            self._emit(tr.id)
        for tid in list(self.visits):
            self._emit(tid)
        if self.areas:
            self.areas.forget()

    # --------------------------------------------------------------------- people
    def _count(self, ts: datetime, direction: str) -> None:
        minute = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
        m = self.minutes.setdefault(minute, [0, 0])
        m[0 if direction == "in" else 1] += 1
        self.totals[direction] += 1
        self.sink.set_footfall(self.cfg.camera_id, minute, m[0], m[1])

    # ------------------------------------------------------------------- vehicles
    def _vehicle(self, tr, frame, ts: datetime) -> None:
        h, w = frame.shape[:2]
        pos = (tr.center[0] / w, tr.center[1] / h)
        if self.cfg.zone and not point_in_polygon(pos, self.cfg.zone):
            return
        v = self.visits.setdefault(tr.id, Visit(tr.id, tr.label))
        v.path.append(pos)
        area = (tr.box[2] - tr.box[0]) * (tr.box[3] - tr.box[1]) / (w * h)
        t_now = ts.timestamp()
        if v.attempts >= self.max_reads or t_now - v.last_read < self.min_read_interval or area < self.min_area:
            return
        v.last_read = t_now
        pad_x, pad_y = (tr.box[2] - tr.box[0]) * 0.05, (tr.box[3] - tr.box[1]) * 0.05
        x1, y1 = max(0, int(tr.box[0] - pad_x)), max(0, int(tr.box[1] - pad_y))
        x2, y2 = min(w, int(tr.box[2] + pad_x)), min(h, int(tr.box[3] + pad_y))
        reads = self.reader.read(frame[y1:y2, x1:x2])
        v.attempts += 1
        self.stats["plate_ms"] += getattr(self.reader, "last_ms", 0.0)
        self.stats["plate_reads"] += 1
        if not reads:
            return
        best = max(reads, key=lambda r: r.confidence)
        v.reads.append((best.text, best.confidence))
        if best.confidence > v.best_conf:
            v.best_conf, v.best_ts = best.confidence, ts
            v.snapshot = self._snapshot(frame, tr.box, best.box, (x1, y1), best.text)

    @staticmethod
    def _snapshot(frame, vbox, pbox, origin, text) -> bytes | None:
        img = frame.copy()
        cv2.rectangle(img, tuple(int(c) for c in vbox[:2]), tuple(int(c) for c in vbox[2:]), (80, 220, 80), 2)
        px1, py1, px2, py2 = pbox[0] + origin[0], pbox[1] + origin[1], pbox[2] + origin[0], pbox[3] + origin[1]
        cv2.rectangle(img, (px1, py1), (px2, py2), (60, 60, 255), 2)
        cv2.putText(img, text, (px1, max(15, py1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def _emit(self, track_id: int) -> None:
        v = self.visits.pop(track_id, None)
        if not v or not v.reads:
            return
        text, conf = vote(v.reads)
        if not text or len(text) > 20:                       # not plausible as a plate (the backend limit is 20)
            self.stats["skipped_plates"] += 1
            return
        direction = None
        zdir = self.cfg.zone_direction
        if zdir and len(v.path) >= 2:
            dx, dy = v.path[-1][0] - v.path[0][0], v.path[-1][1] - v.path[0][1]
            if math.hypot(dx, dy) >= 0.05:                   # it really moved; a parked vehicle has no direction
                direction = "in" if dx * zdir[0] + dy * zdir[1] > 0 else "out"
        key = self._save_snapshot(v, track_id)
        self.sink.add_plate(self.cfg.camera_id, {
            "seen_at": iso(v.best_ts), "track_id": f"{self.run_id}-{track_id}", "plate_raw": text, "confidence": max(0, min(100, round(conf * 100))),
            "vehicle_type": v.label, "direction": direction, "snapshot_key": key}, snapshot=v.snapshot)
        self.stats["plates_emitted"] += 1

    def _save_snapshot(self, v: Visit, track_id: int) -> str | None:
        if not (self.snapshot_dir and v.snapshot and v.best_ts):
            return None
        key = f"{self.cfg.camera_id}/{v.best_ts.strftime('%Y%m%d')}/{self.run_id}-{track_id}.jpg"
        path = self.snapshot_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(v.snapshot)
        return key
