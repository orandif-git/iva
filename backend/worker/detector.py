"""Object detection with YOLOX (Apache-2.0) run through ONNX Runtime on the CPU.

YOLOX's ONNX output is raw: for every candidate it gives offsets relative to a grid cell, so the box centres and sizes
must be decoded with each cell's position and stride before use."""
import os
import time

import cv2
import numpy as np

from .tracker import Detection

# COCO class ids that matter here. Everything else the model can see is ignored.
PERSON = {0: "person"}
VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def _nms(boxes: np.ndarray, scores: np.ndarray, thresh: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0]); yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2]); yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
        iou = inter / (area(boxes[i]) + area(boxes[rest]) - inter + 1e-9)
        order = rest[iou <= thresh]
    return keep


def _decode(raw: np.ndarray, size: int) -> np.ndarray:
    """raw: (N, 85) straight from the network. Returns centre x, centre y, width, height in input-image pixels, then scores."""
    grids, strides = [], []
    for s in (8, 16, 32):
        n = size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), s))
    grid, stride = np.concatenate(grids), np.concatenate(strides)
    out = raw.copy()
    out[:, :2] = (out[:, :2] + grid) * stride
    out[:, 2:4] = np.exp(out[:, 2:4]) * stride
    return out


class YoloxDetector:
    def __init__(self, model_path: str | os.PathLike, conf: float = 0.25, nms_iou: float = 0.45, threads: int = 2,
                 classes: dict | None = None):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.size = int(self.session.get_inputs()[0].shape[2])
        self.conf, self.nms_iou = conf, nms_iou
        self.classes = classes or {**PERSON, **VEHICLES}
        self.last_ms = 0.0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        r = min(self.size / h, self.size / w)
        nh, nw = int(h * r), int(w * r)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)               # grey padding keeps the aspect ratio
        canvas[:nh, :nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)               # BGR, 0..255, no scaling: what YOLOX expects
        raw = self.session.run(None, {self.input_name: blob})[0][0]
        pred = _decode(raw, self.size)

        cls_scores = pred[:, 5:] * pred[:, 4:5]
        cls_ids = cls_scores.argmax(1)
        scores = cls_scores[np.arange(len(pred)), cls_ids]
        keep_mask = (scores >= self.conf) & np.isin(cls_ids, list(self.classes))
        pred, scores, cls_ids = pred[keep_mask], scores[keep_mask], cls_ids[keep_mask]
        if not len(pred):
            self.last_ms = (time.perf_counter() - t0) * 1000
            return []
        boxes = np.stack([pred[:, 0] - pred[:, 2] / 2, pred[:, 1] - pred[:, 3] / 2, pred[:, 0] + pred[:, 2] / 2, pred[:, 1] + pred[:, 3] / 2], 1) / r
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
        out = []
        for c in np.unique(cls_ids):                                             # suppress overlaps within each class only
            idx = np.where(cls_ids == c)[0]
            for k in _nms(boxes[idx], scores[idx], self.nms_iou):
                i = idx[k]
                out.append(Detection(tuple(float(v) for v in boxes[i]), float(scores[i]), self.classes[int(c)]))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return out
