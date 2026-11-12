"""Is the camera itself all right? Two alerts: "Camera position changed or covered" and "Poor image quality".

The worker measures each camera's picture every five minutes (worker/picture.py): brightness, contrast, sharpness and a small sketch of the outlines it looks at. Here each
camera is compared with its OWN usual view, and an alert is raised only when something has been wrong for a good while, so a person standing in front of the lens, a cloud
or a passing headlight never raises one.

  moved    the outline sketch matches the usual view poorly for 30 minutes. It then becomes the new usual view, so one move is one alert.
  covered  the picture is a flat colour (no outlines) for 15 minutes: a cloth, a hand, tape, or a lens turned to a wall.
  blurry   much less sharp than this camera usually is in daylight, for two hours: a dirty or fogged lens, or focus knocked.
  dark     almost black for an hour while the premises are open (needs the opening hours): a lamp left off, a failed infrared light.

Daylight and night keep separate usual views, because a camera's picture changes completely when it switches to infrared."""
import base64
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import detectors
from .alerts import raise_alert
from .opening_hours import Hours

SKETCH_BYTES = 288                  # 64 x 36 points, as bits
NIGHT_BELOW = 60                    # brightness under which the picture is a night picture
MOVED_BELOW, SAME_ABOVE = 0.20, 0.45   # how much of the outlines still match: below the first it is a different view, above the second it is the same
FLAT_CONTRAST, FLAT_EDGES = 8.0, 12     # a picture this flat, with almost no outlines, is covered
MOVED_FOR, COVERED_FOR, BLURRY_FOR, DARK_FOR = timedelta(minutes=30), timedelta(minutes=15), timedelta(hours=2), timedelta(hours=1)
BLURRY_SHARE = 0.35                 # sharper than this share of its usual sharpness is fine
DARK_BELOW = 20
BASELINE_AFTER = 48                 # daylight measurements (about four hours) before "usual sharpness" is trusted
REFRESH_AFTER = timedelta(days=1)   # while the view matches, its usual sketch is refreshed daily, so seasons and furniture do not build up into false alarms
COOLDOWN = 24 * 60


def decode(edges: str) -> bytes | None:
    try:
        raw = base64.b64decode(edges, validate=True)
    except ValueError:
        return None
    return raw if len(raw) == SKETCH_BYTES else None


def similarity(a: bytes, b: bytes) -> float:
    """How much of the outlines two sketches share, 0 to 1 (outlines in both over outlines in either). Two empty sketches are the same."""
    x, y = int.from_bytes(a, "big"), int.from_bytes(b, "big")
    union = (x | y).bit_count()
    return (x & y).bit_count() / union if union else 1.0


def edge_points(a: bytes) -> int:
    return int.from_bytes(a, "big").bit_count()


async def apply(conn, cam, sample: dict) -> list[str]:
    """`cam` carries id, tenant_id and name. `sample`: at (a datetime), brightness, contrast, sharpness, sketch (bytes). Returns the alert types raised."""
    at, raised = sample["at"], []
    st = await conn.fetchrow("SELECT * FROM app.camera_picture_state WHERE camera_id=$1", cam["id"])
    if st is None:
        await conn.execute("INSERT INTO app.camera_picture_state (camera_id, tenant_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", cam["id"], cam["tenant_id"])
        st = await conn.fetchrow("SELECT * FROM app.camera_picture_state WHERE camera_id=$1", cam["id"])
    if st["last_at"] is not None and at <= st["last_at"]:
        return raised                                                   # an old or repeated measurement
    sketch, bright, contrast, sharp = sample["sketch"], sample["brightness"], sample["contrast"], sample["sharpness"]
    light = "night" if bright < NIGHT_BELOW else "day"
    ref, ref_at = st[f"ref_{light}"], st[f"ref_{light}_at"]
    upd: dict = {"last_at": at, "last_brightness": bright, "last_sharpness": sharp}

    # ---- the view: covered, moved, or as usual
    flat = contrast < FLAT_CONTRAST and edge_points(sketch) < FLAT_EDGES
    kind = None
    if flat:
        kind = "covered"
    elif ref is None:
        upd[f"ref_{light}"], upd[f"ref_{light}_at"] = sketch, at         # the first sight of a camera in this light is its usual view
    else:
        sim = similarity(bytes(ref), sketch)
        if sim < MOVED_BELOW:
            kind = "moved"
        elif sim >= SAME_ABOVE and (ref_at is None or at - ref_at >= REFRESH_AFTER):
            upd[f"ref_{light}"], upd[f"ref_{light}_at"] = sketch, at
    if kind is None:
        upd["bad_view_since"] = upd["bad_view_kind"] = None
    else:
        since = st["bad_view_since"] if st["bad_view_kind"] == kind and st["bad_view_since"] else at
        upd["bad_view_since"], upd["bad_view_kind"] = since, kind
        if at - since >= (COVERED_FOR if kind == "covered" else MOVED_FOR):
            pic = await detectors._camera_picture(conn, cam["tenant_id"], cam["id"])
            say = "has been covered" if kind == "covered" else "no longer looks at what it used to"
            if await raise_alert(conn, cam["tenant_id"], "view_changed", title=f"Camera {'covered' if kind == 'covered' else 'position changed'}: {cam['name']}",
                                 body=f"{cam['name']} {say}. Check that nothing is in front of it and that it has not been knocked or turned.", camera_id=cam["id"], camera_name=cam["name"],
                                 details={"what": kind}, dedupe_key=f"view:{cam['id']}", cooldown_minutes=COOLDOWN, evidence_id=pic):
                raised.append("view_changed")
            if kind == "moved":
                upd[f"ref_{light}"], upd[f"ref_{light}_at"] = sketch, at       # it is now where it is: the new usual view
                upd["bad_view_since"] = upd["bad_view_kind"] = None

    # ---- the picture itself: blurry or dark (a covered camera is already reported as covered)
    poor = None
    if not flat:
        base, n = st["baseline_sharp"], st["day_samples"]
        if light == "day":
            if base is not None and n >= BASELINE_AFTER and sharp < base * BLURRY_SHARE:
                poor = "blurry"                                          # not counted in the usual sharpness: a dirty lens must not become "normal"
            else:
                upd["baseline_sharp"] = sharp if base is None else base * 0.98 + sharp * 0.02
                upd["day_samples"] = n + 1
        if bright < DARK_BELOW:
            hrs = await conn.fetchrow("SELECT open_time, close_time, closed_days, timezone FROM app.tenants WHERE id=$1", cam["tenant_id"])
            h = Hours.of(hrs)
            if h.set and h.is_open(at.astimezone(ZoneInfo(hrs["timezone"])).replace(tzinfo=None)):
                poor = "dark"
    if poor is None:
        upd["poor_since"] = upd["poor_kind"] = None
    else:
        since = st["poor_since"] if st["poor_kind"] == poor and st["poor_since"] else at
        upd["poor_since"], upd["poor_kind"] = since, poor
        if at - since >= (BLURRY_FOR if poor == "blurry" else DARK_FOR):
            pic = await detectors._camera_picture(conn, cam["tenant_id"], cam["id"])
            hours = int((at - since).total_seconds() // 3600) or 1
            what = ("much less sharp than it usually is. A dirty or fogged lens, or knocked focus, can cause this" if poor == "blurry"
                    else "almost black while you are open. Check the lamp or the camera's infrared light")
            if await raise_alert(conn, cam["tenant_id"], "picture_poor", title=f"Poor image quality: {cam['name']}",
                                 body=f"The picture from {cam['name']} has been {what} for over {hours} hour{'s' if hours != 1 else ''}.", camera_id=cam["id"], camera_name=cam["name"],
                                 details={"what": poor}, dedupe_key=f"poor:{cam['id']}", cooldown_minutes=COOLDOWN, evidence_id=pic):
                raised.append("picture_poor")
    cols = list(upd)
    await conn.execute(f"UPDATE app.camera_picture_state SET {', '.join(f'{c}=${i + 2}' for i, c in enumerate(cols))} WHERE camera_id=$1", cam["id"], *[upd[c] for c in cols])
    return raised
