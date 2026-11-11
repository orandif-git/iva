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


# The alerts that are found by looking at the data on a clock (every other alert is raised the moment its event arrives), and the check that finds each.
# Each is checked every DEFAULT_CHECK_SECONDS unless Platform Admin sets another time for it (platform.alert_checks).
TIMED = {"vehicle_overstay": "vehicle_overstay", "crowding": "crowding", "connection_unstable": "connection_unstable", "daily_summary": "daily_summary"}
DEFAULT_CHECK_SECONDS, TICK_SECONDS = 60, 15
_last_run: dict[str, float] = {}


async def check_intervals() -> dict[str, int]:
    async with db.service_tx() as conn:
        return {r["alert_type"]: r["every_seconds"] for r in await conn.fetch("SELECT alert_type, every_seconds FROM platform.alert_checks")}


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
        try:
            every = await check_intervals()
        except Exception:
            log.exception("could not read the check intervals")
            every = {}
        for alert_type, job in TIMED.items():
            if loop_time - _last_run.get(alert_type, -1e9) < every.get(alert_type, DEFAULT_CHECK_SECONDS):
                continue
            _last_run[alert_type] = loop_time
            try:
                await getattr(detectors, job)()
            except Exception:
                log.exception("detector %s failed", job)
        await asyncio.sleep(TICK_SECONDS)
