from __future__ import annotations

import logging
from typing import Any

from .handlers import (
    handle_get_learned_insights,
    handle_get_plan,
    handle_get_status,
    handle_record_outcome,
    handle_replan,
    handle_trigger_cycle,
)
from .schemas import (
    GET_LEARNED_INSIGHTS_SCHEMA,
    GET_PLAN_SCHEMA,
    GET_STATUS_SCHEMA,
    RECORD_OUTCOME_SCHEMA,
    REPLAN_SCHEMA,
    TRIGGER_CYCLE_SCHEMA,
)

logger = logging.getLogger(__name__)

__all__ = ["register_tools"]

_TOOLS = [
    (
        "daily_life_get_status",
        GET_STATUS_SCHEMA,
        handle_get_status,
        "Get current daily life status (brain, plan, active tasks).",
    ),
    (
        "daily_life_trigger_cycle",
        TRIGGER_CYCLE_SCHEMA,
        handle_trigger_cycle,
        "Trigger daily cycle execution (idempotent).",
    ),
    (
        "daily_life_get_plan",
        GET_PLAN_SCHEMA,
        handle_get_plan,
        "Get active daily plan and scheduled time blocks.",
    ),
    (
        "daily_life_replan",
        REPLAN_SCHEMA,
        handle_replan,
        "Request replanning due to unexpected changes or fatigue.",
    ),
    (
        "daily_life_record_outcome",
        RECORD_OUTCOME_SCHEMA,
        handle_record_outcome,
        "Record activity telemetry outcome for learning engine.",
    ),
    (
        "daily_life_get_learned_insights",
        GET_LEARNED_INSIGHTS_SCHEMA,
        handle_get_learned_insights,
        "Get learned duration calibration and time affinities.",
    ),
]


def register_tools(ctx: Any) -> None:
    """Register all daily-life tools with the Hermes plugin context."""
    for name, schema, handler, description in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="daily-life",
            schema=schema,
            handler=handler,
            description=description,
            is_async=True,
        )
    logger.debug("daily-life: registered %d tools", len(_TOOLS))
