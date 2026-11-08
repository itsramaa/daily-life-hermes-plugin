from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger("hooks.daily-life-boot")


def _run_boot_cycle(context: dict[str, Any]) -> None:
    """Run non-blocking catch-up evaluation on gateway startup."""
    try:
        from core.lifecycle import run_startup_catchup
        res = run_startup_catchup()
        logger.info("daily-life-boot startup catch-up result: %s", res.get("status"))
    except Exception as exc:
        logger.error("daily-life-boot hook encountered error: %s", exc)


async def handle(event_type: str, context: dict[str, Any]) -> None:
    """Gateway startup handler (trusted-by-placement)."""
    logger.info("daily-life-boot: gateway startup detected (%s)", event_type)
    thread = threading.Thread(
        target=_run_boot_cycle,
        args=(context,),
        name="daily-life-boot",
        daemon=True,
    )
    thread.start()
