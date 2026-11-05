"""Synthetic video and stand-in detectors for testing the worker's logic deterministically.

People are red rectangles, vehicles are blue rectangles with a white plate patch. ColorDetector finds them again by colour.
This tests tracking, counting, voting and delivery. It says nothing about how well the real neural networks see."""
import glob

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from worker.tracker import Detection

W, H = 640, 360
FONT = (glob.glob("/usr/share/fonts/**/DejaVuSansMono-Bold.ttf", recursive=True) or [None])[0]


def plate_patch(text: str, w=200, h=52) -> np.ndarray:
    im = Image.new("RGB", (w, h), (245, 245, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([1, 1, w - 2, h - 2], outline=(20, 20, 20), width=3)
    f = ImageFont.truetype(FONT, 34) if FONT else ImageFont.load_default()
    d.text(((w - d.textlength(text, font=f)) / 2, 6), text, fill=(15, 15, 15), font=f)
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def draw(frame, persons=(), vehicles=()):
    for x, y in persons:                                         # centre of a 30x70 person
        cv2.rectangle(frame, (int(x - 15), int(y - 35)), (int(x + 15), int(y + 35)), (0, 0, 255), -1)
    for x, y, text in vehicles:                                  # centre of a 240x130 vehicle
        cv2.rectangle(frame, (int(x - 120), int(y - 65)), (int(x + 120), int(y + 65)), (255, 0, 0), -1)
        if text:
            p = plate_patch(text, 150, 40)
            px, py = int(x - 75), int(y + 15)
            frame[py:py + 40, px:px + 150] = p


def make_video(path, seconds: float, scene, fps: int = 25):
    """scene(t) -> (persons, vehicles) in pixels for time t seconds."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for i in range(int(seconds * fps)):
        f = np.full((H, W, 3), 110, np.uint8)
        persons, vehicles = scene(i / fps)
        draw(f, persons, vehicles)
        vw.write(f)
    vw.release()
    return path


def walk(x, y0, y1, t0, t1):
    """A function t -> (x, y) walking vertically from y0 to y1 between t0 and t1 (waits at the ends)."""
    def at(t):
        k = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return (x, y0 + (y1 - y0) * k)
    return at


class ColorDetector:
    """Finds the red and blue rectangles from make_video."""
    def __init__(self):
        self.calls = 0
        self.last_ms = 0.0

    def detect(self, frame):
        self.calls += 1
        out = []
        for lo, hi, label, min_area in (((0, 0, 200), (60, 60, 255), "person", 600), ((200, 0, 0), (255, 60, 60), "car", 5000)):
            mask = cv2.inRange(frame, np.array(lo), np.array(hi))
            for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                x, y, w, h = cv2.boundingRect(c)
                if w * h >= min_area:
                    out.append(Detection((float(x), float(y), float(x + w), float(y + h)), 0.9, label))
        return out


class ScriptedReader:
    """Plate reader that returns a prepared list of reads, one per call, then repeats the last one."""
    def __init__(self, reads, conf=0.9):
        self.reads, self.conf, self.i, self.last_ms = reads, conf, 0, 0.0

    def read(self, image):
        from worker.plates import PlateRead
        text = self.reads[min(self.i, len(self.reads) - 1)]
        self.i += 1
        return [PlateRead(text, self.conf, (10, 10, 60, 30))] if text else []
