"""Traces the globe from the JAS Infotech logo file into a clean vector path (assets/jas_globe.svgpath).
The logo file is the customer's own brand artwork; the traced path is the same globe, redrawn as smooth curves.

    python3 brand/trace_globe.py /path/to/jas_logo.png

Needs: pip install potracer numpy pillow"""
import pathlib
import sys

import numpy as np
import potrace
from PIL import Image, ImageFilter

src = pathlib.Path(sys.argv[1])
im = Image.open(src).convert("RGBA")
a = np.array(im)
blue = (a[..., 3] > 128) & (a[..., 2] > 180) & (a[..., 0] < 80)
x1 = 312                                                       # the globe ends before the text starts (column gap at x 304 to 333)
ys, xs = np.where(blue[:, :x1])
box = (xs.min() - 2, ys.min() - 2, xs.max() + 3, ys.max() + 3)
print("globe box in the logo file:", box)
alpha = Image.fromarray(((blue[:, :x1]) * 255).astype("uint8")).crop(box)
K = 6                                                          # trace at 6x so curves are smooth
big = alpha.resize((alpha.width * K, alpha.height * K), Image.LANCZOS).filter(ImageFilter.GaussianBlur(K * 1.1))
bm = potrace.Bitmap(np.array(big) < 100)          # potrace traces the dark pixels, so invert: the globe must be the dark side
plist = bm.trace(turdsize=60, alphamax=1.3, opticurve=True, opttolerance=3.0)


def d_of(plist):
    out = []
    for curve in plist:
        s = curve.start_point
        out.append(f"M{s.x / K:.1f} {s.y / K:.1f}")
        for seg in curve.segments:
            if seg.is_corner:
                out.append(f"L{seg.c.x / K:.1f} {seg.c.y / K:.1f}L{seg.end_point.x / K:.1f} {seg.end_point.y / K:.1f}")
            else:
                out.append(f"C{seg.c1.x / K:.1f} {seg.c1.y / K:.1f} {seg.c2.x / K:.1f} {seg.c2.y / K:.1f} {seg.end_point.x / K:.1f} {seg.end_point.y / K:.1f}")
        out.append("Z")
    return "".join(out)


w, h = alpha.size
d = d_of(plist)
pathlib.Path(__file__).with_name("jas_globe_path.txt").write_text(f"{w} {h}\n{d}\n")
print(f"traced {len(plist)} shapes, path {len(d)} chars, size {w}x{h}")
