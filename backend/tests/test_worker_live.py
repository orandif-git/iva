import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from tests.video_gen import make_video
from worker.live import LatestQueue, StreamReader, build_command, make_stream_reader, mask_url, probe_stream, rtsp_timeout_flag

FAKE = str(Path(__file__).with_name("fake_ffmpeg.py"))
W, H = 64, 36


def fake_reader(tmp_path, attempts, **kw):
    sc = tmp_path / "scenario.json"
    sc.write_text(json.dumps({"state": str(tmp_path / "count"), "w": W, "h": H, "attempts": attempts}))
    kw.setdefault("backoff", (0.05, 0.1, 0.2))
    kw.setdefault("jitter", 0.0)
    r = StreamReader(W, H, 10, lambda: [sys.executable, FAKE, str(sc)], **kw)
    r.start()
    return r


def collect(reader, n, timeout=8.0):
    out, end = [], time.time() + timeout
    while len(out) < n and time.time() < end:
        f = reader.get(0.2)
        if f:
            out.append(f)
    return out


def wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ masking
@pytest.mark.parametrize("text,expect", [
    ("rtsp://admin:Hunter2@10.0.0.9:554/x", "rtsp://***@10.0.0.9:554/x"),
    ("Failed to open rtsps://user:p%40ss@cam.example.in/live: refused", "Failed to open rtsps://***@cam.example.in/live: refused"),
    ("ffmpeg -i rtsp://a:b@h/p -f rawvideo pipe:1", "ffmpeg -i rtsp://***@h/p -f rawvideo pipe:1"),
    ("rtsp://10.0.0.9/no-password", "rtsp://10.0.0.9/no-password"),
    ("no address here", "no address here"), ("", ""), (None, ""),
])
def test_passwords_are_removed_from_text(text, expect):
    assert mask_url(text) == expect


# ------------------------------------------------------------------ command
def test_the_command_asks_for_raw_pictures_of_an_exact_size_at_the_requested_rate():
    c = build_command("rtsp://u:p@10.0.0.9/x", 960, 540, 7.5)
    assert c[c.index("-vf") + 1] == "fps=7.5,scale=960:540"
    assert c[c.index("-pix_fmt") + 1] == "bgr24" and c[c.index("-f") + 1] == "rawvideo" and c[-1] == "pipe:1"
    assert "-an" in c and "-nostdin" in c and "mjpeg" not in c and "image2pipe" not in c
    assert c[c.index("-rtsp_transport") + 1] == "tcp"
    assert c[c.index("-i") + 1] == "rtsp://u:p@10.0.0.9/x"


def test_rtsp_options_are_only_used_for_rtsp_addresses_and_files_play_at_real_speed():
    f = build_command("/tmp/clip.mp4", 320, 180, 5)
    assert "-rtsp_transport" not in f and f.index("-re") < f.index("-i")           # a file is paced like a camera
    assert "-re" not in build_command("rtsp://x/y", 320, 180, 5)                   # a real camera sets its own pace


def test_the_timeout_option_name_follows_the_installed_ffmpeg(tmp_path):
    assert rtsp_timeout_flag() == "-timeout"                                  # FFmpeg 5 and later; -stimeout no longer exists there
    old = tmp_path / "old_ffmpeg"
    old.write_text("#!/bin/sh\necho '  -stimeout  <int64>  set timeout of socket I/O'\n")
    old.chmod(0o755)
    assert rtsp_timeout_flag(str(old)) == "-stimeout"                         # an older FFmpeg that still uses the old name
    assert build_command("rtsp://x/y", 64, 36, 5, ffmpeg=str(old)).count("-stimeout") == 1


# ------------------------------------------------------------------ queue
def test_a_full_queue_drops_the_oldest_picture():
    q = LatestQueue(4)
    for i in range(10):
        q.put(i)
    assert q.dropped == 6 and [q.get(0) for _ in range(4)] == [6, 7, 8, 9] and q.get(0.01) is None


def test_backoff_grows_then_holds_at_its_last_value():
    r = StreamReader(W, H, 5, lambda: ["true"], backoff=(1, 2, 4, 8, 16, 30))
    assert [r._delay(a) for a in (0, 1, 2, 3, 4, 5, 6, 7, 50)] == [1, 1, 2, 4, 8, 16, 30, 30, 30]


# ------------------------------------------------------------------ reader
def test_frames_are_cut_exactly_and_stamped_on_arrival(tmp_path):
    r = fake_reader(tmp_path, [{"frames": 12, "gap": 0.01, "then": "continue"}])
    try:
        frames = collect(r, 12)
        assert len(frames) == 12
        for i, f in enumerate(frames):
            assert f.image.shape == (H, W, 3) and (f.image == i % 256).all(), i          # no frame is ever cut mid-way
        stamps = [f.ts for f in frames]
        assert stamps == sorted(stamps) and abs((datetime.now(timezone.utc) - stamps[-1]).total_seconds()) < 3
        assert {f.epoch for f in frames} == {1} and r.state == "streaming"
    finally:
        r.stop()


def test_it_reconnects_after_failures_and_never_leaks_the_password(tmp_path):
    secret = "rtsp://admin:Hunter2@10.0.0.9:554/Streaming/Channels/102"
    r = fake_reader(tmp_path, [{"fail": f"Error opening input {secret}: Connection refused"}, {"fail": f"{secret}: timed out"},
                               {"frames": 5, "then": "continue"}])
    try:
        assert wait(lambda: r.reconnects >= 1)
        assert r.state in ("reconnecting", "connecting") and "Hunter2" not in (r.last_error or "")
        got = collect(r, 3)
        assert len(got) == 3 and r.state == "streaming" and r.reconnects == 2 and r.down_seconds() == 0.0
        assert "Hunter2" not in (r.last_error or "") and "***" in r.last_error
    finally:
        r.stop()


def test_each_reconnect_starts_a_new_epoch_so_the_caller_knows_tracking_was_interrupted(tmp_path):
    r = fake_reader(tmp_path, [{"frames": 4, "then": "exit"}, {"frames": 4, "then": "exit"}, {"frames": 3, "then": "continue"}])
    try:
        frames = collect(r, 11)
        epochs = [f.epoch for f in frames]
        assert epochs[:4] == [1] * 4 and epochs[4:8] == [2] * 4 and epochs[8:] == [3] * 3
        assert r.reconnects == 2
    finally:
        r.stop()


def test_a_connection_that_stays_open_but_goes_silent_is_restarted(tmp_path):
    r = fake_reader(tmp_path, [{"frames": 3, "then": "hang"}, {"frames": 3, "then": "continue"}], stall_seconds=0.6)
    try:
        frames = collect(r, 6, timeout=10)
        assert [f.epoch for f in frames] == [1, 1, 1, 2, 2, 2]               # the watchdog killed the silent connection
        assert r.reconnects >= 1
    finally:
        r.stop()


def test_a_slow_consumer_always_gets_the_most_recent_pictures(tmp_path):
    r = fake_reader(tmp_path, [{"frames": 60, "gap": 0.002, "then": "continue"}], max_queue=3)
    try:
        assert wait(lambda: r.frames >= 60)                                    # the camera outruns a consumer that has not started
        got = collect(r, 3)
        idx = [int(f.image[0, 0, 0]) for f in got]
        assert idx == sorted(idx) and idx[-1] >= 59 - 3 and r.queue.dropped >= 50     # old pictures were dropped, the newest are kept
    finally:
        r.stop()


def test_stop_is_prompt_even_when_the_connection_is_hung(tmp_path):
    r = fake_reader(tmp_path, [{"frames": 2, "then": "hang"}], stall_seconds=60)
    collect(r, 2)
    t = time.time()
    r.stop()
    assert time.time() - t < 3 and r.state == "stopped"


def test_a_long_outage_is_reported_as_time_down(tmp_path):
    r = fake_reader(tmp_path, [{"fail": "refused"}], backoff=(0.05,))
    try:
        assert wait(lambda: r.reconnects >= 3)
        assert r.state == "reconnecting" and r.down_seconds() > 0.05 and r.last_error == "refused"
    finally:
        r.stop()


# --------------------------------------------------------- the real FFmpeg
@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = tmp_path_factory.mktemp("clip") / "c.mp4"
    return make_video(p, 4, lambda t: ([], []), fps=25)


def test_real_ffmpeg_delivers_exact_pictures_at_the_requested_rate(clip):
    r = StreamReader(320, 180, 5, lambda: build_command(str(clip), 320, 180, 5), backoff=(30,))
    r.start()
    try:
        frames = collect(r, 20)
        assert 18 <= len(frames) <= 21                                         # 4 seconds at 5 fps
        img = frames[0].image
        assert img.shape == (180, 320, 3) and 100 <= img.mean() <= 140 and img.std() < 6      # a flat grey, as drawn (the codec shifts the level a little)
    finally:
        r.stop()


def test_real_ffmpeg_accepts_the_rtsp_options_and_a_refused_connection_is_handled_cleanly():
    url = "rtsp://admin:Pa55w0rd@127.0.0.1:1/live"                             # nothing listens on port 1
    r = StreamReader(64, 36, 5, lambda: build_command(url, 64, 36, 5, timeout_s=2), backoff=(0.1, 0.2), jitter=0)
    r.start()
    try:
        assert wait(lambda: r.reconnects >= 2, timeout=15)
        err = r.last_error or ""
        assert "refused" in err.lower(), err                                   # it got as far as connecting: the options were accepted
        assert "unrecognized" not in err.lower() and "Pa55w0rd" not in err
    finally:
        r.stop()


def test_probe_reads_the_streams_own_rate_and_size(clip):
    info = probe_stream(str(clip))
    assert info["fps"] == pytest.approx(25, abs=1) and (info["width"], info["height"]) == (640, 360)
    assert probe_stream("rtsp://127.0.0.1:1/nothing", timeout_s=2) is None


def test_it_never_asks_the_camera_for_more_frames_than_it_makes(tmp_path):
    slow = make_video(tmp_path / "ten.mp4", 3, lambda t: ([], []), fps=10)
    r = make_stream_reader(str(slow), 160, 90, 25)                              # policy wants 25 fps, the stream only has 10
    r.start()
    try:
        frames = collect(r, 25, timeout=10)
        assert r.effective_fps == 10 and 24 <= len(frames) <= 31               # about 3 s of real frames, none duplicated up to 25 fps
    finally:
        r.stop()


@pytest.mark.skipif(not os.environ.get("GI_TEST_RTSP_URL"), reason="set GI_TEST_RTSP_URL to a live RTSP stream to run this")
def test_a_real_rtsp_session():
    r = make_stream_reader(os.environ["GI_TEST_RTSP_URL"], 640, 360, 5)
    r.start()
    try:
        frames = collect(r, 10, timeout=30)
        assert len(frames) == 10 and frames[0].image.shape == (360, 640, 3) and r.state == "streaming"
    finally:
        r.stop()
