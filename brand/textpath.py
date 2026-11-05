"""Turns text into SVG outline paths (so a logo looks the same everywhere without any font installed)."""
import re

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


def text_path(font_file: str, text: str, size: float, x: float, y: float, tracking: float = 0.0):
    """SVG path data for `text`, baseline at y, left edge at x. Returns (d, width)."""
    f = TTFont(font_file)
    gs, cmap, upm = f.getGlyphSet(), f.getBestCmap(), f["head"].unitsPerEm
    s, cx, parts = size / upm, x, []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (s, 0, 0, -s, cx, y)))
        parts.append(pen.getCommands())
        cx += gs[name].width * s + tracking
    d = re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."), " ".join(p for p in parts if p))   # one decimal is plenty and halves the file
    return d, cx - x - tracking
