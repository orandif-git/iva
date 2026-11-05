"""Builds the Jas iVision logo files from one source (jas_mark.py draws the mark), so every size and variant matches.

    python3 brand/make_logo.py            # writes backend/web/img/*.svg and *.png, favicon.ico

Idea: the JAS Infotech globe (tapered line-art with the orbit gap) inside an AI detection frame, with an AI sparkle in the gap and a few connected nodes.
The lettering follows the JAS Infotech logo: heavy italic sans for JAS in brand blue, bold italic serif for the product word.
Text is converted to outlines (paths), so the logo looks identical everywhere without any font installed.
Needs: pip install fonttools; Chromium (Playwright) is used only to draw the PNG sizes."""
import pathlib
import re
import sys

from fontTools.ttLib import TTFont


OUT = pathlib.Path(__file__).resolve().parents[1] / "backend" / "web" / "img"
BLUE, NAVY, INK, GREY, WHITE = "#128BE0", "#0A3D73", "#0B1220", "#5B6B7A", "#FFFFFF"
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from textpath import text_path                                                              # noqa: E402

SANS = "/usr/share/fonts/opentype/inter/Inter-ExtraBoldItalic.otf"
SANS_MED = "/usr/share/fonts/opentype/inter/Inter-MediumItalic.otf"
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf"


# ------------------------------------------------------------------------------------------------ the mark (viewBox 0 0 120 120)
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from jas_mark import CYAN, lens, mark_body                                                     # noqa: E402


def _classes(body: str) -> str:
    """Tag the colours that change on dark backgrounds, so a single SVG file can follow the visitor's light or dark setting."""
    return (body.replace(f'stroke="{NAVY}"', f'class="fr" stroke="{NAVY}"').replace(f'fill="{BLUE}"', f'class="gl" fill="{BLUE}"')
            .replace(f'stroke="{WHITE}" stroke-width="4"', f'class="halo" stroke="{WHITE}" stroke-width="4"'))


DARK_CSS = "@media (prefers-color-scheme:dark){.fr{stroke:#7CC4FF}.gl{fill:#3DA5F0}.halo{stroke:#0C1620}.hf{fill:#0C1620}.frf{fill:#7CC4FF}.pt{fill:#0C1620}.ink{fill:#fff}.grey{fill:#A9B8C6}}"


def svg(w, h, defs, body, style="", y0=0) -> str:
    st = f"<style>{style}</style>" if style else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {y0} {w} {h}" width="{w}" height="{h}" role="img" aria-label="Jas iVision"><defs>{defs}</defs>{st}{body}</svg>\n'


def mark_svg(uid="m") -> str:
    d, b = mark_body(uid, NAVY, BLUE, CYAN, WHITE)
    return svg(120, 120, d, _classes(b), DARK_CSS)


def favicon_svg() -> str:
    """Simplified so it reads at 16 px: a blue tile, white frame corners, the globe and the sparkle (no fine lines, no nodes)."""
    d, b = mark_body("f", WHITE, WHITE, "#BFEFFF", BLUE, detail=False)
    return svg(120, 120, d, f'<rect width="120" height="120" rx="26" fill="{BLUE}"/>{b}')


def app_icon_svg(size=512) -> str:
    d, b = mark_body("a", WHITE, WHITE, "#BFEFFF", BLUE, detail=True)
    return svg(120, 120, d, f'<rect width="120" height="120" fill="{BLUE}"/><g transform="translate(9 9) scale(.85)">{b}</g>')


# ------------------------------------------------------------------------------------------------ the lockup
def lockup(dark_bg=False, auto_dark=False, caps=True) -> str:
    """The mark on the left; Jas (heavy italic sans, brand blue) over iVision (bold italic serif), and the tagline underneath."""
    jas, jw = text_path(SANS, "JAS" if caps else "Jas", 76, 150, 70, tracking=-1.5 if caps else -1)
    # iVision with the "o" replaced by a camera lens whose pupil is the AI sparkle
    ft = TTFont(SERIF); upm = ft["head"].unitsPerEm; k = 62 / upm
    o_adv = ft.getGlyphSet()[ft.getBestCmap()[ord("o")]].width * k
    xh = (getattr(ft["OS/2"], "sxHeight", 0) or 0.46 * upm) * k
    v1, w1 = text_path(SERIF, "iVisi", 62, 144, 124)
    v2, w2 = text_path(SERIF, "n", 62, 144 + w1 + o_adv, 124)
    vis, vw = v1 + " " + v2, w1 + o_adv + w2
    lens_r = min(xh * 0.56, o_adv * 0.5)
    lens_cx, lens_cy = 144 + w1 + o_adv / 2 + 1.5, 124 - xh / 2
    tag, tw = text_path(SANS_MED, "Intelligent Vision. Automated Insights.", 15, 148, 152, tracking=0.3)
    w = int(max(150 + jw, 144 + vw, 148 + tw)) + 8
    ink, grey = (WHITE, "#A9B8C6") if dark_bg else (INK, GREY)
    frame, globe_c = ("#7CC4FF", "#3DA5F0") if dark_bg else (NAVY, BLUE)
    d, b = mark_body("l", frame, globe_c, CYAN, "#0C1620" if dark_bg else WHITE)
    if auto_dark:
        b = _classes(b)
    body = (f'<g transform="translate(2 18)">{b}</g><path d="{jas}" fill="{BLUE}"/><path class="ink" d="{vis}" fill="{ink}"/>{lens(lens_cx, lens_cy, lens_r, BLUE, "#0A3D73", CYAN)}<path class="grey" d="{tag}" fill="{grey}"/>')
    return svg(w, 156, d, body, DARK_CSS if auto_dark else "")


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
    fav, big = favicon_svg(), app_icon_svg()
    rasterise([
        (fav, "favicon-16.png", 16, 16, None), (fav, "favicon-32.png", 32, 32, None), (fav, "favicon-48.png", 48, 48, None),
        (big, "apple-touch-icon.png", 180, 180, BLUE), (big, "icon-192.png", 192, 192, BLUE), (big, "icon-512.png", 512, 512, BLUE),
        (mark_svg(), "logo-mark-512.png", 512, 512, None), (lockup(), "logo-on-light.png", 1200, 624, "#FFFFFF"), (lockup(dark_bg=True), "logo-on-dark.png", 1200, 624, "#0C1620"),
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
