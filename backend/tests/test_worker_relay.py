import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from tests.fake_device import Media
from tests.test_registrar_flow import env  # noqa: F401
from tests.test_relay import ready
from worker.live import StreamReader, build_command
from worker.relay import RelayClient, RelayError, RelaySource, make_relay_reader

FAKE = str(Path(__file__).with_name("fake_ffmpeg.py"))
W, H = 64, 36


def client_for(handler):
    return RelayClient("http://relay", "t" * 40, http=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://relay"))


# ------------------------------------------------------------------ the client
def test_it_returns_the_play_link_and_sends_the_token_only_to_the_relay():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(201, json={"url": "http://relay/relay/v1/play/abc", "expires_in": 60})
    assert client_for(handler).play_url("34020000001180000001", 3) == "http://relay/relay/v1/play/abc"
    assert seen == [("/relay/v1/streams", "Bearer " + "t" * 40, {"device_id": "34020000001180000001", "channel_no": 3})]


@pytest.mark.parametrize("status,body,code", [
    (404, {"error": {"code": "device_offline", "message": "That recorder is not connected."}}, "device_offline"),
    (429, {"error": {"code": "too_many_streams", "message": "At the limit."}}, "too_many_streams"),
    (401, {"error": {"code": "invalid_token", "message": "Not allowed."}}, "invalid_token"),
])
def test_the_relays_reason_is_passed_on(status, body, code):
    with pytest.raises(RelayError) as e:
        client_for(lambda r: httpx.Response(status, json=body)).play_url("d", 1)
    assert e.value.code == code and "t" * 40 not in str(e.value)


def test_an_unreachable_or_confused_relay_gives_a_clear_error_without_leaking_the_token():
    def down(request):
        raise httpx.ConnectError("refused: Bearer " + "t" * 40)
    with pytest.raises(RelayError) as e:
        client_for(down).play_url("d", 1)
    assert e.value.code == "relay_unreachable" and "t" * 40 not in str(e.value)
    with pytest.raises(RelayError) as e2:
        client_for(lambda r: httpx.Response(502, text="<html>bad gateway</html>")).play_url("d", 1)
    assert e2.value.code == "relay_error"


# ------------------------------------------------------------------ the command
def test_a_relay_link_is_read_as_a_program_stream_with_a_network_timeout_and_no_realtime_pacing():
    c = build_command("http://relay:8081/relay/v1/play/abc", 960, 540, 5, input_format="mpeg")
    i = c.index("-i")
    assert c[c.index("-f"):c.index("-f") + 2] == ["-f", "mpeg"] and c.index("-f") < i                  # the format is declared before the input
    assert "-rw_timeout" in c and "-re" not in c and "-rtsp_transport" not in c
    assert c[i + 1] == "http://relay:8081/relay/v1/play/abc" and "Bearer" not in " ".join(c)            # the long-lived token is not on the command line
    assert "fps=5,scale=960:540" in c


# ----------------------------------------------------- a fresh link every connection
def test_every_reconnect_asks_the_relay_for_a_new_single_use_link(tmp_path):
    sc = tmp_path / "s.json"
    sc.write_text(json.dumps({"state": str(tmp_path / "n"), "w": W, "h": H, "attempts": [{"frames": 3, "then": "exit"}, {"frames": 3, "then": "exit"}, {"frames": 3, "then": "continue"}]}))
    links = []

    def handler(request):
        links.append(1)
        return httpx.Response(201, json={"url": f"http://relay/relay/v1/play/token{len(links)}"})
    relay = client_for(handler)
    reader = StreamReader(W, H, 5, lambda: [sys.executable, FAKE, str(sc)] and (relay.play_url("d", 1), [sys.executable, FAKE, str(sc)])[1],
                          backoff=(0.05,), jitter=0)
    reader.start()
    try:
        got, end = [], time.time() + 8
        while len(got) < 9 and time.time() < end:
            f = reader.get(0.2)
            if f:
                got.append(f)
        assert len(got) == 9 and len(links) == 3 and {f.epoch for f in got} == {1, 2, 3}             # one link per connection, never reused
    finally:
        reader.stop()


def test_a_relay_refusal_shows_as_the_reason_and_the_reader_recovers_when_the_relay_does(tmp_path):
    sc = tmp_path / "s.json"
    sc.write_text(json.dumps({"state": str(tmp_path / "n"), "w": W, "h": H, "attempts": [{"frames": 3, "then": "continue"}]}))
    answers = [(404, {"error": {"code": "device_offline", "message": "That recorder is not connected."}}), (201, {"url": "http://relay/relay/v1/play/ok"})]

    def handler(request):
        status, body = answers.pop(0) if len(answers) > 1 else answers[0]
        return httpx.Response(status, json=body)
    relay = client_for(handler)
    reader = StreamReader(W, H, 5, lambda: (relay.play_url("d", 1), [sys.executable, FAKE, str(sc)])[1], backoff=(0.05,), jitter=0)
    reader.start()
    try:
        end = time.time() + 6
        while reader.reconnects < 1 and time.time() < end:
            time.sleep(0.02)
        assert "device_offline" in (reader.last_error or "") and reader.state == "reconnecting"          # the camera's health report will say why
        frames = []
        while len(frames) < 3 and time.time() < end:
            f = reader.get(0.2)
            if f:
                frames.append(f)
        assert len(frames) == 3 and reader.state == "streaming"
    finally:
        reader.stop()


# ------------------------------------------- the whole chain with real video and real FFmpeg
@pytest.fixture(scope="module")
def program_stream(tmp_path_factory):
    """10 s of real H.264 video in an MPEG program stream: what a GB28181 recorder sends."""
    p = tmp_path_factory.mktemp("ps") / "test.ps"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "10", "-c:v", "libx264",
                    "-g", "25", "-bf", "0", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-f", "mpeg", str(p)], check=True)
    return p.read_bytes()


def collect(reader, n, timeout=20):
    out, end = [], time.time() + timeout
    while len(out) < n and time.time() < end:
        f = reader.get(0.2)
        if f:
            out.append(f)
    return out


async def test_real_video_comes_through_the_relay_and_is_decoded_by_ffmpeg(env, program_stream):
    env.cfg.relay_stall_seconds = 3.0
    t, u, cred, dev = await env.onboard(media=Media(data=program_stream, payload_size=1200, gap=0.01))
    await ready(env, cred, dev)
    relay = RelayClient(env.reg.relay_base, env.cfg.service_token)
    reader = make_relay_reader(RelaySource(cred["device_id"], 1), relay, 320, 180, 5, backoff=(0.2,), jitter=0)
    reader.start()
    try:
        import asyncio
        frames = await asyncio.get_running_loop().run_in_executor(None, collect, reader, 12)
        assert len(frames) >= 12
        img = frames[5].image
        assert img.shape == (180, 320, 3)
        assert img.std() > 25                                                   # a real picture (colour bars and text), not a blank or garbled one
        stamps = [f.ts for f in frames]
        assert stamps == sorted(stamps) and {f.epoch for f in frames} == {1}
        assert len(dev.invites) == 1                                           # one recorder stream served the reader
    finally:
        reader.stop()


async def test_when_the_recorders_stream_dies_the_worker_reconnects_and_the_video_resumes(env, program_stream):
    env.cfg.relay_stall_seconds = 1.0
    media = Media(data=program_stream, payload_size=1200, gap=0.01, stall_after=60)   # the recorder goes silent part-way through the first stream
    t, u, cred, dev = await env.onboard(media=media)
    await ready(env, cred, dev)
    relay = RelayClient(env.reg.relay_base, env.cfg.service_token)
    reader = make_relay_reader(RelaySource(cred["device_id"], 1), relay, 320, 180, 5, backoff=(0.2,), jitter=0)
    reader.start()
    try:
        import asyncio
        frames = await asyncio.get_running_loop().run_in_executor(None, collect, reader, 40, 40)      # the first stream holds roughly 25 pictures
        epochs = [f.epoch for f in frames]
        assert len(frames) >= 40 and epochs[0] == 1 and epochs[-1] >= 2               # the relay ended the dead stream; the reader came back with a new one
        assert epochs == sorted(epochs)                                               # and the pictures stayed in order across the gap
        assert reader.reconnects >= 1 and len(dev.invites) >= 2 and dev.bye_count >= 1
    finally:
        reader.stop()


async def test_the_live_runner_works_end_to_end_through_the_relay(env, client, su, program_stream):
    """Recorder registers -> camera set up -> backend says "use the relay" -> worker asks the relay -> real video -> FFmpeg -> pictures -> health to the backend."""
    import asyncio
    import uuid
    from tests.video_gen import ColorDetector
    from worker.backend import WorkerBackend
    from worker.pipeline import CameraPipeline
    from worker.runner import LiveSettings, run_live
    from worker.sink import MemorySink

    env.cfg.relay_stall_seconds = 3.0
    t, u, cred, dev = await env.onboard(media=Media(data=program_stream, payload_size=1200, gap=0.07))     # about real time: 10 s of video over ~9 s
    await ready(env, cred, dev)
    cam = (await client.post("/v1/cameras", json={"recorder_id": cred["recorder_id"], "channel_no": 1, "name": "Gate", "use_footfall": True}, headers=u["headers"])).json()["id"]
    await client.put(f"/v1/cameras/{cam}/zones", json={"zones": [{"kind": "count_line", "points": [[0.1, 0.5], [0.9, 0.5]], "in_direction": [0, 1]}]}, headers=u["headers"])
    reports = []

    class Link(WorkerBackend):
        async def status(self, state, fps, error, dropped=None):
            reports.append((state, fps, error))
            return await super().status(state, fps, error, dropped)

    relay = RelayClient(env.reg.relay_base, env.cfg.service_token)
    sink, stop = MemorySink(), asyncio.Event()
    asked = []

    def reader_factory(source, w, h, fps):
        asked.append((source, fps))
        return make_relay_reader(source, relay, w, h, fps, backoff=(0.2,), jitter=0)

    asyncio.get_running_loop().call_later(9, stop.set)
    result = await run_live(backend=Link(client, env.cfg.service_token, cam), sink=sink, reader_factory=reader_factory,
                            pipeline_factory=lambda c: CameraPipeline(c, ColorDetector(), None, sink),
                            settings=LiveSettings(width=320, height=180, config_refresh=99, health_interval=1.5, flush_interval=1), stop=stop)
    assert asked[0][0] == RelaySource(cred["device_id"], 1)                           # the backend said "use the relay", with no address and no password
    assert result["exit"] == "stopped" and result["frames_seen"] >= 15 and result["processed"] >= 5
    states = [r[0] for r in reports]
    assert "streaming" in states and states[-1] == "offline"                           # health reported while running, and a clean goodbye at the end
    assert any(r[1] for r in reports if r[0] == "streaming")                           # with the real analysis rate
    row = await su.fetchrow("SELECT stream_state, stream_error FROM app.cameras WHERE id=$1", uuid.UUID(cam))
    assert row["stream_state"] == "offline" and row["stream_error"] == "the analysis worker stopped"
    await env.until(lambda: _no_streams(env))                                         # the worker left: the relay let go of the recorder after the grace period
    assert dev.bye_count >= 1 and len(dev.invites) == 1


async def _no_streams(env):
    return not env.reg.relay.streams
