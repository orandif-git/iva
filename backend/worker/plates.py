"""Reading number plates: find the plate inside a vehicle picture, read it, and combine many noisy reads into one answer."""
import time
from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass
class PlateRead:
    text: str            # upper-case letters and digits only
    confidence: float    # 0..1, from the OCR model
    box: tuple           # x1, y1, x2, y2 inside the image that was searched


def _clean(s: str) -> str:
    return "".join(ch for ch in s.upper() if ch.isalnum())


class OnnxPlateReader:
    """Plate detector (open-image-models, MIT) followed by an OCR model (fast-plate-ocr, MIT), both on ONNX Runtime / CPU.

    The detector's box is tight and often clips the first or last character. Padding the crop by 10% before reading raised exact
    matches from 15 to 34 out of 38 in a test on rendered plates, so it is not optional."""

    def __init__(self, detector_model="yolo-v9-t-416-license-plate-end2end", ocr_model="cct-xs-v2-global-model", pad: float = 0.10,
                 min_detect_conf: float = 0.4):
        from fast_plate_ocr import LicensePlateRecognizer
        from open_image_models import create_detector
        self.detector = create_detector(detector_model, providers=["CPUExecutionProvider"])
        self.ocr = LicensePlateRecognizer(ocr_model, device="cpu")
        self.pad, self.min_detect_conf = pad, min_detect_conf
        self.last_ms = 0.0

    def read(self, image: np.ndarray) -> list[PlateRead]:
        t0 = time.perf_counter()
        out = []
        h, w = image.shape[:2]
        for r in self.detector.predict(image):
            if r.confidence < self.min_detect_conf:
                continue
            b = r.bounding_box
            bw, bh = b.x2 - b.x1, b.y2 - b.y1
            x1, x2 = max(0, int(b.x1 - bw * self.pad)), min(w, int(b.x2 + bw * self.pad))
            y1, y2 = max(0, int(b.y1 - bh * self.pad * 1.2)), min(h, int(b.y2 + bh * self.pad * 1.2))
            if x2 - x1 < 8 or y2 - y1 < 4:
                continue
            pred = self.ocr.run(image[y1:y2, x1:x2], return_confidence=True)[0]
            text = _clean(pred.plate)
            if not text:
                continue
            probs = pred.char_probs
            conf = 0.0
            if probs is not None:
                p = np.asarray(probs)
                p = p.max(-1) if p.ndim > 1 else p
                conf = float(np.mean(p[: len(pred.plate)])) if len(pred.plate) else 0.0
            out.append(PlateRead(text, conf, (int(b.x1), int(b.y1), int(b.x2), int(b.y2))))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return out


def vote(reads: list[tuple[str, float]]) -> tuple[str, float]:
    """Combine reads of the same plate (text, confidence) into one answer.

    Independent mistakes do not agree with each other, so: pick the most likely length (by total confidence), then choose each
    character position separately by weighted vote among reads of that length. Returns (text, confidence 0..1) where confidence is
    the average read confidence scaled by how strongly the reads agreed."""
    if not reads:
        return "", 0.0
    by_len: dict = defaultdict(float)
    for t, c in reads:
        by_len[len(t)] += c
    n = max(by_len, key=lambda k: (by_len[k], k))
    same = [(t, c) for t, c in reads if len(t) == n]
    chars, agree = [], []
    for i in range(n):
        w: dict = defaultdict(float)
        for t, c in same:
            w[t[i]] += c
        best = max(w, key=lambda ch: (w[ch], ch))
        chars.append(best)
        agree.append(w[best] / sum(w.values()))
    mean_conf = sum(c for _, c in same) / len(same)
    return "".join(chars), mean_conf * (sum(agree) / n)
