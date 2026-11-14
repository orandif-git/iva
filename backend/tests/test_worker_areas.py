"""The worker's side of marked areas: counting people in a shape, timing a queue, timing a phone in a staff area, and what reaches the backend."""
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from tests.helpers import now, setup_cameras
from tests.test_area_alerts import area, draw, turn_on
from worker.areas import AreaConfig, AreaMonitor
from worker.pipeline import CameraConfig, CameraPipeline
from worker.sink import BackendSink, MemorySink
from worker.tracker import Detection, Track

W, H = 640, 360
SQUARE = ((0.1, 0.1), (0.6, 0.1), (0.6, 0.6), (0.1, 0.6))               # the left-top part of the picture


def cfg(kind="area_count", name="Counter", **params):
    return AreaConfig(str(uuid.uuid4()), kind, name, SQUARE, tuple(sorted(params.items())))


def person(i, x, y, w=40, h=100):
    """A confirmed person whose box has its top-left at (x, y) in pixels."""
    return Track(id=i, label="person", box=(x, y, x + w, y + h), score=0.9, confirmed=True)


INSIDE = (100, 60)          # the foot of this person is at (120, 160) of a 640 x 360 picture: inside the shape (x 64..384, y 36..216)
OUTSIDE = (500, 200)


def phone_at(person_box, fraction=0.25):
    x1, y1, x2, y2 = person_box
    cx, cy = (x1 + x2) / 2, y1 + (y2 - y1) * fraction
    return Detection((cx - 6, cy - 6, cx + 6, cy + 6), 0.6, "cell phone")


# ------------------------------------------------------------------------------------------ counting
def test_only_people_standing_inside_the_shape_are_counted():
    m = AreaMonitor([cfg()])
    here, there = person(1, *INSIDE), person(2, *OUTSIDE)
    (r,) = m.update(100.0, W, H, [here, there, person(3, 150, 80)], [])
    assert r["people"] == 2 and r["wait_seconds"] is None and r["phone_seconds"] is None and not r["take_picture"]


def test_an_area_reports_every_ten_seconds_not_every_picture():
    m = AreaMonitor([cfg()])
    got = [len(m.update(100.0 + i * 0.5, W, H, [person(1, *INSIDE)], [])) for i in range(45)]          # 22 seconds of pictures
    assert sum(got) == 3 and got[0] == 1 and got[20] == 1 and got[40] == 1


def test_each_area_is_measured_on_its_own():
    a, b = cfg("area_count", "Counter"), AreaConfig(str(uuid.uuid4()), "area_count", "Door", ((0.7, 0.5), (0.95, 0.5), (0.95, 0.9), (0.7, 0.9)), ())
    m = AreaMonitor([a, b])
    out = {r["area"].name: r["people"] for r in m.update(100.0, W, H, [person(1, *INSIDE), person(2, 470, 200), person(3, 480, 190)], [])}
    assert out == {"Counter": 1, "Door": 2}


# ------------------------------------------------------------------------------------------ queue
def test_the_wait_is_the_longest_time_a_person_still_in_the_queue_has_been_there():
    m = AreaMonitor([cfg("queue_area")], every=1.0)
    first = person(1, *INSIDE)
    waits = []
    for i in range(0, 61, 5):
        crowd = [first] + ([person(2, 200, 60)] if i >= 30 else [])
        waits.append(m.update(100.0 + i, W, H, crowd, [])[0]["wait_seconds"])
    assert waits[0] == 0 and waits[6] == 30 and waits[-1] == 60                                       # the first person has waited a minute; the later one only thirty seconds


def test_a_person_missed_for_a_moment_keeps_their_wait_but_one_who_left_starts_again():
    m = AreaMonitor([cfg("queue_area")], every=1.0)
    p = person(1, *INSIDE)
    m.update(100.0, W, H, [p], [])
    m.update(110.0, W, H, [], [])                                                                      # not seen for a second
    assert m.update(112.0, W, H, [p], [])[0]["wait_seconds"] == 12.0                                   # missed for two seconds only: still the same wait since 100
    m.update(113.0, W, H, [], [])
    assert m.update(119.0, W, H, [p], [])[0]["wait_seconds"] == 19.0                                   # lost by the tracker for six seconds in a crowd: still the same person waiting
    m.update(120.0, W, H, [], [])
    m.update(129.0, W, H, [], [])                                                                      # gone for nine seconds: they left
    assert m.update(131.0, W, H, [p], [])[0]["wait_seconds"] == 0.0


# ------------------------------------------------------------------------------------------ phones
def run_phone(m, seconds, p, phones_on=True, start=100.0):
    last = None
    for i in range(int(seconds * 2) + 1):
        last = m.update(start + i * 0.5, W, H, [p], [phone_at(p.box)] if phones_on else [])
    return last


def test_a_phone_in_the_upper_body_of_a_person_in_the_area_is_timed():
    m = AreaMonitor([cfg("staff_area", seconds=20)], every=1.0)
    (r,) = run_phone(m, 12, person(1, *INSIDE))
    assert r["phone_seconds"] == 12.0 and not r["take_picture"]


def test_a_picture_is_asked_for_once_when_the_phone_has_been_in_use_for_the_time_set_and_again_after_a_break():
    m = AreaMonitor([cfg("staff_area", seconds=20)], every=1.0)
    p = person(1, *INSIDE)
    shots = 0
    for i in range(0, 80):                                                                             # 40 seconds of phone use
        shots += sum(r["take_picture"] for r in m.update(100.0 + i * 0.5, W, H, [p], [phone_at(p.box)]))
    assert shots == 1
    for i in range(0, 20):                                                                             # ten seconds without the phone: the episode ends
        m.update(140.0 + i * 0.5, W, H, [p], [])
    assert m.update(150.0, W, H, [p], [])[0]["phone_seconds"] == 0.0
    shots = 0
    for i in range(0, 50):
        shots += sum(r["take_picture"] for r in m.update(160.0 + i * 0.5, W, H, [p], [phone_at(p.box)]))
    assert shots == 1                                                                                  # a new episode, a new picture


def test_a_queue_or_a_crowd_at_its_limit_gets_a_picture_when_it_gets_there_and_another_every_few_minutes_while_it_stays():
    for kind, params in (("queue_area", {"length": 3, "minutes": 2, "picture_minutes": 2}), ("area_count", {"limit": 3, "minutes": 2, "picture_minutes": 2})):
        m = AreaMonitor([cfg(kind, **params)], every=1.0)
        crowd = [person(i, 100 + i * 5, 60) for i in range(1, 5)]                                    # four people inside: over the limit of three
        shots = []
        for i in range(0, 600):                                                                       # five minutes, a report every second
            for r in m.update(100.0 + i * 0.5, W, H, crowd, []):
                if r["take_picture"]:
                    shots.append(i * 0.5)
        assert shots[0] == 0.0 and len(shots) == 3 and all(b - a >= 120 for a, b in zip(shots, shots[1:])), (kind, shots)      # at once, then every 2 minutes (the customer chose 2)
        for i in range(0, 4):                                                                         # the crowd goes: under the limit
            m.update(400.0 + i, W, H, crowd[:1], [])
        assert [r["take_picture"] for r in m.update(405.0, W, H, crowd, [])] == [True], "a new episode starts again with a picture"


def test_the_customer_sets_how_often_another_picture_is_taken_and_zero_means_none():
    crowd = [person(i, 100 + i * 5, 60) for i in range(1, 5)]
    def shots(**params):
        m = AreaMonitor([cfg("queue_area", length=3, minutes=2, **params)], every=1.0)
        return [i * 0.5 for i in range(0, 600) for r in m.update(100.0 + i * 0.5, W, H, crowd, []) if r["take_picture"]]
    assert len(shots(picture_minutes=1)) == 5, "every minute for five minutes: at 0, 60, 120, 180, 240 seconds"
    assert len(shots(picture_minutes=5)) == 1, "once, then not again within five minutes"
    assert shots(picture_minutes=0) == [], "the customer turned pictures off"


def test_without_a_choice_another_picture_is_taken_every_fifteen_minutes():
    m = AreaMonitor([cfg("queue_area", length=3, minutes=2)], every=1.0)
    crowd = [person(i, 100 + i * 5, 60) for i in range(1, 5)]
    shots = [i * 0.5 for i in range(0, 4000) for r in m.update(100.0 + i * 0.5, W, H, crowd, []) if r["take_picture"]]       # about 33 minutes
    assert len(shots) == 3 and shots[1] - shots[0] >= 900


def test_a_queue_or_crowd_under_its_limit_takes_no_pictures():
    m = AreaMonitor([cfg("queue_area", length=5, minutes=2)], every=1.0)
    crowd = [person(i, 100 + i * 5, 60) for i in range(1, 4)]
    assert not any(r["take_picture"] for i in range(0, 100) for r in m.update(100.0 + i, W, H, crowd, []))


def test_a_short_break_does_not_end_the_time():
    m = AreaMonitor([cfg("staff_area", seconds=20)], every=1.0)
    p = person(1, *INSIDE)
    for i in range(0, 20):
        m.update(100.0 + i * 0.5, W, H, [p], [phone_at(p.box)])
    m.update(110.0, W, H, [p], [])                                                                     # the phone was missed in one picture
    (r,) = m.update(111.0, W, H, [p], [phone_at(p.box)])
    assert r["phone_seconds"] == 11.0


def test_things_that_are_not_phone_use_are_not_timed():
    p = person(1, *INSIDE)
    m = AreaMonitor([cfg("staff_area")], every=1.0)
    assert run_phone(m, 30, p, phones_on=False)[0]["phone_seconds"] == 0.0                             # no phone at all
    m = AreaMonitor([cfg("staff_area")], every=1.0)
    low = [Detection((p.box[0] + 10, p.box[3] - 10, p.box[0] + 20, p.box[3] - 2), 0.6, "cell phone")]    # at the feet: on the floor or the counter
    assert all(m.update(100.0 + i, W, H, [p], low)[0]["phone_seconds"] == 0.0 for i in range(30))
    m = AreaMonitor([cfg("staff_area")], every=1.0)
    far = [Detection((600, 300, 612, 312), 0.6, "cell phone")]                                          # nobody is holding it
    assert all(m.update(100.0 + i, W, H, [p], far)[0]["phone_seconds"] == 0.0 for i in range(30))
    m = AreaMonitor([cfg("staff_area")], every=1.0)
    outsider = person(2, *OUTSIDE)                                                                      # a customer, outside the staff area, on the phone
    assert all(m.update(100.0 + i, W, H, [outsider], [phone_at(outsider.box)])[0]["phone_seconds"] == 0.0 for i in range(30))


def test_the_other_kinds_of_area_never_time_phones():
    m = AreaMonitor([cfg("area_count"), cfg("queue_area")], every=1.0)
    p = person(1, *INSIDE)
    out = m.update(100.0, W, H, [p], [phone_at(p.box)])
    assert [r["phone_seconds"] for r in out] == [None, None]


# ------------------------------------------------------------------------------------------ the settings the worker is given
def test_the_worker_reads_the_areas_and_a_changed_limit_makes_a_new_configuration():
    j = {"camera_id": "c1", "name": "Hall", "areas": [{"id": "z1", "kind": "area_count", "name": "Counter", "points": [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6]], "params": {"limit": 5, "minutes": 2}}]}
    a = CameraConfig.from_backend(j)
    assert len(a.areas) == 1 and a.areas[0].name == "Counter" and a.areas[0].param("limit") == 5
    assert CameraConfig.from_backend(j) == a                                                           # nothing changed: the picture is not interrupted
    j2 = {**j, "areas": [{**j["areas"][0], "points": [[0.1, 0.1], [0.7, 0.1], [0.6, 0.6]]}]}
    assert CameraConfig.from_backend(j2) != a                                                          # a moved shape starts a new analysis
    assert CameraConfig.from_backend({"camera_id": "c1"}).areas == ()


# ------------------------------------------------------------------------------------------ the whole pipeline
class Script:
    """A detector that shows what the test says for each picture: (people boxes, phone boxes)."""
    last_ms = 0.0

    def __init__(self, scene):
        self.scene, self.calls = scene, 0

    def detect(self, frame):
        self.calls += 1
        boxes, phones = self.scene(self.calls)
        return [Detection(tuple(map(float, b)), 0.9, "person") for b in boxes] + [Detection(tuple(map(float, b)), 0.6, "cell phone") for b in phones]


def feed(pipe, seconds, step=0.5, start=None):
    start = start or datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)
    frame = np.zeros((H, W, 3), np.uint8)
    for i in range(int(seconds / step) + 1):
        pipe.process(frame, start + timedelta(seconds=i * step))


def pipe_for(areas, scene, sink):
    c = CameraConfig("cam", "Hall", areas=tuple(AreaConfig.from_backend(a) for a in areas))
    return CameraPipeline(c, Script(scene), None, sink)


def test_a_camera_with_only_areas_counts_people_in_them_without_a_counting_line():
    sink = MemorySink()
    zone = {"id": "z1", "kind": "area_count", "name": "Counter", "points": [list(p) for p in SQUARE], "params": {"limit": 3}}
    pipe = pipe_for([zone], lambda n: ([(100, 60, 140, 160), (160, 70, 200, 170), (500, 200, 540, 300)], []), sink)
    feed(pipe, 25)
    assert pipe.counter is None and pipe.totals == {"in": 0, "out": 0}
    assert sink.footfall == {} and sink.zones and {z["people"] for z in sink.zones} == {2}
    assert all(z["zone_id"] == "z1" and z["camera_id"] == "cam" and z["at"].endswith("Z") for z in sink.zones) and len(sink.zones) == 3


def test_phone_use_in_a_staff_area_reaches_the_sink_with_a_picture_once_the_time_is_reached():
    sink = MemorySink()
    zone = {"id": "z9", "kind": "staff_area", "name": "Behind the counter", "points": [list(p) for p in SQUARE], "params": {"seconds": 20}}
    box = (100, 60, 140, 160)
    pipe = pipe_for([zone], lambda n: ([box], [(118, 78, 130, 90)]), sink)
    feed(pipe, 45)
    held = [z["phone_seconds"] for z in sink.zones]
    assert held == sorted(held) and held[0] < 5 and held[-1] >= 40
    assert [z["has_picture"] for z in sink.zones].count(True) == 1 and sink.zones[[z["has_picture"] for z in sink.zones].index(True)]["phone_seconds"] >= 20


def test_a_camera_with_no_areas_does_no_area_work():
    det = Script(lambda n: ([], []))
    pipe = CameraPipeline(CameraConfig("cam", "Hall"), det, None, MemorySink())
    feed(pipe, 5)
    assert det.calls == 0


# ------------------------------------------------------------------------------------------ worker to backend, and an alert
async def test_what_the_worker_measures_reaches_the_backend_and_a_long_queue_becomes_an_alert(client, make, su):
    t, u, (cam,), _ = await setup_cameras(client, make)
    await turn_on(client, u, "queue_long")
    (z,) = await draw(client, u, cam, area("queue_area", "Front queue", length=3, minutes=2, wait_minutes=0))
    cfg_json = (await client.get(f"/internal/v1/cameras/{cam}/config", headers=make.service())).json()
    c = CameraConfig.from_backend(cfg_json)
    assert len(c.areas) == 1 and c.areas[0].id == z["id"] and c.areas[0].param("length") == 3
    sink = BackendSink(client, make.service()["Authorization"].split()[1])
    people = [(100, 60, 140, 160), (160, 70, 200, 170), (200, 60, 240, 160), (240, 80, 280, 180)]
    pipe = CameraPipeline(c, Script(lambda n: (people, [])), None, sink)
    feed(pipe, 130, start=now() - timedelta(seconds=135))
    assert await sink.close() == 0 and not sink.rejected
    assert await su.fetchval("SELECT count(*) FROM app.zone_samples WHERE zone_id=$1", uuid.UUID(z["id"])) >= 13
    (alert,) = await su.fetch("SELECT title, body FROM app.alerts WHERE tenant_id=$1 AND rule_type='queue_long'", t)
    assert alert["title"] == "Long queue at Front queue" and "4 people" in alert["body"]


# ------------------------------------------------------------------------------------------ activity in an area: people or vehicles
def vehicle(i, x, y, w=120, h=70):
    return Track(id=i, label="car", box=(x, y, x + w, y + h), score=0.9, confirmed=True)


def test_an_activity_area_looks_for_the_kind_of_thing_it_was_set_for():
    people_area, car_area = cfg("activity_area", "Store room", target="person"), cfg("activity_area", "Yard", target="vehicle")
    m = AreaMonitor([people_area, car_area], every=10, warmup=0)
    assert m.wants_vehicles is True and AreaMonitor([people_area], every=10).wants_vehicles is False
    out = {r["area"].name: r for r in m.update(100.0, W, H, [person(1, *INSIDE)], [], [vehicle(7, 120, 100)])}
    assert out["Store room"]["people"] == 1 and out["Yard"]["people"] == 1                      # one person in the first, one vehicle in the second
    out = {r["area"].name: r for r in AreaMonitor([people_area, car_area], every=10, warmup=0).update(100.0, W, H, [], [], [vehicle(7, 120, 100)])}
    assert out["Store room"]["people"] == 0 and out["Yard"]["people"] == 1                      # a vehicle is not a person


def test_an_activity_area_takes_a_picture_when_something_is_first_seen_and_then_every_few_minutes():
    m = AreaMonitor([cfg("activity_area", "Store room", target="person", picture_minutes=10)], every=10, warmup=0)
    first = m.update(100.0, W, H, [person(1, *INSIDE)], [])[0]
    assert first["take_picture"] is True
    assert m.update(110.0, W, H, [person(1, *INSIDE)], [])[0]["take_picture"] is False
    assert m.update(100.0 + 601, W, H, [person(1, *INSIDE)], [])[0]["take_picture"] is True


class CarScript:
    """Finds one car, drawn at the given box, in every picture."""
    last_ms = 0.0

    def __init__(self, box):
        self.box = box

    def detect(self, frame):
        return [Detection(tuple(map(float, self.box)), 0.9, "car")]


def test_a_camera_that_reads_no_plates_still_follows_vehicles_for_an_activity_area():
    sink = MemorySink()
    zone = {"id": "z7", "kind": "activity_area", "name": "Yard", "points": [list(p) for p in SQUARE], "params": {"target": "vehicle"}}
    c = CameraConfig("cam", "Gate", areas=(AreaConfig.from_backend(zone),))
    assert c.use_anpr is False
    pipe = CameraPipeline(c, CarScript((120, 100, 240, 170)), None, sink)
    feed(pipe, 30)
    assert sink.zones and sink.zones[-1]["people"] == 1                     # the vehicle is counted in the area although no plates are read


def test_the_clip_pictures_show_what_the_ai_found_and_the_analysed_picture_is_untouched():
    """Each analysed picture goes into the clip buffer after the analysis, with the people, the area and its count drawn on a copy."""
    import cv2
    sink = MemorySink()
    zone = {"id": "z1", "kind": "area_count", "name": "Counter", "points": [list(p) for p in SQUARE], "params": {"limit": 2}}
    c = CameraConfig("cam", "Hall", areas=(AreaConfig.from_backend(zone),), clips=True)
    pipe = CameraPipeline(c, Script(lambda n: ([(100, 60, 140, 160), (160, 70, 200, 170)], [])), None, sink)
    clips = pipe.clips
    assert clips is not None
    frame = np.zeros((H, W, 3), np.uint8)
    start = datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(30):
        pipe.process(frame, start + timedelta(seconds=i * 0.5))
    assert (frame == 0).all(), "the analysed picture is never drawn on"
    jpeg = clips.ring.frames[-1][1]
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert (img > 40).any(), "boxes and the area outline are drawn on the clip picture"
    assert clips.stats["frames_buffered"] == 30
