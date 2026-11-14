"""What the AI sees is drawn on the pictures of an alert clip: boxes, marked areas with their counts, the counting line, and the people in view."""
from types import SimpleNamespace

import numpy as np

from worker import overlay


def blank(w=640, h=360):
    return np.full((h, w, 3), 40, np.uint8)


def test_nothing_found_still_says_how_many_people_are_in_view_and_never_changes_the_original():
    src = blank()
    out = overlay.draw(src)
    assert out.shape == src.shape and (src == 40).all(), "the picture the analysis works on is never changed"
    assert not (out == src).all(), "the caption is drawn"


def test_people_get_a_green_box_and_vehicles_a_yellow_one():
    src = blank()
    out = overlay.draw(src, people=[SimpleNamespace(box=(100, 100, 160, 260))], vehicles=[SimpleNamespace(box=(300, 150, 420, 250))])
    assert tuple(out[100, 130]) == overlay.PEOPLE and tuple(out[150, 360]) == overlay.VEHICLE


def test_an_area_is_outlined_named_and_turns_orange_at_its_limit():
    pts = ((0.2, 0.3), (0.8, 0.3), (0.8, 0.9), (0.2, 0.9))
    calm = overlay.draw(blank(), areas=[{"points": pts, "name": "Counter", "count": 2, "over": False}])
    busy = overlay.draw(blank(), areas=[{"points": pts, "name": "Counter", "count": 5, "over": True}])
    assert tuple(calm[int(0.9 * 360), 320]) == overlay.AREA and tuple(busy[int(0.9 * 360), 320]) == overlay.OVER
    assert not (calm == busy).all()


def test_the_counting_line_and_the_plate_zone_are_drawn():
    out = overlay.draw(blank(), line=((0.1, 0.5), (0.9, 0.5), (0, -1)), zone=((0.1, 0.1), (0.4, 0.1), (0.4, 0.3), (0.1, 0.3)))
    assert tuple(out[180, 100]) == overlay.LINE or tuple(out[181, 100]) == overlay.LINE
    assert tuple(out[36, 100]) == overlay.AREA or tuple(out[35, 100]) == overlay.AREA
