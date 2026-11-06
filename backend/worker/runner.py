"""Runs one camera live: read pictures, decide which to analyse (adaptive frame rate), analyse them, deliver the results,
report health, and pick up changes made in the admin console without a restart."""
import asyncio
import logging
import time
from dataclasses import dataclass

from .adaptive import IDLE, MotionDetector, Policy, RateController
from .backend import BackendUnavailable
from .pipeline import CameraConfig

log = logging.getLogger("worker.runner")


@dataclass
class LiveSettings:
    width: int = 960
    height: int = 540
    config_refresh: float = 30.0        # how often to ask the backend for new settings (so frame-rate changes apply within this time)
    health_interval: float = 15.0
    flush_interval: float = 2.0
    ai_poll_interval: float = 5.0       # how often to ask whether anyone is looking at "what the AI sees" (a very small request)
    ai_frame_interval: float = 3.0      # while someone is, how often to draw and send the picture
    hold_seconds: float = 3.0           # stay fast this long after the last person or vehicle was seen
    use_motion: bool = True
    retry_delay: float = 2.0            # wait between attempts to reach the backend at start-up


def _report_state(reader) -> str:
    if reader.state == "streaming":
        return "streaming"
    return "offline" if reader.down_seconds() > 60 else "reconnecting"


async def run_live(*, backend, sink, reader_factory, pipeline_factory, settings: LiveSettings, stop: asyncio.Event,
                   url: str | None = None, clock=time.monotonic, cpu: asyncio.Semaphore | None = None) -> dict:
    """reader_factory(url, width, height, fps) -> a started-able reader.  pipeline_factory(CameraConfig) -> CameraPipeline.
    cpu: when several cameras run in one process, a semaphore limiting how many analyse at once (analysis then runs in threads)."""
    # ---- settings first. Wait for the backend if it is not there yet.
    cfg_json = None
    while cfg_json is None and not stop.is_set():
        try:
            cfg_json = await backend.config()
        except BackendUnavailable as e:
            log.warning("backend not reachable yet: %s", e)
            await _sleep(stop, settings.retry_delay)
        else:
            if cfg_json is None:
                return {"exit": "unknown_camera"}
    if stop.is_set():
        return {"exit": "stopped"}
    if not cfg_json["active"]:
        return {"exit": "inactive"}
    if url is None:
        url = await backend.source()
        if url is None:
            return {"exit": "no_source"}

    cfg = CameraConfig.from_backend(cfg_json)
    policy = Policy.from_backend(cfg_json["frame_rate"])
    source_stamp = cfg_json.get("source_set_at")
    controller = RateController(policy, hold_seconds=settings.hold_seconds)
    motion = MotionDetector()
    pipeline = pipeline_factory(cfg)
    reader = reader_factory(url, settings.width, settings.height, policy.capture_fps)
    reader.start()

    epoch = None
    seen = total_processed = 0
    last_state = None
    t0 = clock()
    next_flush = next_health = next_refresh = next_ai_poll = next_ai_frame = t0
    ai_on = False
    exit_reason = "stopped"
    log.info("live: %s policy=%s", cfg.name, policy)

    async def restart_reader(new_url: str, fps: float) -> None:
        nonlocal reader, epoch
        await asyncio.to_thread(reader.stop)
        pipeline.reset()                                     # the picture is interrupted: nobody can be followed across it
        controller.reset_after_gap()
        motion.reset()
        reader = reader_factory(new_url, settings.width, settings.height, fps)
        reader.start()
        epoch = None

    try:
        while not stop.is_set():
            fr = await asyncio.to_thread(reader.get, 0.5)
            now = clock()
            if fr is not None:
                seen += 1
                if fr.epoch != epoch:                        # first picture, or the first after a reconnect
                    if epoch is not None:
                        pipeline.reset()
                        controller.reset_after_gap()
                        motion.reset()
                    epoch = fr.epoch
                if getattr(reader, "effective_fps", None):
                    controller.capture_fps = min(controller.capture_fps, reader.effective_fps) if controller.capture_fps else reader.effective_fps
                moved = settings.use_motion and motion.update(fr.image)
                ts = fr.ts.timestamp()
                if controller.should_process(ts, bool(moved) and controller.state == IDLE):
                    if cpu is None:
                        obs = pipeline.process(fr.image, fr.ts)
                    else:                                    # several cameras share this process: analyse in a thread, a few at a time, so the others keep running
                        async with cpu:
                            obs = await asyncio.to_thread(pipeline.process, fr.image, fr.ts)
                    controller.observe(obs, ts)
                    if ai_on and now >= next_ai_frame and hasattr(backend, "ai_frame"):       # only while someone is looking: otherwise nothing is drawn
                        next_ai_frame = now + settings.ai_frame_interval
                        jpeg = await asyncio.to_thread(pipeline.annotate, fr.image, controller.average_fps())
                        if jpeg and await backend.ai_frame(jpeg) is False:
                            ai_on = False

            if now >= next_flush:                            # on a timer, so a quiet or silent camera still delivers what it has
                await sink.flush()
                next_flush = now + settings.flush_interval
                if sink.fatal:
                    exit_reason = f"sink:{sink.fatal}"
                    break
            if now >= next_ai_poll and hasattr(backend, "ai_view"):
                next_ai_poll = now + settings.ai_poll_interval
                ai_on = bool(await backend.ai_view())
            state = _report_state(reader)
            if now >= next_health or state != last_state:        # on a timer, and at once whenever the stream comes up or goes down
                last_state = state
                keep_going = await backend.status(state, controller.average_fps(), reader.last_error, reader.queue.dropped)
                total_processed += controller.processed          # the controller's own count restarts after every report
                controller.reset_stats()
                next_health = now + settings.health_interval
                if keep_going is False:
                    exit_reason = "inactive"
                    break
            if now >= next_refresh:
                next_refresh = now + settings.config_refresh
                try:
                    new = await backend.config()
                except BackendUnavailable:
                    new = "unreachable"                      # keep running on the last settings
                if new is None or (new != "unreachable" and not new["active"]):
                    exit_reason = "inactive"
                    break
                if new != "unreachable":
                    new_cfg, new_policy = CameraConfig.from_backend(new), Policy.from_backend(new["frame_rate"])
                    if new_cfg != cfg:
                        pipeline.reset()
                        pipeline = pipeline_factory(new_cfg)
                        cfg = new_cfg
                    if new_policy != policy:
                        log.info("frame rate changed: %s", new_policy)
                        speed_changed = new_policy.capture_fps != policy.capture_fps
                        policy = new_policy
                        controller.set_policy(policy)
                        if speed_changed:                    # FFmpeg must be told to deliver pictures at the new speed
                            await restart_reader(url, policy.capture_fps)
                    if new.get("source_set_at") != source_stamp:
                        source_stamp = new.get("source_set_at")
                        fresh = await backend.source()
                        if fresh and fresh != url:
                            url = fresh
                            await restart_reader(url, policy.capture_fps)
    finally:
        pipeline.finish()
        await asyncio.to_thread(reader.stop)
        await backend.status("offline", None, "the analysis worker stopped", reader.queue.dropped)
        left = await sink.close()
        drain = getattr(pipeline, "drain_clips", None)
        if drain:
            await drain(30.0)                                  # the last plate batch may have asked for clips; finish them before exiting
    return {"exit": exit_reason, "frames_seen": seen, "processed": total_processed + controller.processed, "reconnects": reader.reconnects,
            "dropped": reader.queue.dropped, "unsent": left}


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass
