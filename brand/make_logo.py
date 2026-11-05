"""Builds every Jas iVision logo file from one source, so every size and variant matches.

    python3 brand/make_logo.py            # writes backend/web/img/*.svg and *.png, and backend/web/favicon.ico

The design: the JAS Infotech globe (traced from the brand artwork by trace_globe.py) inside an AI detection frame, with an AI sparkle in the
globe's orbit gap and network nodes where its lines cross. In the name, the "o" of iVision is a camera lens whose pupil is the AI sparkle.
Lettering follows the JAS Infotech logo (heavy italic sans for JAS in brand blue, bold italic serif for the product word) and is converted to
outlines, so the logo looks identical everywhere without any font installed.

Variants: full (with tagline), compact (for headers), stacked (for profile pictures and social media), one-colour black and white, the mark alone,
favicons, app icons (also a safe-zone version for Android), and a social-share image. Each colour SVG follows the visitor's light or dark setting.
Needs: pip install fonttools pillow; Chromium through Playwright (Node) draws the PNG files."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jas_mark import CYAN, lens, mark_body   # noqa: E402
from textpath import text_path                # noqa: E402

OUT = HERE.parents[0] / "backend" / "web" / "img"
BLUE, NAVY, INK, GREY, WHITE, DARKBG = "#128BE0", "#0A3D73", "#0B1220", "#5B6B7A", "#FFFFFF", "#0C1620"
SANS = "/usr/share/fonts/opentype/inter/Inter-ExtraBoldItalic.otf"
SANS_MED = "/usr/share/fonts/opentype/inter/Inter-MediumItalic.otf"
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf"
TAGLINE = "Intelligent Vision. Automated Insights."

# The colours of each look. "auto" is the light look plus a style block that switches to the dark look when the visitor's device is in dark mode.
LOOKS = {
    "light": dict(frame=NAVY, globe=BLUE, jas=BLUE, ink=INK, grey=GREY, halo=WHITE, ring=BLUE, glass=NAVY, spark=CYAN, mono=False),
    "dark": dict(frame="#7CC4FF", globe="#3DA5F0", jas=BLUE, ink=WHITE, grey="#A9B8C6", halo=DARKBG, ring=BLUE, glass=NAVY, spark=CYAN, mono=False),
    "black": dict(frame=INK, globe=INK, jas=INK, ink=INK, grey=INK, halo=None, ring=INK, glass=INK, spark=INK, mono=True),
    "white": dict(frame=WHITE, globe=WHITE, jas=WHITE, ink=WHITE, grey=WHITE, halo=None, ring=WHITE, glass=WHITE, spark=WHITE, mono=True),
}
DARK_CSS = ("@media (prefers-color-scheme:dark){.fr{stroke:#7CC4FF}.gl{fill:#3DA5F0}.halo{stroke:#0C1620}.ink{fill:#fff}.grey{fill:#A9B8C6}}")


def svg(w, h, body, style="", label="Jas iVision") -> str:
    st = f"<style>{style}</style>" if style else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}" role="img" aria-label="{label}">{st}{body}</svg>\n')


def _classes(body: str) -> str:
    """Tag the colours that change in dark mode, so one SVG file can follow the visitor's light or dark setting."""
    L = LOOKS["light"]
    return (body.replace(f'stroke="{L["frame"]}" stroke-width="8"', f'class="fr" stroke="{L["frame"]}" stroke-width="8"')
            .replace(f'fill="{L["globe"]}" fill-rule', f'class="gl" fill="{L["globe"]}" fill-rule')
            .replace(f'stroke="{L["halo"]}" stroke-width="4"', f'class="halo" stroke="{L["halo"]}" stroke-width="4"'))


# ---------------------------------------------------------------------------------------------------------------- text pieces
def ink_left(font_file, ch, size):
    """How far the first letter's ink starts to the right of where it is placed (so lines of different fonts can share one visible left edge)."""
    f = TTFont(font_file)
    gs = f.getGlyphSet()
    bp = BoundsPen(gs)
    gs[f.getBestCmap()[ord(ch)]].draw(bp)
    return bp.bounds[0] * size / f["head"].unitsPerEm


def word_jas(x, base, size=76):
    """JAS, placed so its ink starts exactly at x."""
    d, w = text_path(SANS, "JAS", size, x - ink_left(SANS, "J", size), base, tracking=-1.5)
    return d, w


def word_ivision(x, base, size=62):
    """iVision with its "o" left out: returns the outline of "iVisi" and "n", and where the lens goes (centre and radius)."""
    ft = TTFont(SERIF)
    upm = ft["head"].unitsPerEm
    k = size / upm
    o_adv = ft.getGlyphSet()[ft.getBestCmap()[ord("o")]].width * k
    xh = (getattr(ft["OS/2"], "sxHeight", 0) or 0.46 * upm) * k
    x0 = x - ink_left(SERIF, "i", size)
    v1, w1 = text_path(SERIF, "iVisi", size, x0, base)
    v2, w2 = text_path(SERIF, "n", size, x0 + w1 + o_adv, base)
    return v1 + " " + v2, (x0 + w1 + o_adv / 2 + 1.5, base - xh / 2, min(xh * 0.56, o_adv * 0.5)), w1 + o_adv + w2


# ---------------------------------------------------------------------------------------------------------------- the mark
def mark_group(look, uid="m", detail=True):
    L = LOOKS[look]
    _, b = mark_body(uid, L["frame"], L["globe"], L["spark"], L["halo"], detail)
    return b


def mark_svg(look="light", auto=False) -> str:
    b = mark_group(look)
    return svg(120, 120, _classes(b) if auto else b, DARK_CSS if auto else "")


def _tile(inner, size=120, rx=26, bg=BLUE):
    return f'<rect width="{size}" height="{size}" rx="{rx}" fill="{bg}"/>{inner}'


def favicon_svg(tiny=False) -> str:
    """Blue tile and a white mark. The 16 px version drops the globe (too fine to read) and keeps the frame and the AI sparkle."""
    if tiny:
        br = lambda d: f'<path d="{d}" fill="none" stroke="#fff" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>'
        inner = (br("M18 46V24Q18 18 24 18H46") + br("M74 18H96Q102 18 102 24V46") + br("M102 74V96Q102 102 96 102H74") + br("M46 102H24Q18 102 18 96V74")
                 + '<path fill="#fff" d="M60 30Q64 56 90 60Q64 64 60 90Q56 64 30 60Q56 56 60 30Z"/>')
        return svg(120, 120, _tile(inner, rx=24))
    return svg(120, 120, _tile(mark_group_on_blue(False)))


def mark_group_on_blue(detail=True):
    _, b = mark_body("a", WHITE, WHITE, "#BFEFFF", BLUE, detail)
    return b


def app_icon_svg(scale=0.85, rounded=False) -> str:
    """Full-bleed blue square (the phone rounds the corners). scale sets how much of the square the mark fills; a smaller scale gives the
    Android 'safe zone' version that survives being cropped to a circle."""
    off = 60 - 60 * scale
    return svg(120, 120, f'<rect width="120" height="120" fill="{BLUE}"/><g transform="translate({off:g} {off:g}) scale({scale:g})">{mark_group_on_blue()}</g>')


# ---------------------------------------------------------------------------------------------------------------- lockups
def _lockup_parts(look, tagline=True, x_text=150):
    L = LOOKS[look]
    jas_d, jw = word_jas(x_text, 70)
    iv_d, (lcx, lcy, lr), vw = word_ivision(x_text, 124)
    parts = [f'<path d="{jas_d}" fill="{L["jas"]}"/>', f'<path class="ink" d="{iv_d}" fill="{L["ink"]}"/>',
             lens(lcx, lcy, lr, L["ring"], L["glass"], L["spark"], mono=L["mono"])]
    right = max(x_text + jw, x_text + vw)
    if tagline:
        tag_d, tw = text_path(SANS_MED, TAGLINE, 15, x_text - ink_left(SANS_MED, "I", 15), 152, tracking=0.3)
        parts.append(f'<path class="grey" d="{tag_d}" fill="{L["grey"]}"/>')
        right = max(right, x_text + tw)
    return parts, right


def lockup(look="light", auto=False, variant="full") -> str:
    """full: mark, JAS over iVision, tagline. compact: the same without the tagline (for headers). stacked: mark above one line of name and the tagline."""
    if variant == "stacked":
        return stacked(look, auto)
    tagline = variant == "full"
    parts, right = _lockup_parts(look, tagline)
    cap_top = 70 - 0.727 * 76                                  # top of the capital letters of JAS
    bottom = 152 if tagline else 124
    my = (cap_top + bottom) / 2 - 60                           # the mark's vertical centre sits on the text block's centre
    b = mark_group(look, "l")
    body = f'<g transform="translate(2 {my:.1f})">{_classes(b) if auto else b}</g>' + "".join(parts)
    h = max(my + 120, bottom + 6) + 6
    top = min(0, my - 6)
    return svg(right + 8, h - top, f'<g transform="translate(0 {-top:.1f})">{body}</g>', DARK_CSS if auto else "")


def stacked(look="light", auto=False) -> str:
    L = LOOKS[look]
    jas_d, jw = word_jas(0, 0, 70)
    iv_d, (lcx, lcy, lr), vw = word_ivision(0, 0, 60)
    gap = 14
    total = jw + gap + vw
    W = max(total, 330) + 40
    x0 = (W - total) / 2
    base = 196
    jas_d, jw = word_jas(x0, base, 70)
    iv_d, (lcx, lcy, lr), vw = word_ivision(x0 + jw + gap, base, 60)
    tag_d, tw = text_path(SANS_MED, TAGLINE, 15, 0, base + 34, tracking=0.3)
    tag_d, tw = text_path(SANS_MED, TAGLINE, 15, (W - tw) / 2, base + 34, tracking=0.3)
    mark = _classes(mark_group(look, "s")) if auto else mark_group(look, "s")
    body = (f'<g transform="translate({(W - 120) / 2:g} 6) ">{mark}</g><path d="{jas_d}" fill="{L["jas"]}"/><path class="ink" d="{iv_d}" fill="{L["ink"]}"/>'
            + lens(lcx, lcy, lr, L["ring"], L["glass"], L["spark"], mono=L["mono"]) + f'<path class="grey" d="{tag_d}" fill="{L["grey"]}"/>')
    return svg(W, base + 48, body, DARK_CSS if auto else "")


def social_image() -> str:
    """1200 x 630 share picture: the full lockup on a soft brand-blue ground."""
    parts, right = _lockup_parts("light", True)
    inner = lockup("light")
    w = float(inner.split('width="')[1].split('"')[0]); h = float(inner.split('height="')[1].split('"')[0])
    s = 860 / w
    x, y = (1200 - w * s) / 2, (630 - h * s) / 2 - 6
    body = ('<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F7FBFF"/><stop offset="1" stop-color="#DCEEFC"/></linearGradient></defs>'
            f'<rect width="1200" height="630" fill="url(#bg)"/><rect x="0" y="610" width="1200" height="20" fill="{BLUE}"/>'
            f'<g transform="translate({x:.1f} {y:.1f}) scale({s:.4f})">{inner.split(">", 1)[1].rsplit("</svg>", 1)[0]}</g>')
    return svg(1200, 630, body)


# ---------------------------------------------------------------------------------------------------------------- drawing PNGs
def rasterise(jobs):
    """jobs: [(svg text, png name, width, height, background or None, device scale, final width or None)]. Chromium draws each at `device scale`;
    when a final width is given the picture is then shrunk with a high-quality filter, which gives much cleaner edges at tiny sizes."""
    with tempfile.TemporaryDirectory() as d:
        spec = []
        for n, (text, name, w, h, bg, dsf, final) in enumerate(jobs):
            f = pathlib.Path(d) / f"{n}.svg"
            f.write_text(text)
            spec.append({"svg": str(f), "out": str(OUT / name), "w": w, "h": h, "bg": bg, "dsf": dsf})
        (pathlib.Path(d) / "jobs.json").write_text(json.dumps(spec))
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
        subprocess.run(["node", str(HERE / "rasterise.js"), str(pathlib.Path(d) / "jobs.json")], check=True, env={**os.environ, "NODE_PATH": root})
    for text, name, w, h, bg, dsf, final in jobs:
        if final:
            im = Image.open(OUT / name)
            im.resize((final, round(final * im.height / im.width)), Image.LANCZOS).save(OUT / name, optimize=True)


def size_of(svg_text):
    return int(float(svg_text.split('width="')[1].split('"')[0])), int(float(svg_text.split('height="')[1].split('"')[0]))


def write_ico(path, pngs):
    """A .ico file holding one ready-made PNG per size (so each size keeps its own hand-tuned picture). pngs: [(size, png bytes)]."""
    import struct
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for size, png in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    pathlib.Path(path).write_bytes(head + entries + data)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "logo.svg": lockup("light", auto=True), "logo-on-light.svg": lockup("light"), "logo-on-dark.svg": lockup("dark"),
        "logo-compact.svg": lockup("light", auto=True, variant="compact"), "logo-compact-on-dark.svg": lockup("dark", variant="compact"),
        "logo-stacked.svg": lockup("light", auto=True, variant="stacked"), "logo-stacked-on-dark.svg": lockup("dark", variant="stacked"),
        "logo-black.svg": lockup("black"), "logo-white.svg": lockup("white"),
        "logo-mark.svg": mark_svg("light", auto=True), "logo-mark-black.svg": mark_svg("black"), "logo-mark-white.svg": mark_svg("white"),
        "favicon.svg": favicon_svg(), "og-image.svg": social_image(),
    }
    for name, text in files.items():
        (OUT / name).write_text(text)

    jobs = []

    def add(text, name, bg=None, dsf=4, final=None, size=None):
        w, h = size or size_of(text)
        jobs.append((text, name, w, h, bg, dsf, final))
    # lockups: four times the SVG's size, so they stay sharp when printed or put on a big screen
    add(lockup("light"), "logo-on-light.png", "#FFFFFF")
    add(lockup("dark"), "logo-on-dark.png", DARKBG)
    add(lockup("light", variant="compact"), "logo-compact.png", "#FFFFFF")
    add(lockup("light", variant="stacked"), "logo-stacked.png", "#FFFFFF")
    add(lockup("light"), "logo-transparent.png")
    add(lockup("dark"), "logo-transparent-dark.png")
    add(files["logo-black.svg"], "logo-black.png")
    add(files["logo-white.svg"], "logo-white.png")
    add(mark_svg("light"), "logo-mark-512.png", dsf=6, final=512)
    # icons: drawn large, then shrunk with a high-quality filter, which gives much cleaner edges at tiny sizes
    big = app_icon_svg()
    add(favicon_svg(tiny=True), "favicon-16.png", dsf=10, final=16, size=(120, 120))
    add(favicon_svg(), "favicon-32.png", dsf=8, final=32, size=(120, 120))
    add(favicon_svg(), "favicon-48.png", dsf=8, final=48, size=(120, 120))
    add(big, "apple-touch-icon.png", BLUE, dsf=6, final=180, size=(120, 120))
    add(big, "icon-192.png", BLUE, dsf=6, final=192, size=(120, 120))
    add(big, "icon-512.png", BLUE, dsf=6, final=512, size=(120, 120))
    add(app_icon_svg(0.62), "icon-maskable-512.png", BLUE, dsf=6, final=512, size=(120, 120))
    add(files["og-image.svg"], "og-image.png", "#FFFFFF", dsf=1)
    rasterise(jobs)

    pngs = [(n, (OUT / f"favicon-{n}.png").read_bytes()) for n in (16, 32, 48)]
    write_ico(OUT / "favicon.ico", pngs)
    (OUT.parent / "favicon.ico").write_bytes((OUT / "favicon.ico").read_bytes())          # browsers ask for /favicon.ico by themselves
    (OUT / "site.webmanifest").write_text(json.dumps({
        "name": "Jas iVision", "short_name": "Jas iVision", "description": TAGLINE,
        "icons": [{"src": "/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/img/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "theme_color": BLUE, "background_color": "#FFFFFF", "display": "standalone"}, indent=1) + "\n")
    print("wrote", len(list(OUT.iterdir())), "files")


if __name__ == "__main__":
    sys.exit(main())
