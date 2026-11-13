"""The worker side of clips: the picture buffer, resampling to a steady video, real H.264 encoding, and the recorder that ties them together."""
import asyncio
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import cv2
import httpx
import numpy as np
import pytest

from worker.clips import ClipRecorder, FrameRing, encode_mp4, pick_segment, resample
from worker.sink import BackendSink

T0 = 1_800_000_000.0


def frame(i: int, w=320, h=180):
    """A picture with a box that moves right as i grows, so a decoded video shows which pictures it holds."""
    img = np.full((h, w, 3), 40, np.uint8)
    x = 10 + (i * 6) % (w - 60)
    cv2.rectangle(img, (x, 60), (x + 40, 110), (0, 200, 255), -1)
    return img


def jpeg(i: int) -> bytes:
    return cv2.imencode(".jpg", frame(i))[1].tobytes()


def probe(data: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data); f.flush()
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", f.name], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = [s for s in j["streams"] if s["codec_type"] == "video"]
    return {"video": v[0], "streams": len(j["streams"]), "duration": float(j["format"]["duration"]), "frames": int(v[0]["nb_read_frames"])}


# ------------------------------------------------------------------------ pure pieces
def test_the_ring_keeps_only_recent_pictures_and_ignores_repeats():
    r = FrameRing(keep_seconds=10)
    for i in range(40):
        r.add(T0 + i, b"%d" % i)
    r.add(T0 + 39, b"dup"); r.add(T0 + 5, b"old")
    assert r.frames[0][0] == T0 + 29 and r.newest == T0 + 39 and len(r.frames) == 11
    assert [f[1] for f in r.window(T0 + 35, T0 + 37)] == [b"35", b"36", b"37"]


def test_a_break_in_the_picture_is_not_bridged():
    frames = [(T0 + t, b"x%d" % t) for t in (0, 1, 2, 3, 10, 11, 12, 13, 14)]
    assert [f[0] - T0 for f in pick_segment(frames, T0 + 12)] == [10, 11, 12, 13, 14]       # the moment is in the second stretch
    assert [f[0] - T0 for f in pick_segment(frames, T0 + 1)] == [0, 1, 2, 3]
    assert [f[0] - T0 for f in pick_segment(frames, T0 + 5)] == [0, 1, 2, 3]                # in the gap: the nearest stretch
    assert pick_segment([], T0) == []


def test_uneven_pictures_become_a_steady_five_per_second():
    frames = [(T0 + 0.0, b"A"), (T0 + 0.5, b"B"), (T0 + 1.5, b"C"), (T0 + 2.0, b"D")]
    out = resample(frames, 5)
    assert len(out) == 11                                                  # two seconds at five a second, plus the first
    assert out[:3] == [b"A"] * 3 and out[3] == b"B" and out[7] == b"B" and out[8] == b"C" and out[9] == b"C" and out[10] == b"D"      # latest picture at or before each step
    assert resample([(T0, b"only")], 5) == [b"only"]


# ------------------------------------------------------------------------ real encoding
async def test_the_clip_is_h264_that_whatsapp_and_browsers_play():
    data = await encode_mp4([jpeg(i) for i in range(45)], fps=5)
    p = probe(data)
    v = p["video"]
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and v["profile"] == "Main" and p["streams"] == 1          # no audio track
    assert p["frames"] == 45 and abs(p["duration"] - 9.0) < 0.3 and v["width"] % 2 == 0 and v["height"] % 2 == 0
    assert data[4:8] == b"ftyp" and data.index(b"moov") < data.index(b"mdat")                                             # starts playing before it has all downloaded
    assert len(data) < 500_000


async def test_odd_sized_pictures_and_large_ones_are_fitted():
    odd = cv2.imencode(".jpg", np.full((181, 321, 3), 90, np.uint8))[1].tobytes()
    p = probe(await encode_mp4([odd] * 6))
    assert p["video"]["width"] % 2 == 0 and p["video"]["height"] % 2 == 0
    big = cv2.imencode(".jpg", np.random.randint(0, 255, (1080, 1920, 3), np.uint8))[1].tobytes()
    assert probe(await encode_mp4([big] * 5))["video"]["width"] <= 960


async def test_a_clip_that_cannot_be_made_small_enough_is_dropped():
    noisy = [cv2.imencode(".jpg", np.random.randint(0, 255, (360, 640, 3), np.uint8))[1].tobytes() for _ in range(10)]
    assert await encode_mp4(noisy, max_bytes=2_000) is None
    assert await encode_mp4([]) is None
    assert await encode_mp4([b"not a picture"] * 3) is None
    assert await encode_mp4([jpeg(1)] * 3, ffmpeg="/nonexistent/ffmpeg") is None


# ------------------------------------------------------------------------ the recorder
class FakeSink:
    def __init__(self, outcomes=("ok",)):
        self.uploads, self.outcomes, self.clip_handler = [], list(outcomes), None

    async def upload_clip(self, alert_id, data, span=None):
        self.uploads.append((alert_id, data))
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def fill(rec: ClipRecorder, start: float, end: float, fps=5.0, first=0):
    t, i = start, first
    while t <= end:
        rec.add(frame(i), datetime.fromtimestamp(t, timezone.utc))
        t += 1 / fps
        i += 1


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


async def test_a_clip_covers_five_seconds_before_and_four_after():
    sink = FakeSink()
    rec = ClipRecorder(sink, retry_delays=(0.0, 0.0))
    fill(rec, T0, T0 + 20)
    rec.request("cam", iso(T0 + 12), "alert-1")
    await rec.drain(30)
    assert [u[0] for u in sink.uploads] == ["alert-1"] and rec.stats["uploaded"] == 1
    p = probe(sink.uploads[0][1])
    assert abs(p["duration"] - 9.2) < 0.5 and 44 <= p["frames"] <= 47                    # 5 s before + 4 s after at 5 a second


async def test_it_waits_for_the_seconds_after_the_moment_to_arrive():
    sink = FakeSink()
    rec = ClipRecorder(sink, extra_wait=8.0)
    fill(rec, T0, T0 + 12)                                                              # the moment is T0+12: nothing after it yet
    rec.request("cam", iso(T0 + 12), "alert-2")
    await asyncio.sleep(0.6)
    assert sink.uploads == []                                                           # still waiting
    fill(rec, T0 + 12.2, T0 + 17, first=100)                                            # the rest arrives
    await asyncio.wait_for(rec.drain(30), 20)
    assert len(sink.uploads) == 1 and probe(sink.uploads[0][1])["duration"] > 8.5


async def test_when_the_stream_ends_it_uses_what_there_is():
    sink = FakeSink()
    rec = ClipRecorder(sink, extra_wait=30.0)
    fill(rec, T0, T0 + 12)
    rec.request("cam", iso(T0 + 12), "alert-3")
    await asyncio.sleep(0.3)
    await asyncio.wait_for(rec.drain(30), 20)                                           # drain tells it the stream is over
    assert len(sink.uploads) == 1 and 4.5 < probe(sink.uploads[0][1])["duration"] < 6.5


async def test_no_pictures_means_no_clip_and_no_upload():
    sink = FakeSink()
    rec = ClipRecorder(sink)
    rec.request("cam", iso(T0), "alert-4")
    fill(rec, T0 + 500, T0 + 501)                                                       # pictures exist, but not around the moment
    await rec.drain(5)
    assert sink.uploads == [] and rec.stats["failed"] == 1
    rec.request("cam", "not a time", "alert-5")
    assert rec.stats["requested"] == 1


async def test_upload_is_retried_only_when_worth_it():
    s1 = FakeSink(["retry", "retry", "ok"]); r1 = ClipRecorder(s1, retry_delays=(0.0, 0.0)); fill(r1, T0, T0 + 20); r1.request("c", iso(T0 + 12), "a"); await r1.drain(30)
    assert len(s1.uploads) == 3 and r1.stats["uploaded"] == 1
    s2 = FakeSink(["drop"]); r2 = ClipRecorder(s2, retry_delays=(0.0, 0.0)); fill(r2, T0, T0 + 20); r2.request("c", iso(T0 + 12), "a"); await r2.drain(30)
    assert len(s2.uploads) == 1 and r2.stats["failed"] == 1
    s3 = FakeSink(["retry"]); r3 = ClipRecorder(s3, retry_delays=(0.0, 0.0)); fill(r3, T0, T0 + 20); r3.request("c", iso(T0 + 12), "a"); await r3.drain(30)
    assert len(s3.uploads) == 3 and r3.stats["failed"] == 1                             # gives up after the retries


async def test_a_reconnect_in_the_middle_does_not_freeze_the_clip():
    sink = FakeSink()
    rec = ClipRecorder(sink)
    fill(rec, T0, T0 + 9)
    fill(rec, T0 + 20, T0 + 30, first=200)                                              # the stream was down for 11 seconds
    rec.request("cam", iso(T0 + 25), "alert-6")
    await rec.drain(30)
    assert 8.0 < probe(sink.uploads[0][1])["duration"] < 10.5                           # only the stretch around the moment, not 30 seconds with a frozen middle


async def test_several_requests_are_made_independently():
    sink = FakeSink()
    rec = ClipRecorder(sink, concurrency=1)
    fill(rec, T0, T0 + 25)
    for n, c in enumerate((10, 14, 18)):
        rec.request("cam", iso(T0 + c), f"alert-{n}")
    await rec.drain(60)
    assert sorted(u[0] for u in sink.uploads) == ["alert-0", "alert-1", "alert-2"]


# ------------------------------------------------------------------------ the sink
def mock_sink(handler):
    return BackendSink(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://b"), "t" * 40, clock=lambda: 1e9)


async def test_the_sink_hands_clip_requests_to_the_right_events():
    got = []

    def handler(req):
        return httpx.Response(200, json={"accepted": 3, "duplicates": 0, "rejected": [], "clips": [{"index": 2, "alert_id": "al-9", "seconds_before": 6, "seconds_after": 3}]})
    sink = mock_sink(handler)
    sink.clip_handler = lambda *a: got.append(a)
    for i, p in enumerate(("AAA", "BBB", "CCC")):
        sink.add_plate("cam-1", {"plate_raw": p, "seen_at": f"2026-11-05T10:0{i}:00Z"})
    await sink.flush(force=True)
    assert got == [("cam-1", "2026-11-05T10:02:00Z", "al-9", 6, 3)]


async def test_a_failing_clip_handler_never_loses_the_plates():
    def handler(req):
        return httpx.Response(200, json={"accepted": 1, "duplicates": 0, "rejected": [], "clips": [{"index": 0, "alert_id": "x", "seconds_before": 5, "seconds_after": 4}]})
    sink = mock_sink(handler)
    sink.clip_handler = lambda *a: 1 / 0
    sink.add_plate("c", {"plate_raw": "AAA", "seen_at": "2026-11-05T10:00:00Z"})
    assert await sink.flush(force=True) and sink.stats["plates_sent"] == 1
    sink.clip_handler = None
    sink.add_plate("c", {"plate_raw": "BBB", "seen_at": "2026-11-05T10:00:00Z"})
    assert await sink.flush(force=True)                                                 # a response with clips and no handler is fine too


@pytest.mark.parametrize("status,expected", [(201, "ok"), (200, "ok"), (500, "retry"), (503, "retry"), (429, "retry"), (409, "drop"), (413, "drop"), (415, "drop"), (404, "drop"), (403, "drop")])
async def test_upload_outcomes(status, expected):
    seen = {}

    def handler(req):
        seen["path"], seen["ct"] = req.url.path, req.headers["content-type"]
        return httpx.Response(status, json={})
    assert await mock_sink(handler).upload_clip("abc", b"\x00\x00\x00\x18ftypmp42") == expected
    assert seen == {"path": "/internal/v1/alerts/abc/clip", "ct": "video/mp4"}


async def test_a_network_error_is_retried():
    def handler(req):
        raise httpx.ConnectError("down")
    assert await mock_sink(handler).upload_clip("abc", b"x") == "retry"


# ------------------------------------------------------------------------ the pipeline
class NoDetector:
    last_ms = 0.0

    def detect(self, frame):
        return []


class NoReader:
    last_ms = 0.0

    def read(self, crop):
        return []


def test_the_pipeline_buffers_pictures_only_when_clips_are_on():
    from worker.pipeline import CameraConfig, CameraPipeline
    from worker.sink import MemorySink
    off = MemorySink()
    p_off = CameraPipeline(CameraConfig.from_backend({"camera_id": "c", "use_anpr": True}), NoDetector(), NoReader(), off)
    p_off.process(frame(1), datetime.fromtimestamp(T0, timezone.utc))
    assert p_off.clips is None and off.clip_handler is None                         # nothing buffered, no cost

    on = MemorySink()
    cfg = CameraConfig.from_backend({"camera_id": "c", "use_anpr": True, "clips": True})
    assert cfg.clips and cfg != CameraConfig.from_backend({"camera_id": "c", "use_anpr": True})       # a change in the setting rebuilds the pipeline
    p = CameraPipeline(cfg, NoDetector(), NoReader(), on)
    for i in range(10):
        p.process(frame(i), datetime.fromtimestamp(T0 + i * 0.2, timezone.utc))
    assert len(p.clips.ring.frames) == 10 and on.clip_handler == p.clips.request
    # footfall-only cameras never record, even if the flag arrives
    q = CameraPipeline(CameraConfig.from_backend({"camera_id": "c", "use_footfall": True, "clips": True}), NoDetector(), None, MemorySink())
    assert q.clips is None


async def test_the_pipeline_drains_pending_clips():
    from worker.pipeline import CameraConfig, CameraPipeline
    sink = FakeSink()
    p = CameraPipeline(CameraConfig.from_backend({"camera_id": "c", "use_anpr": True, "clips": True}), NoDetector(), NoReader(), sink)
    for i in range(80):
        p.process(frame(i), datetime.fromtimestamp(T0 + i * 0.2, timezone.utc))
    p.clips.request("c", iso(T0 + 10), "al-1")
    await p.drain_clips(30)
    assert [u[0] for u in sink.uploads] == ["al-1"]


def test_the_shops_clock_is_written_on_each_picture_of_a_video():
    import numpy as np
    from datetime import datetime, timezone
    from worker.clips import ClipRecorder, stamp
    frame = np.zeros((360, 640, 3), np.uint8)
    out = stamp(frame, datetime(2026, 11, 13, 22, 11, 50))
    assert out.shape == frame.shape and out[300:, :250].sum() > 0 and frame.sum() == 0, "the corner is drawn on a copy"
    rec = ClipRecorder(object(), tz="Asia/Kolkata")
    rec.add(frame, datetime(2026, 11, 13, 16, 41, 50, tzinfo=timezone.utc))
    assert rec.ring.newest is not None and rec.tz is not None


def test_a_camera_with_only_marked_areas_keeps_pictures_for_videos():
    from worker.areas import AreaConfig
    from worker.pipeline import CameraConfig, CameraPipeline
    cfg = CameraConfig.from_backend({"camera_id": "c", "clips": True, "timezone": "Asia/Kolkata",
                                     "areas": [{"id": "z", "kind": "area_count", "name": "Hall", "points": [[0, 0], [1, 0], [1, 1]], "params": {}}]})
    assert cfg.tz == "Asia/Kolkata" and CameraPipeline(cfg, None, None, type("S", (), {})()).clips is not None
