"""Rendering of Indian-style plates with blur, noise and perspective, to exercise the real plate models without real photographs.
Rendered plates in one font are easier than real ones. Treat results as a sanity check, not as accuracy figures."""
import glob
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = (glob.glob("/usr/share/fonts/**/DejaVuSansMono-Bold.ttf", recursive=True) or [None])[0]
STATES = ["GJ", "MH", "KA", "TN", "DL", "RJ", "UP", "HR", "AP", "KL"]


def rand_plate(rng: random.Random) -> str:
    series = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXY") for _ in range(rng.choice([1, 2])))
    return f"{rng.choice(STATES)}{rng.randint(1, 60):02d}{series}{rng.randint(1000, 9999)}"


def plate_image(text: str, w=420, h=110) -> np.ndarray:
    im = Image.new("RGB", (w, h), (245, 245, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, w - 3, h - 3], outline=(20, 20, 20), width=4)
    f = ImageFont.truetype(FONT, 68)
    d.text(((w - d.textlength(text, font=f)) / 2, 16), text, fill=(15, 15, 15), font=f)
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def degrade(img, rng: random.Random, nprng: np.random.Generator) -> np.ndarray:
    h, w = img.shape[:2]
    j = lambda m: rng.uniform(-m, m)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + np.float32([[j(w * .05), j(h * .1)] for _ in range(4)])
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(200, 200, 200))
    out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.5, 1.6))
    return np.clip(out.astype(np.float32) + nprng.normal(0, 8, out.shape), 0, 255).astype(np.uint8)


def car_scene(text: str, rng: random.Random, nprng: np.random.Generator, size=(480, 640)) -> np.ndarray:
    s = np.full((*size, 3), 90, np.uint8)
    cv2.rectangle(s, (120, 80), (520, 440), (60, 70, 120), -1)
    p = degrade(plate_image(text), rng, nprng)
    pw = rng.randint(190, 260)
    p = cv2.resize(p, (pw, int(pw * p.shape[0] / p.shape[1])))
    x, y = rng.randint(150, 250), rng.randint(300, 340)
    s[y:y + p.shape[0], x:x + pw] = p
    return s
