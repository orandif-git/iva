"""Builds the Jas iVision logo files from one source, so every size and variant matches.

    python3 brand/make_logo.py            # writes backend/web/img/*.svg and *.png, favicon.ico

Idea: an EYE (vision) whose iris is the JAS globe (the parent brand), framed by two orbit swooshes like the JAS logo's.
The lettering follows the JAS Infotech logo: heavy italic sans for JAS in brand blue, bold italic serif for the product word.
Text is converted to outlines (paths), so the logo looks identical everywhere without any font installed.
Needs: pip install fonttools; Chromium (Playwright) is used only to draw the PNG sizes."""
import pathlib
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = pathlib.Path(__file__).resolve().parents[1] / "backend" / "web" / "img"
BLUE, NAVY, INK, GREY, WHITE = "#128BE0", "#0A3D73", "#0B1220", "#5B6B7A", "#FFFFFF"
SANS = "/usr/share/fonts/opentype/inter/Inter-ExtraBoldItalic.otf"
SANS_MED = "/usr/share/fonts/opentype/inter/Inter-MediumItalic.otf"
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf"


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
    return " ".join(p for p in parts if p), cx - x - tracking


# ------------------------------------------------------------------------------------------------ the mark (viewBox 0 0 120 120)
def mark(fg=BLUE, deep=NAVY, ring=WHITE, detail=True) -> str:
    """The eye. `ring` is the colour that separates the iris from the lids (the background colour behind the mark)."""
    globe = (f'<g fill="none" stroke="{ring}" stroke-width="2.4" stroke-linecap="round">'
             f'<ellipse cx="60" cy="61" rx="12" ry="26.5"/><path d="M60 34.5V87.5M33.5 61H86.5"/>'
             f'<path d="M36.5 48.5Q60 54.5 83.5 48.5M36.5 73.5Q60 67.5 83.5 73.5"/></g>') if detail else ""
    spark = (f'<path d="M100 6l3.2 9L112 18.2l-8.8 3.2L100 30.4l-3.2-9L88 18.2l8.8-3.2z" fill="{deep}"/>') if detail else ""
    return (
        # upper lid: a thick swoosh that tapers to a point at both ends (like the JAS orbit)
        f'<path d="M1 64C25 8 95 8 119 60C95 28 27 30 1 64Z" fill="{fg}"/>'
        # lower lid: a thinner swoosh
        f'<path d="M7 69C33 106 89 106 113 65C90 93 33 93 7 69Z" fill="{deep}"/>'
        # iris = the globe, with a little depth
        f'<defs><radialGradient id="iris" cx="38%" cy="32%" r="80%"><stop offset="0" stop-color="#4FB3FF"/><stop offset="1" stop-color="{fg}"/></radialGradient></defs>'
        f'<circle cx="60" cy="61" r="30" fill="url(#iris)"/><circle cx="60" cy="61" r="30" fill="none" stroke="{deep}" stroke-width="3.2"/>'
        f'{globe}{spark}')


def svg(w, h, body, extra_style="") -> str:
    style = f"<style>{extra_style}</style>" if extra_style else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="Jas iVision">{style}{body}</svg>\n'


def mark_svg() -> str:
    # The deep blue lower lid and the ring colour adapt to dark backgrounds, like the lockup.
    dark = "@media (prefers-color-scheme:dark){.deep{fill:#7CC4FF}.ring{stroke:#0C1620}.ink{fill:#fff}}"
    body = mark().replace(f'fill="{NAVY}"', 'class="deep" fill="%s"' % NAVY).replace(f'stroke="{WHITE}"', 'class="ring" stroke="%s"' % WHITE)
    return svg(120, 120, body, dark)


def favicon_svg() -> str:
    """Simple on purpose: it must read at 16 px. A blue rounded square, a white eye, a blue iris with the globe's cross lines."""
    body = (f'<rect width="64" height="64" rx="14" fill="{BLUE}"/>'
            f'<path d="M6 33C16 14 48 14 58 33C48 52 16 52 6 33Z" fill="{WHITE}"/>'
            f'<circle cx="32" cy="33" r="12.5" fill="{BLUE}"/>'
            f'<g fill="none" stroke="{WHITE}" stroke-width="2.4" stroke-linecap="round"><ellipse cx="32" cy="33" rx="5.2" ry="12.5"/><path d="M32 21V45M19.5 33H44.5"/></g>')
    return svg(64, 64, body)


# ------------------------------------------------------------------------------------------------ the lockup
def lockup(dark_bg=False, auto_dark=False) -> str:
    """Mark on the left; JAS (heavy italic, brand blue) over iVision (bold italic serif), and the tagline underneath."""
    jas, jw = text_path(SANS, "JAS", 74, 156, 74, tracking=-2)
    vis, vw = text_path(SERIF, "iVision", 62, 150, 132)
    tag, tw = text_path(SANS_MED, "Intelligent Vision. Automated Insights.", 15.6, 154, 163, tracking=0.2)
    w = int(max(156 + jw, 150 + vw, 154 + tw)) + 8
    ink, grey, ring, deep = (WHITE, "#A9B8C6", "#0C1620", "#7CC4FF") if dark_bg else (INK, GREY, WHITE, NAVY)
    body = (f'<g transform="translate(2 8) scale(1.2)">{mark(BLUE, deep, ring)}</g>'
            f'<path d="{jas}" fill="{BLUE}"/><path class="ink" d="{vis}" fill="{ink}"/><path class="grey" d="{tag}" fill="{grey}"/>')
    style = "@media (prefers-color-scheme:dark){.ink{fill:#fff}.grey{fill:#A9B8C6}}" if auto_dark else ""
    if auto_dark:       # the mark's own colours must follow too
        body = body.replace(f'fill="{NAVY}"', 'class="deep" fill="%s"' % NAVY).replace(f'stroke="{WHITE}"', 'class="ring" stroke="%s"' % WHITE)
        style += "@media (prefers-color-scheme:dark){.deep{fill:#7CC4FF}.ring{stroke:#0C1620}}"
    return svg(w, 176, body, style)


def rasterise(jobs: list):
    """jobs: [(svg text, png name, width, height, background or None)]. Chromium draws them (brand/rasterise.js)."""
    import json, subprocess, tempfile
    with tempfile.TemporaryDirectory() as d:
        spec = []
        for n, (text, name, w, h, bg) in enumerate(jobs):
            f = pathlib.Path(d) / f"{n}.svg"
            f.write_text(text)
            spec.append({"svg": str(f), "out": str(OUT / name), "w": w, "h": h, "bg": bg})
        (pathlib.Path(d) / "jobs.json").write_text(json.dumps(spec))
        subprocess.run(["node", str(pathlib.Path(__file__).with_name("rasterise.js")), str(pathlib.Path(d) / "jobs.json")], check=True,
                       env={**__import__("os").environ, "NODE_PATH": __import__("subprocess").run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logo-mark.svg").write_text(mark_svg())
    (OUT / "logo.svg").write_text(lockup(auto_dark=True))                  # follows the visitor's light or dark setting
    (OUT / "logo-on-light.svg").write_text(lockup())
    (OUT / "logo-on-dark.svg").write_text(lockup(dark_bg=True))
    (OUT / "favicon.svg").write_text(favicon_svg())
    fav = favicon_svg()
    big = svg(512, 512, f'<rect width="512" height="512" fill="{BLUE}"/><g transform="translate(46 40) scale(3.45)">{mark(WHITE, "#BFE3FF", BLUE)}</g>')
    rasterise([
        (fav, "favicon-16.png", 16, 16, None), (fav, "favicon-32.png", 32, 32, None), (fav, "favicon-48.png", 48, 48, None),
        (big, "apple-touch-icon.png", 180, 180, BLUE), (big, "icon-192.png", 192, 192, BLUE), (big, "icon-512.png", 512, 512, BLUE),
        (mark_svg(), "logo-mark-512.png", 512, 512, None), (lockup(), "logo-on-light.png", 1230, 528, "#FFFFFF"), (lockup(dark_bg=True), "logo-on-dark.png", 1230, 528, "#0C1620"),
    ])
    from PIL import Image
    Image.open(OUT / "favicon-48.png").save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    (OUT.parent / "favicon.ico").write_bytes((OUT / "favicon.ico").read_bytes())          # browsers ask for /favicon.ico by themselves
    (OUT / "site.webmanifest").write_text('{"name":"Jas iVision","short_name":"Jas iVision","description":"Intelligent Vision. Automated Insights.",'
                                          '"icons":[{"src":"/img/icon-192.png","sizes":"192x192","type":"image/png"},{"src":"/img/icon-512.png","sizes":"512x512","type":"image/png"}],'
                                          '"theme_color":"#128BE0","background_color":"#FFFFFF","display":"standalone"}\n')
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    sys.exit(main())
