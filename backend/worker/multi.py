"""Runs many cameras in one process.

A supervisor asks the backend which cameras this worker should run (`discover`), starts a live runner for each new one, stops the runner of a
camera that is no longer assigned (switched off, removed, account suspended), and starts a camera again at the next poll if its runner ended
on its own. Cameras share the analysis models and one sender to the backend, but each has its own settings, frame rate, health reports and
reconnect handling, so one bad camera never affects the others."""
import asyncio
import logging

from .backend import BackendUnavailable

log = logging.getLogger("worker.multi")


class Fleet:
    def __init__(self, discover, start_camera, *, poll: float = 60.0, stop_timeout: float = 20.0):
        """discover() -> list of camera ids.   start_camera(camera_id, stop_event) -> coroutine that runs one camera until stop_event is set."""
        self.discover, self.start_camera, self.poll, self.stop_timeout = discover, start_camera, poll, stop_timeout
        self.running: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}
        self.history: list = []                  # (camera_id, how it ended), for the log and tests

    def _reap(self) -> None:
        for cid, (task, _) in list(self.running.items()):
            if task.done():
                del self.running[cid]
                try:
                    self.history.append((cid, task.result().get("exit") if isinstance(task.result(), dict) else "done"))
                except asyncio.CancelledError:
                    self.history.append((cid, "cancelled"))
                except Exception as e:
                    log.error("camera %s stopped with an error: %s", cid, type(e).__name__)
                    self.history.append((cid, "error"))

    async def step(self) -> None:
        """One round: reap finished cameras, ask what should run, start and stop to match."""
        self._reap()
        try:
            wanted = set(await self.discover())
        except BackendUnavailable as e:
            log.warning("could not ask the backend which cameras to run (%s); carrying on with the current ones", e)
            return
        for cid in sorted(wanted - set(self.running)):
            ev = asyncio.Event()
            self.running[cid] = (asyncio.create_task(self.start_camera(cid, ev)), ev)
            log.info("starting camera %s (%d running)", cid, len(self.running))
        for cid in set(self.running) - wanted:
            if not self.running[cid][1].is_set():
                log.info("camera %s is no longer assigned to this worker: stopping it", cid)
                self.running[cid][1].set()

    async def run(self, stop: asyncio.Event) -> dict:
        try:
            while not stop.is_set():
                await self.step()
                try:
                    await asyncio.wait_for(stop.wait(), self.poll)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.shutdown()
        return {"ended": self.history}

    async def shutdown(self) -> None:
        for _, ev in self.running.values():
            ev.set()
        tasks = [t for t, _ in self.running.values()]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.stop_timeout)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._reap()
