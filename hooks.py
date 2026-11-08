from __future__ import annotations

import logging
from typing import Any

from db import record_task_telemetry, record_tool_telemetry

logger = logging.getLogger(__name__)

__all__ = [
    "on_post_tool_call",
    "on_kanban_task_completed",
    "on_kanban_task_blocked",
    "register_hooks",
]


def on_post_tool_call(
    tool_name: str = "",
    duration_ms: float = 0.0,
    status: str = "",
    task_id: str | None = None,
    session_id: str | None = None,
    error_type: str | None = None,
    **kwargs: Any,
) -> None:
    """Observer hook: automatically populate activity_telemetry from tool invocations."""
    try:
        record_tool_telemetry(
            tool_name=tool_name,
            duration_ms=duration_ms,
            status=status,
            task_id=task_id,
            error_type=error_type,
        )
    except Exception as exc:
        logger.debug("daily-life: failed recording tool telemetry: %s", exc)


def on_kanban_task_completed(
    task_id: str = "",
    duration_sec: float = 0.0,
    task_title: str = "",
    **kwargs: Any,
) -> None:
    """Observer hook: record completion duration when Kanban task finishes."""
    try:
        from config import get_settings
        from datetime import datetime

        now = datetime.utcnow()
        duration_min = max(1, int(duration_sec / 60.0))
        record_task_telemetry(
            profile_id=get_settings().profile_id,
            activity_type=f"kanban:{task_title or task_id}",
            day_of_week=now.weekday(),
            hour_of_day=now.hour,
            estimated_duration_min=duration_min,
            actual_duration_min=duration_min,
            status="completed",
        )
    except Exception as exc:
        logger.debug("daily-life: failed recording kanban completion telemetry: %s", exc)


def on_kanban_task_blocked(task_id: str = "", reason: str = "", **kwargs: Any) -> None:
    """Observer hook: note blocked task to assist dynamic replanning."""
    logger.warning("daily-life: kanban task %s blocked: %s", task_id, reason)


def register_hooks(ctx: Any) -> None:
    """Register lifecycle and telemetry hooks with the Hermes plugin context."""
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("kanban_task_completed", on_kanban_task_completed)
    ctx.register_hook("kanban_task_blocked", on_kanban_task_blocked)
    logger.debug("daily-life: registered 3 lifecycle hooks")
