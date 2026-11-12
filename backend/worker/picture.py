"""What one camera picture says about the camera itself, not about the people and vehicles in it: how bright and sharp it is, and a small sketch of what it looks at.

The worker sends one of these every few minutes. The backend keeps each camera's own usual view and tells the customer when the camera has been moved, turned or covered
(the sketch no longer matches, or the picture is a flat colour), or when the picture has stayed too dark or too blurry. Nothing about any person is kept: the sketch is
only the outlines in the picture, 64 by 36 points, as bits."""
import base64

import cv2
import numpy as np

SKETCH_W, SKETCH_H = 64, 36              # 2304 points: 288 bytes
EVERY_SECONDS = 300.0


def measure(frame) -> dict:
    """{brightness, contrast, sharpness, edges}: brightness and contrast on a 0 to 255 scale, sharpness the variance of the picture's edges at a fixed size (so it does not
    depend on the camera's resolution), and edges the outline sketch as base64."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    small = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
    sharp = float(cv2.Laplacian(small, cv2.CV_64F).var())
    tiny = cv2.resize(cv2.GaussianBlur(small, (5, 5), 0), (SKETCH_W, SKETCH_H), interpolation=cv2.INTER_AREA)
    edges = cv2.dilate(cv2.Canny(tiny, 30, 90), np.ones((3, 3), np.uint8))          # a little thick, so a shift of a point or two still matches
    bits = np.packbits((edges > 0).flatten())
    return {"brightness": round(float(small.mean()), 1), "contrast": round(float(small.std()), 1), "sharpness": round(sharp, 2), "edges": base64.b64encode(bits.tobytes()).decode()}
