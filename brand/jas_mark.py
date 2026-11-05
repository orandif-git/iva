"""The Jas iVision mark: the JAS Infotech globe, traced from the brand artwork (trace_globe.py), inside an AI detection frame. AI elements:
a sparkle in the globe's orbit gap and network nodes on the line intersections, so the globe reads as a neural network."""
import pathlib

BLUE, NAVY, CYAN, WHITE = "#128BE0", "#0A3D73", "#35C6F4", "#FFFFFF"
_gw, _gh, _gd = (lambda t: (float(t[0].split()[0]), float(t[0].split()[1]), t[1]))(pathlib.Path(__file__).with_name("jas_globe_path.txt").read_text().split("\n"))
GAP = (200.0, 76.0)                                                    # centre of the orbit gap, in the globe's own coordinates
NODES = [(80, 103), (137, 150), (217, 181), (85, 243), (205, 278)]      # where the globe's lines cross


def _f(v): return f"{v:.2f}".rstrip("0").rstrip(".")


def sparkle(cx, cy, r, fill):
    k = r * 0.22
    return (f'<path fill="{fill}" d="M{_f(cx)} {_f(cy-r)}Q{_f(cx+k)} {_f(cy-k)} {_f(cx+r)} {_f(cy)}Q{_f(cx+k)} {_f(cy+k)} {_f(cx)} {_f(cy+r)}'
            f'Q{_f(cx-k)} {_f(cy+k)} {_f(cx-r)} {_f(cy)}Q{_f(cx-k)} {_f(cy-k)} {_f(cx)} {_f(cy-r)}Z"/>')


def lens(cx, cy, R, ring, glass, pupil, glint="#fff", mono=False):
    """A camera lens of radius R: outer ring, dark glass, an inner ring, and the AI sparkle as its pupil. Used for the 'o' in iVision."""
    if mono:                                                        # one colour: an open ring, the sparkle, nothing else
        return (f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(R*0.88)}" fill="none" stroke="{ring}" stroke-width="{_f(R*0.2)}"/>' + sparkle(cx, cy, R * 0.5, ring))
    return (f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(R)}" fill="{ring}"/><circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(R*0.78)}" fill="{glass}"/>'
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(R*0.54)}" fill="none" stroke="{pupil}" stroke-width="{_f(R*0.09)}" opacity=".7"/>'
            + sparkle(cx, cy, R * 0.47, pupil) +
            f'<ellipse cx="{_f(cx-R*0.40)}" cy="{_f(cy-R*0.44)}" rx="{_f(R*0.19)}" ry="{_f(R*0.1)}" transform="rotate(-38 {_f(cx-R*0.40)} {_f(cy-R*0.44)})" fill="{glint}" opacity=".9"/>')


def mark_body(uid, frame, globe_c, ai, halo, detail=True):
    """The whole mark in a 120 x 120 box. frame = bracket colour, globe_c = globe colour, ai = sparkle and node colour,
    halo = the background colour, drawn as a thin ring round each node so it stays clear of the lines. Returns (defs, body)."""
    br = lambda d: f'<path d="{d}" fill="none" stroke="{frame}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>'
    s = 80.0 / _gw                                                       # the globe, scaled to sit inside the frame
    tx, ty = 60 - _gw * s / 2 + 1, 61 - _gh * s / 2
    inner = f'<path d="{_gd}" fill="{globe_c}" fill-rule="evenodd" stroke="{globe_c}" stroke-width="{4.5 if detail else 8}" stroke-linejoin="round"/>'
    inner += sparkle(GAP[0] + 4, GAP[1], 50 if detail else 46, ai)
    if detail:
        inner += "".join(f'<circle cx="{x}" cy="{y}" r="11" fill="{ai}"' + (f' stroke="{halo}" stroke-width="4"' if halo else "") + "/>" for x, y in NODES)
    body = (br("M10 38V14Q10 10 14 10H38") + br("M82 10H106Q110 10 110 14V38") + br("M110 82V106Q110 110 106 110H82") + br("M38 110H14Q10 110 10 106V82")
            + f'<g transform="translate({_f(tx)} {_f(ty)}) scale({_f(s)})">{inner}</g>')
    return "", body
