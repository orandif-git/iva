import asyncio
import logging
import signal

from .backend_client import BackendClient
from .config import RegistrarSettings
from .server import Registrar


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = RegistrarSettings()
    reg = Registrar(cfg, BackendClient(cfg.backend_url, cfg.service_token))
    await reg.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await reg.stop()


if __name__ == "__main__":
    asyncio.run(main())
