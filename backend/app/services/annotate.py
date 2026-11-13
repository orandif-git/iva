"""Evidence pictures for the alerts that are raised from counts (occupancy limit, after-hours activity).

Those pictures are plain camera grabs taken by the API. This draws on them what the AI sees, the same way the worker does for area and plate alerts: a green box on
each person, the counting line with its IN arrow, and a caption with the number of people in view. It never names anyone. The detector is loaded the first time it is
needed and runs once per alert (alerts are rare), on a thread so the API keeps answering. If anything goes wrong the plain picture is used: an alert never waits for this."""
import asyncio
import logging

import cv2
import numpy as np

log = logging.getLogger("annotate")
_detector = None
_lock = asyncio.Lock()


def _load():
    global _detector
    if _detector is None:
        from worker.config import WorkerSettings
        from worker.detector import YoloxDetector, PERSON
        s = WorkerSettings()
        _detector = YoloxDetector(s.model_dir / f"{s.detector_model}.onnx", threads=1, classes=PERSON)
    return _detector


def draw(jpeg: bytes, boxes: list, line=None, caption: str = "") -> bytes | None:
    """`boxes`: pixel boxes (x1, y1, x2, y2). `line`: ((x1, y1), (x2, y2), in_direction or None), all normalised 0..1."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = max(1.0, w / 640)
    th = max(1, round(scale))
    px = lambda p: (int(p[0] * w), int(p[1] * h))
    if line:
        a, b, d = line
        cv2.line(img, px(a), px(b), (63, 210, 255), th + 1)
        if d:
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            cv2.arrowedLine(img, px(m), px((m[0] + d[0] * 0.08, m[1] + d[1] * 0.08)), (255, 255, 255), th + 1, tipLength=0.35)
            cv2.putText(img, "IN", px((m[0] + d[0] * 0.1, m[1] + d[1] * 0.1)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), th)
    for x1, y1, x2, y2 in boxes:
        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (80, 200, 80), th)
    if caption:
        cv2.putText(img, caption, (6, int(18 * scale)), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (255, 255, 255), th)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


def _run(jpeg: bytes, line, caption: str) -> bytes | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    people = [d.box for d in _load().detect(img)]
    return draw(jpeg, people, line, f"{caption} {len(people)} in view".strip())


async def people_picture(jpeg: bytes, line=None, caption: str = "") -> bytes:
    """The picture with the people in it boxed. The plain picture when the detector is not available or fails."""
    try:
        async with _lock:                                          # one at a time: the detector is loaded once and the server stays calm
            out = await asyncio.wait_for(asyncio.to_thread(_run, jpeg, line, caption), 30)
        return out or jpeg
    except Exception as e:
        log.info("picture left plain: %s", type(e).__name__)
        return jpeg


async def count_line(conn, camera_id):
    """The camera's counting line, as the worker uses it (None when none is drawn)."""
    z = await conn.fetchrow("SELECT points, in_direction FROM app.camera_zones WHERE camera_id=$1 AND kind::text='count_line'", camera_id)
    if not z or len(z["points"]) != 2:
        return None
    return (tuple(z["points"][0]), tuple(z["points"][1]), tuple(z["in_direction"]) if z["in_direction"] else None)
