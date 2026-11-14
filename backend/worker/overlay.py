"""What the AI sees, drawn on the pictures of an alert clip.

Every picture that goes into a clip has what was found in it drawn on a copy: a box around each person (and each vehicle when vehicles are followed), the marked areas with
their names and the number of people in them (the outline turns orange while an area is at its limit), the counting line with its IN arrow, the plate zone, and the number of
people in view. Nothing about who anybody is: no names, no track numbers, no percentages. The picture the analysis works on is never changed; this draws on a copy."""
import cv2
import numpy as np

PEOPLE = (80, 200, 80)          # BGR
VEHICLE = (53, 198, 244)
AREA = (244, 198, 53)
OVER = (40, 140, 255)
LINE = (63, 210, 255)
WHITE = (255, 255, 255)


def _label(img, text: str, org: tuple, color, scale: float, th: int) -> None:
    (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, th)
    x, y = org
    cv2.rectangle(img, (x, y - tht - int(6 * scale)), (x + tw + int(8 * scale), y + int(4 * scale)), (0, 0, 0), -1)
    cv2.putText(img, text, (x + int(4 * scale), y), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, color, th)


def draw(frame, *, people=(), vehicles=(), areas=(), line=None, zone=None):
    """A copy of `frame` with what the AI found drawn on it.
    people, vehicles: tracks with a pixel `.box` (x1, y1, x2, y2).   areas: dicts with `points` (normalised 0..1), `name`, `count` (people inside, or None) and `over` (at its limit).
    line: ((x1, y1), (x2, y2), in_direction or None), normalised.   zone: the plate zone's points, normalised."""
    img = frame.copy()
    h, w = img.shape[:2]
    px = lambda p: (int(p[0] * w), int(p[1] * h))
    scale = max(1.0, w / 640)
    th = max(1, round(scale))
    if zone:
        cv2.polylines(img, [np.array([px(p) for p in zone], np.int32)], True, AREA, th)
    for a in areas:
        pts = np.array([px(p) for p in a["points"]], np.int32)
        cv2.polylines(img, [pts], True, OVER if a.get("over") else AREA, th + 1)
        text = a["name"] if a.get("count") is None else f"{a['name']}: {a['count']}"
        _label(img, text[:40], (int(pts[:, 0].min()), max(int(18 * scale), int(pts[:, 1].min()) - int(2 * scale))), WHITE, scale, th)
    if line:
        p1, p2, d = line
        cv2.line(img, px(p1), px(p2), LINE, th + 1)
        if d:
            m = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
            cv2.arrowedLine(img, px(m), px((m[0] + d[0] * 0.08, m[1] + d[1] * 0.08)), WHITE, th + 1, tipLength=0.35)
            cv2.putText(img, "IN", px((m[0] + d[0] * 0.1, m[1] + d[1] * 0.1)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, WHITE, th)
    for t in people:
        x1, y1, x2, y2 = (int(v) for v in t.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), PEOPLE, th)
    for t in vehicles:
        x1, y1, x2, y2 = (int(v) for v in t.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), VEHICLE, th)
    n = len(people)
    _label(img, f"People in view: {n}", (int(6 * scale), int(18 * scale)), WHITE, scale, th)
    return img
