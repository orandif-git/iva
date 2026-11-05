"""Three logo directions for Jas iVision, drawn side by side for choosing. Writes brand/concepts/*.svg, *.png and a comparison sheet."""
import base64, pathlib, subprocess, sys
from fontTools.pens.boundsPen import BoundsPen
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from make_logo import text_path, BLUE, NAVY, INK, GREY, WHITE, SANS, SANS_MED, SERIF
from fontTools.ttLib import TTFont

OUT = pathlib.Path(__file__).parent / "concepts"; OUT.mkdir(exist_ok=True)
SANS_BOLD = "/usr/share/fonts/opentype/inter/Inter-BoldItalic.otf"
CYAN, SKY = "#35C6F4", "#7CC4FF"
TAG = "Intelligent Vision. Automated Insights."

def pal(dark): return dict(ink=WHITE if dark else INK, grey="#A9B8C6" if dark else GREY, deep=SKY if dark else NAVY, ring="#0C1620" if dark else WHITE, bg="#0C1620" if dark else WHITE)

def globe(cx, cy, r, fg, line, deep=None, w=None):
    w = w or r * 0.085
    d = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fg}"/>'
    if deep: d += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{deep}" stroke-width="{r*0.11}"/>'
    d += (f'<g fill="none" stroke="{line}" stroke-width="{w}" stroke-linecap="round"><ellipse cx="{cx}" cy="{cy}" rx="{r*0.42}" ry="{r*0.94}"/>'
          f'<path d="M{cx} {cy-r*0.94}V{cy+r*0.94}M{cx-r*0.94} {cy}H{cx+r*0.94}"/>'
          f'<path d="M{cx-r*0.78} {cy-r*0.46}Q{cx} {cy-r*0.28} {cx+r*0.78} {cy-r*0.46}M{cx-r*0.78} {cy+r*0.46}Q{cx} {cy+r*0.28} {cx+r*0.78} {cy+r*0.46}"/></g>')
    return d

def svg(w, h, body): return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="Jas iVision">{body}</svg>'

# ---------------------------------------------------------------------------- A: Lens Orbit (the JAS globe as a camera lens, orbit swoosh around it)
def mark_a(dark=False):
    p = pal(dark)
    return (f'<circle cx="60" cy="60" r="52" fill="none" stroke="{p["deep"]}" stroke-width="9"/>'
            f'<circle cx="60" cy="60" r="41" fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-dasharray="3 5.2" stroke-linecap="round"/>'
            + globe(60, 60, 31, BLUE, WHITE, p["deep"]) +
            # orbit swoosh crossing in front, tapering, like the JAS logo's
            f'<path d="M6 83C40 108 100 90 118 40C97 74 48 90 6 83Z" fill="{CYAN}"/>'
            f'<path d="M27 26C20 22 12 28 10 34" fill="none" stroke="{WHITE}" stroke-width="0"/>'
            f'<circle cx="93" cy="27" r="4.5" fill="{WHITE}" opacity=".95"/>')

def lockup_a(dark=False):
    p = pal(dark)
    jas, jw = text_path(SANS, "Jas", 84, 142, 80, tracking=-1)
    vis, vw = text_path(SANS_MED, "iVision", 84, 142 + jw + 14, 80)
    tag, tw = text_path(SANS_MED, TAG, 15.5, 146, 113, tracking=.3)
    w = int(max(142 + jw + 14 + vw, 146 + tw)) + 10
    return svg(w, 124, f'<g transform="translate(4 2)">{mark_a(dark)}</g><path d="{jas}" fill="{BLUE}"/><path d="{vis}" fill="{p["ink"]}"/><path d="{tag}" fill="{p["grey"]}"/>')

def icon_a(dark=False):
    return svg(120, 120, f'<rect width="120" height="120" rx="26" fill="{BLUE}"/><g transform="translate(14 14) scale(.77)">{mark_a_on_blue()}</g>')

def mark_a_on_blue():
    return (f'<circle cx="60" cy="60" r="52" fill="none" stroke="{WHITE}" stroke-width="9"/>' + globe(60, 60, 31, WHITE, BLUE) +
            f'<path d="M6 83C40 108 100 90 118 40C97 74 48 90 6 83Z" fill="{NAVY}"/>')

# ---------------------------------------------------------------------------- B: the "i" whose dot is the globe lens (wordmark-led, very simple)
def lockup_b(dark=False, tagline=True):
    p = pal(dark)
    size = 92
    jas, jw = text_path(SANS, "Jas", size, 6, 92, tracking=-1.5)
    f = TTFont(SANS_BOLD); gs = f.getGlyphSet(); cmap = f.getBestCmap(); upm = f["head"].unitsPerEm; s = size / upm
    dotless = cmap[0x131]
    bp = BoundsPen(gs); gs[dotless].draw(bp); x0, y0, x1, y1 = bp.bounds
    x = 6 + jw + 22
    i_d, iw = text_path(SANS_BOLD, "ı", size, x, 92)
    rest, rw = text_path(SANS_BOLD, "Vision", size, x + iw - 1, 92, tracking=-1)
    # the dot: stem top is on the right half of the italic glyph's box
    sx = x + (x0 + (x1 - x0) * 0.62) * s; top = 92 - y1 * s
    gcx, gcy, gr = sx + 4, top - 25, 16.5
    body = (f'<path d="{jas}" fill="{BLUE}"/><path d="{i_d}" fill="{p["ink"]}"/><path d="{rest}" fill="{p["ink"]}"/>'
            + globe(gcx, gcy, gr, BLUE, WHITE, p["deep"]))
    w = int(x + iw + rw) + 12
    h = 124
    if tagline:
        tag, tw = text_path(SANS_MED, TAG, 16, 8, 120, tracking=.35)
        body += f'<path d="{tag}" fill="{p["grey"]}"/>'; w = max(w, int(8 + tw) + 8)
    return svg(w, h, body)

def icon_b(dark=False):
    f = TTFont(SANS_BOLD); gs = f.getGlyphSet(); cm = f.getBestCmap(); upm = f["head"].unitsPerEm; size = 92; s = size / upm
    bp = BoundsPen(gs); gs[cm[0x131]].draw(bp); x0, y0, x1, y1 = bp.bounds
    wi, wv = gs[cm[0x131]].width * s, gs[cm[ord("V")]].width * s
    ox = 60 - (wi + wv - 4) / 2; oy = 92
    di, _ = text_path(SANS_BOLD, "\u0131", size, ox, oy); dv, _ = text_path(SANS_BOLD, "V", size, ox + wi - 4, oy)
    sx = ox + (x0 + (x1 - x0) * .62) * s; top = oy - y1 * s
    return svg(120, 120, f'<rect width="120" height="120" rx="26" fill="{BLUE}"/><path d="{di}" fill="{WHITE}"/><path d="{dv}" fill="{WHITE}"/>' + globe(sx + 3, top - 17, 13, WHITE, BLUE))

# ---------------------------------------------------------------------------- C: Detection frame (AI "bounding box" + scan line around the globe)
def mark_c(dark=False, on_blue=False):
    p = pal(dark); fr = WHITE if on_blue else p["deep"]
    br = lambda d: f'<path d="{d}" fill="none" stroke="{fr}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>'
    g = globe(60, 60, 33, WHITE if on_blue else BLUE, BLUE if on_blue else WHITE, None)
    return (br("M10 38V14Q10 10 14 10H38") + br("M82 10H106Q110 10 110 14V38") + br("M110 82V106Q110 110 106 110H82") + br("M38 110H14Q10 110 10 106V82")
            + g + f'<rect x="14" y="56" width="92" height="7" rx="3.5" fill="{CYAN if not on_blue else NAVY}" opacity=".92"/>')

def lockup_c(dark=False):
    p = pal(dark)
    jas, jw = text_path(SANS, "Jas", 76, 140, 70, tracking=-1)
    vis, vw = text_path(SERIF, "iVision", 62, 134, 124)
    tag, tw = text_path(SANS_MED, TAG, 15, 138, 152, tracking=.3)
    w = int(max(140 + jw, 134 + vw, 138 + tw)) + 8
    return svg(w, 164, f'<g transform="translate(2 18)">{mark_c(dark)}</g><path d="{jas}" fill="{BLUE}"/><path d="{vis}" fill="{p["ink"]}"/><path d="{tag}" fill="{p["grey"]}"/>')

def icon_c(dark=False):
    return svg(120, 120, f'<rect width="120" height="120" rx="26" fill="{BLUE}"/><g transform="translate(14 14) scale(.77)">{mark_c(on_blue=True)}</g>')

CONCEPTS = [("A", "Lens Orbit", "The JAS globe as a camera lens, with the orbit swoosh around it. Closest to your parent logo.", lockup_a, icon_a),
            ("B", "The Lens Dot", "The dot of the i becomes the globe lens. The most compact and the easiest to remember; works as a plain wordmark.", lockup_b, icon_b),
            ("C", "Detection Frame", "The globe inside the corner brackets and scan line of an AI detection box. Says what the product does.", lockup_c, icon_c)]

def b64(s): return "data:image/svg+xml;base64," + base64.b64encode(s.encode()).decode()

def main():
    cards = ""
    for key, name, why, lk, ic in CONCEPTS:
        (OUT / f"{key}-lockup-light.svg").write_text(lk(False)); (OUT / f"{key}-lockup-dark.svg").write_text(lk(True)); (OUT / f"{key}-icon.svg").write_text(ic(False))
        cards += (f'<section><h2><b>{key}</b> {name}</h2><p>{why}</p><div class="l"><img src="{b64(lk(False))}"></div><div class="d"><img src="{b64(lk(True))}"></div>'
                  f'<div class="ic"><img src="{b64(ic())}" width="96"><img src="{b64(ic())}" width="48"><img src="{b64(ic())}" width="32"><img src="{b64(ic())}" width="16"><span>app icon, then browser tab sizes</span></div></section>')
    html = ('<html><head><style>body{margin:0;background:#EEF2F6;font-family:Inter,system-ui,sans-serif;color:#0B1220}main{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;padding:26px;width:1560px;box-sizing:border-box}'
            'section{background:#fff;border-radius:16px;padding:18px;box-shadow:0 1px 3px #0002}h2{margin:0 0 4px;font-size:22px}h2 b{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#128BE0;color:#fff;margin-right:8px}'
            'p{margin:0 0 14px;color:#51606F;font-size:14px;min-height:40px}.l,.d{border-radius:12px;padding:22px 14px;display:grid;place-items:center;margin-bottom:12px}.l{border:1px solid #DDE5EC}.d{background:#0C1620}'
            'img{max-width:100%}.ic{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.ic span{font-size:12px;color:#51606F}</style></head><body><main>' + cards + '</main></body></html>')
    (OUT / "sheet.html").write_text(html)
    js = OUT / "_shot.js"
    js.write_text("const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1560,height:900},deviceScaleFactor:1.5});"
                  "await p.goto('file://'+process.argv[2]);await p.waitForTimeout(500);await p.screenshot({path:process.argv[3],fullPage:true});await b.close()})()")
    env = {**__import__("os").environ, "NODE_PATH": subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()}
    subprocess.run(["node", str(js), str(OUT / "sheet.html"), str(OUT / "choose-a-logo.png")], check=True, env=env); js.unlink()
    print("ok")

if __name__ == "__main__":
    main()
