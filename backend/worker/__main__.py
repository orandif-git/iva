"""Run the worker on a video file:

    python -m worker run-file --video gate.mp4 --camera-id <uuid>              (zones and settings come from the backend)
    python -m worker run-file --video gate.mp4 --camera-id <uuid> --zones-json zones.json --dry-run   (no backend needed)

or live on a camera (settings, frame-rate policy and the stream address come from the backend and are re-read every 30 s):

    python -m worker run-live --camera-id <uuid>
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime

import httpx

from .config import WorkerSettings
from .pipeline import CameraConfig, CameraPipeline
from .sink import BackendSink, MemorySink
from .source import SourceError, VideoFileSource


async def run_file(a, settings: WorkerSettings) -> int:
    from .detector import YoloxDetector
    model = settings.model_dir / f"{settings.detector_model}.onnx"
    if not model.is_file():
        print(f"Model not found: {model}\nRun:  python -m scripts.fetch_models", file=sys.stderr)
        return 2
    try:
        source = VideoFileSource(a.video, a.fps, datetime.fromisoformat(a.start_time) if a.start_time else None, a.realtime)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    client = None
    if a.zones_json:
        j = json.load(open(a.zones_json))
        cfg = CameraConfig.from_backend({"camera_id": a.camera_id, **j})
    else:
        client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10)
        r = await client.get(f"/internal/v1/cameras/{a.camera_id}/config", headers={"Authorization": f"Bearer {settings.service_token}"})
        if r.status_code != 200 or not r.json().get("active"):
            print(f"camera config unavailable or camera inactive ({r.status_code})", file=sys.stderr)
            return 2
        cfg = CameraConfig.from_backend(r.json())
    if cfg.use_footfall and not cfg.line:
        print("warning: footfall is on but no counting line is drawn for this camera; people will not be counted", file=sys.stderr)

    sink = MemorySink() if a.dry_run else BackendSink(client or httpx.AsyncClient(base_url=settings.backend_url, timeout=10), settings.service_token)
    detector = YoloxDetector(model, threads=settings.threads)
    reader = None
    if cfg.use_anpr and not a.no_plates:
        from .plates import OnnxPlateReader
        reader = OnnxPlateReader()
    pipe = CameraPipeline(cfg, detector, reader, sink, snapshot_dir=a.snapshot_dir, run_id=uuid.uuid4().hex[:6], anchor=a.anchor)

    print(f"{a.video}: {source.duration:.0f} s, sampled at {source.effective_fps:.1f} fps, camera '{cfg.name}'")
    t0, n = time.perf_counter(), 0
    for frame, ts in source.frames():
        pipe.process(frame, ts)
        n += 1
        if n % 25 == 0:
            await sink.flush()
            if sink.fatal:
                print(f"stopping: {sink.fatal}", file=sys.stderr)
                break
    pipe.finish()
    left = await sink.close()
    await pipe.drain_clips(30.0)                 # the last plate batch may have asked for clips
    dt = time.perf_counter() - t0
    s = pipe.stats
    print(f"\n{n} frames in {dt:.1f} s ({n / dt:.1f} frames/s; detection {s['detect_ms'] / max(n, 1):.0f} ms/frame)")
    print(f"people in {pipe.totals['in']}, out {pipe.totals['out']}")
    print(f"plates emitted {s['plates_emitted']} from {s['plate_reads']} reads ({s['plate_ms'] / max(s['plate_reads'], 1):.0f} ms/read)")
    if a.dry_run:
        for e in sink.plates:
            print("  plate:", {k: e[k] for k in ("seen_at", "plate_raw", "confidence", "direction", "vehicle_type")})
    else:
        print("backend:", dict(sink.stats), "rejected:", dict(sink.rejected), f"unsent: {left}")
    return 1 if (left or getattr(sink, "fatal", None)) else 0


async def run_live_cmd(a, settings: WorkerSettings) -> int:
    from .backend import WorkerBackend
    from .detector import YoloxDetector
    from .live import make_stream_reader, mask_url
    from .relay import RelayClient, Relays, RelaySource, make_relay_reader
    from .runner import LiveSettings, run_live

    model = settings.model_dir / f"{settings.detector_model}.onnx"
    if not model.is_file():
        print(f"Model not found: {model}\nRun:  python -m scripts.fetch_models", file=sys.stderr)
        return 2
    client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10)
    backend = WorkerBackend(client, settings.service_token, a.camera_id)
    relay = Relays(RelayClient(settings.relay_url, settings.service_token), RelayClient(settings.dahua_url, settings.service_token))
    url = a.rtsp_url or os.environ.get("GI_WORKER_RTSP_URL")

    def reader_factory(source, w, h, fps):
        if isinstance(source, RelaySource):
            return make_relay_reader(source, relay, w, h, fps)
        return make_stream_reader(source, w, h, fps)

    if a.rtsp_url:
        print("note: an address given on the command line is visible to other users in the process list. "
              "Prefer storing it in the admin console, or set GI_WORKER_RTSP_URL.", file=sys.stderr)
    sink = BackendSink(client, settings.service_token)
    detector = YoloxDetector(model, threads=settings.threads)
    plates = {}

    def pipeline_factory(cfg):
        reader = None
        if cfg.use_anpr:
            if "reader" not in plates:                       # load the plate models once, only if a camera needs them
                from .plates import OnnxPlateReader
                plates["reader"] = OnnxPlateReader()
            reader = plates["reader"]
        return CameraPipeline(cfg, detector, reader, sink, snapshot_dir=a.snapshot_dir, run_id=uuid.uuid4().hex[:6], anchor=a.anchor)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    live = LiveSettings(width=a.width, height=a.height, config_refresh=a.config_refresh, use_motion=not a.no_motion)
    result = await run_live(backend=backend, sink=sink, reader_factory=reader_factory, pipeline_factory=pipeline_factory,
                            settings=live, stop=stop, url=url)
    print(mask_url(json.dumps(result)))
    return 0 if result["exit"] in ("stopped",) else 1


async def run_many_cmd(a, settings: WorkerSettings) -> int:
    """Many cameras in one process: shared models and sender, one live runner per camera, cameras added and removed as the backend says."""
    from .backend import FleetBackend, WorkerBackend
    from .detector import YoloxDetector
    from .live import make_stream_reader
    from .multi import Fleet
    from .relay import RelayClient, Relays, RelaySource, make_relay_reader
    from .runner import LiveSettings, run_live

    model = settings.model_dir / f"{settings.detector_model}.onnx"
    if not model.is_file():
        print(f"Model not found: {model}\nRun:  python -m scripts.fetch_models", file=sys.stderr)
        return 2
    client = httpx.AsyncClient(base_url=settings.backend_url, timeout=10)
    relay = Relays(RelayClient(settings.relay_url, settings.service_token), RelayClient(settings.dahua_url, settings.service_token))
    sink = BackendSink(client, settings.service_token)
    detector = YoloxDetector(model, threads=settings.threads)
    plates: dict = {}
    cpu = asyncio.Semaphore(max(1, a.parallel or settings.threads))      # pictures analysed at the same moment, across all cameras
    live = LiveSettings(width=a.width, height=a.height, config_refresh=a.config_refresh, use_motion=not a.no_motion)

    def reader_factory(source, w, h, fps):
        if isinstance(source, RelaySource):
            return make_relay_reader(source, relay, w, h, fps)
        return make_stream_reader(source, w, h, fps)

    async def start_camera(camera_id: str, stop_ev: asyncio.Event) -> dict:
        view = sink.view(camera_id)

        def pipeline_factory(cfg):
            reader = None
            if cfg.use_anpr:
                if "reader" not in plates:                   # load the plate models once, only if some camera needs them
                    from .plates import OnnxPlateReader
                    plates["reader"] = OnnxPlateReader()
                reader = plates["reader"]
            return CameraPipeline(cfg, detector, reader, view, snapshot_dir=a.snapshot_dir, run_id=uuid.uuid4().hex[:6], anchor=a.anchor)
        return await run_live(backend=WorkerBackend(client, settings.service_token, camera_id), sink=view, reader_factory=reader_factory,
                              pipeline_factory=pipeline_factory, settings=live, stop=stop_ev, cpu=cpu)

    fleet_backend = FleetBackend(client, settings.service_token)

    async def discover():
        if a.camera_ids:
            return a.camera_ids
        return await fleet_backend.cameras(a.shard, a.of)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    result = await Fleet(discover, start_camera, poll=a.poll).run(stop)
    left = await sink.close()
    print(json.dumps({**result, "unsent": left}, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m worker")
    sub = p.add_subparsers(dest="cmd", required=True)
    lv = sub.add_parser("run-live", help="analyse a live camera until stopped")
    lv.add_argument("--camera-id", required=True)
    lv.add_argument("--rtsp-url", help="override the stream address (otherwise it comes from the backend)")
    lv.add_argument("--width", type=int, default=960, help="width of the picture analysed (default 960)")
    lv.add_argument("--height", type=int, default=540)
    lv.add_argument("--config-refresh", type=float, default=30.0, help="seconds between checks for new settings")
    lv.add_argument("--no-motion", action="store_true", help="do not use motion to wake from the idle rate early")
    lv.add_argument("--snapshot-dir")
    lv.add_argument("--anchor", choices=["center", "foot"], default="center")
    mn = sub.add_parser("run-many", help="analyse many live cameras in one process")
    mn.add_argument("--shard", type=int, default=0, help="which share of the cameras this process runs (0 to of-1)")
    mn.add_argument("--of", type=int, default=1, help="how many worker processes share the cameras")
    mn.add_argument("--camera-ids", nargs="*", help="run exactly these cameras instead of asking the backend")
    mn.add_argument("--poll", type=float, default=60.0, help="seconds between checks for cameras added or removed")
    mn.add_argument("--parallel", type=int, default=0, help="pictures analysed at once across all cameras (default: the thread setting)")
    mn.add_argument("--width", type=int, default=960)
    mn.add_argument("--height", type=int, default=540)
    mn.add_argument("--config-refresh", type=float, default=30.0)
    mn.add_argument("--no-motion", action="store_true")
    mn.add_argument("--snapshot-dir")
    mn.add_argument("--anchor", choices=["center", "foot"], default="center")
    r = sub.add_parser("run-file", help="analyse a video file as if it were a camera")
    r.add_argument("--video", required=True)
    r.add_argument("--camera-id", required=True)
    r.add_argument("--fps", type=float, default=5.0, help="frames analysed per second of video (default 5)")
    r.add_argument("--start-time", help="ISO time of the first frame; default places the clip so it ends now")
    r.add_argument("--realtime", action="store_true", help="play at real speed instead of as fast as possible")
    r.add_argument("--zones-json", help="local settings instead of the backend: {name, use_footfall, use_anpr, zones:[...]}")
    r.add_argument("--dry-run", action="store_true", help="do not send anything; print the plate events at the end")
    r.add_argument("--no-plates", action="store_true", help="skip plate reading")
    r.add_argument("--snapshot-dir", help="save an evidence picture for each plate read here")
    r.add_argument("--anchor", choices=["center", "foot"], default="center", help="which point of a person crosses the line (foot for angled cameras)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.cmd == "run-live":
        return asyncio.run(run_live_cmd(a, WorkerSettings()))
    if a.cmd == "run-many":
        return asyncio.run(run_many_cmd(a, WorkerSettings()))
    return asyncio.run(run_file(a, WorkerSettings()))


if __name__ == "__main__":
    sys.exit(main())
