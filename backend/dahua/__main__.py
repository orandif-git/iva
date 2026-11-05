import asyncio
import logging
import signal

import uvicorn

from registrar.backend_client import BackendClient
from registrar.relay import QuietServer, build_app

from .config import DahuaSettings
from .driver import NetSdkDriver
from .gateway import Gateway
from .streams import StreamManager


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = DahuaSettings()
    driver = NetSdkDriver()
    gw = Gateway(cfg, BackendClient(cfg.backend_url, cfg.service_token), driver)
    gw.streams = mgr = StreamManager(gw, driver, cfg)
    await gw.start()
    http = None
    if cfg.relay_http_port >= 0:
        base = cfg.relay_public_base.rstrip("/") or f"http://{cfg.relay_http_bind}:{cfg.relay_http_port}"
        http = QuietServer(uvicorn.Config(build_app(mgr, cfg.service_token, lambda: base), host=cfg.relay_http_bind, port=cfg.relay_http_port,
                                          log_level="warning", access_log=False, lifespan="off"))
        http_task = asyncio.create_task(http.serve())
        logging.getLogger("dahua").info("video relay for workers on %s", base)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    if http is not None:
        http.should_exit = True
        await http_task
    await gw.stop()


if __name__ == "__main__":
    asyncio.run(main())
