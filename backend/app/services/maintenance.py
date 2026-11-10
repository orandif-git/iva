import asyncio
import logging
from datetime import date

from ..db import db

log = logging.getLogger("gateinsight.maintenance")


async def ensure_partitions(months_ahead: int = 2) -> int:
    """Make sure the monthly plate partitions for the previous, current and next months exist."""
    today = date.today()
    n = 0
    async with db.service_tx() as conn:
        for offset in range(-1, months_ahead + 1):
            m = today.month - 1 + offset
            d = date(today.year + m // 12, m % 12 + 1, 1)
            await conn.execute("SELECT app.ensure_plate_partition($1::date)", d)
            n += 1
    return n


async def run_forever() -> None:
    from . import detectors
    last_slow = 0.0
    while True:
        loop_time = asyncio.get_running_loop().time()
        if loop_time - last_slow >= 6 * 3600 or last_slow == 0.0:
            last_slow = loop_time
            from . import retention
            for job in (ensure_partitions, retention.run_all):
                try:
                    await job()
                except Exception:
                    log.exception("maintenance job %s failed", job.__name__)
        for job in (detectors.vehicle_overstay, detectors.crowding, detectors.connection_unstable, detectors.plan_limit, detectors.message_allowance, detectors.payment_due, detectors.daily_summary):
            try:
                await job()
            except Exception:
                log.exception("detector %s failed", job.__name__)
        await asyncio.sleep(300)
