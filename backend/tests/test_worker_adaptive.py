from types import SimpleNamespace

import numpy as np
import pytest

from worker.adaptive import ACTIVE, IDLE, MotionDetector, Policy, RateController

QUIET = SimpleNamespace(relevant=False, tracks=0, visits=0)
BUSY = SimpleNamespace(relevant=True, tracks=1, visits=0)


def run(ctl, seconds, capture=10.0, busy_between=(), start=0.0, motion=lambda t: False):
    """Feed frames arriving at `capture` fps. busy_between: [(from, to)] seconds when a person is in view. Returns processed timestamps."""
    done, n = [], int(seconds * capture)
    for i in range(n):
        t = start + i / capture
        if ctl.should_process(t, motion(t)):
            done.append(t)
            ctl.observe(BUSY if any(a <= t < b for a, b in busy_between) else QUIET, t)
    return done


def rate(ts, a, b):
    return sum(a <= t < b for t in ts) / (b - a)


def test_an_empty_scene_is_analysed_at_the_idle_rate():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10)
    done = run(ctl, 30)
    assert ctl.state == IDLE and 1.8 <= len(done) / 30 <= 2.2


def test_someone_arriving_speeds_it_up_and_leaving_slows_it_down_after_the_hold():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10, hold_seconds=3.0)
    done = run(ctl, 40, busy_between=[(10, 20)])
    assert 1.8 <= rate(done, 0, 9.5) <= 2.2                                  # idle before
    assert 8.5 <= rate(done, 10.7, 20) <= 10.5                               # active while someone is there
    assert 1.5 <= rate(done, 24, 40) <= 2.5                                  # idle again after the 3 s hold
    assert 8.0 <= rate(done, 20.2, 22.8) <= 10.5                             # still fast during the hold, so a stopped vehicle is not lost


def test_the_active_rate_starts_on_the_very_next_frame_after_a_sighting():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10)
    done = run(ctl, 12, busy_between=[(5.0, 12)])
    after = [t for t in done if t > 5.0]
    assert after[0] - 5.0 <= 0.55 and after[1] - after[0] == pytest.approx(0.1, abs=0.02)      # first look within one idle interval, then fast


def test_fixed_mode_never_changes_rate():
    ctl = RateController(Policy("fixed", 2, 15, fixed_fps=5), capture_fps=5)
    done = run(ctl, 30, capture=5, busy_between=[(10, 20)])
    assert ctl.state == ACTIVE and all(4.8 <= rate(done, a, a + 10) <= 5.2 for a in (0, 10, 20))


def test_idle_rate_cannot_exceed_the_active_rate():
    ctl = RateController(Policy("adaptive", idle_fps=8, active_fps=3), capture_fps=3)
    assert ctl.target_fps == 3


def test_movement_between_idle_samples_wakes_it_early_but_not_constantly():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10, motion_min_interval=0.5)
    quiet = run(RateController(Policy("adaptive", 2, 10), capture_fps=10), 20)
    noisy = run(ctl, 20, motion=lambda t: True)                              # constant movement, e.g. swaying trees
    assert len(noisy) > len(quiet)                                           # it does look more often...
    assert len(noisy) / 20 <= 4.0                                            # ...but is capped by the motion interval, never flat out


def test_a_new_policy_takes_effect_at_once():
    ctl = RateController(Policy("adaptive", 2, 5), capture_fps=5)
    first = run(ctl, 10, capture=5, busy_between=[(0, 10)])
    ctl.set_policy(Policy("adaptive", 2, 15), capture_fps=15)
    second = run(ctl, 10, capture=15, busy_between=[(10, 20)], start=10.0)
    assert 4.5 <= len(first) / 10 <= 5.2 and 13.5 <= len(second) / 10 <= 15.5


def test_after_a_stream_gap_it_restarts_slowly_without_a_burst_of_catch_up_frames():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10)
    run(ctl, 10, busy_between=[(0, 10)])
    ctl.reset_after_gap()
    assert ctl.state == IDLE
    done = run(ctl, 5, start=100.0)                                          # 90 seconds later
    assert 1.5 <= len(done) / 5 <= 2.5


def test_the_average_rate_is_reported_for_the_health_heartbeat():
    ctl = RateController(Policy("adaptive", 2, 10), capture_fps=10)
    run(ctl, 60, busy_between=[(20, 26)])
    avg = ctl.average_fps()
    assert 2.5 < avg < 5.0                                                   # mostly idle, a little active
    ctl.reset_stats()
    assert ctl.average_fps() is None


def test_adaptive_costs_a_fraction_of_a_constant_rate_on_a_mostly_empty_scene():
    busy = [(100, 112), (300, 320), (500, 506)]                              # 38 busy seconds in 10 minutes
    adaptive = RateController(Policy("adaptive", 2, 10), capture_fps=10)
    fixed = RateController(Policy("fixed", fixed_fps=10), capture_fps=10)
    a = run(adaptive, 600, busy_between=busy)
    f = run(fixed, 600, busy_between=busy)
    assert len(a) < 0.30 * len(f)                                            # far fewer detector runs for the same 10 fps when it matters


# ---------------------------------------------------------------- motion
def frame(level=100, box=None):
    f = np.full((360, 640, 3), level, np.uint8)
    if box:
        x, y = box
        f[y:y + 80, x:x + 40] = 220
    return f


def test_motion_is_seen_when_something_enters_and_not_when_nothing_changes():
    m = MotionDetector()
    assert m.update(frame()) is False                                        # first frame is just the reference
    assert all(m.update(frame()) is False for _ in range(5))
    assert m.update(frame(box=(300, 150))) is True


def test_slow_lighting_changes_are_not_motion():
    m = MotionDetector()
    m.update(frame(100))
    assert not any(m.update(frame(100 + i)) for i in range(1, 20))           # the light drifts by one level per frame


def test_a_small_flicker_is_not_motion_but_a_person_sized_change_is():
    m = MotionDetector()
    m.update(frame())
    noisy = frame().astype(np.int16) + np.random.default_rng(0).integers(-6, 7, (360, 640, 3))
    assert m.update(np.clip(noisy, 0, 255).astype(np.uint8)) is False
    assert m.update(frame(box=(100, 100))) is True


def test_motion_detector_forgets_the_background_on_reset():
    m = MotionDetector()
    m.update(frame())
    m.reset()
    assert m.update(frame(box=(300, 150))) is False                          # becomes the new reference
