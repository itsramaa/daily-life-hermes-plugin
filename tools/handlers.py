from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from config import get_settings
from core.lifecycle import format_daily_status_summary, run_daily_cycle
from db import (
    get_activity_telemetry,
    get_daily_plan,
    get_learned_preferences,
    get_shared_insights,
    record_task_telemetry,
)

logger = logging.getLogger(__name__)

try:
    from tools.registry import tool_error, tool_result
except ImportError:
    def tool_result(data: Any) -> str:
        return json.dumps({"status": "success", "result": data})

    def tool_error(msg: str, **kwargs: Any) -> str:
        return json.dumps({"status": "error", "error": msg, **kwargs})

__all__ = [
    "handle_get_status",
    "handle_trigger_cycle",
    "handle_get_plan",
    "handle_replan",
    "handle_record_outcome",
    "handle_get_learned_insights",
]


async def handle_get_status(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_get_status."""
    del kwargs
    date_str = args.get("date_str")
    summary = format_daily_status_summary(date_str=date_str)
    return tool_result({"summary": summary})


async def handle_trigger_cycle(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_trigger_cycle."""
    del kwargs
    force = bool(args.get("force", False))
    date_str = args.get("date_str")

    try:
        res = await run_daily_cycle(force=force, date_str=date_str, trigger_source="tool_call")
        return tool_result(res)
    except Exception as exc:
        logger.error("Error triggering daily cycle: %s", exc)
        return tool_error(f"Failed triggering daily cycle: {exc}")


async def handle_get_plan(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_get_plan."""
    del kwargs
    today = args.get("date_str") or datetime.utcnow().strftime("%Y-%m-%d")
    plan = get_daily_plan(today, get_settings().profile_id)
    if not plan:
        return tool_result({"found": False, "message": f"No plan found for {today}."})
    return tool_result({"found": True, "plan": plan})


async def handle_replan(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_replan."""
    del kwargs
    reason = args.get("reason", "Manual replan request")
    from core.planner import trigger_interactive_replan
    msg = trigger_interactive_replan()
    return tool_result({"status": "replanning_scheduled", "reason": reason, "detail": msg})


async def handle_record_outcome(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_record_outcome."""
    del kwargs
    act_type = args.get("activity_type")
    est_dur = args.get("estimated_duration_min")
    act_dur = args.get("actual_duration_min")
    status = args.get("status")

    if not all([act_type, est_dur is not None, act_dur is not None, status]):
        return tool_error("activity_type, estimated_duration_min, actual_duration_min, and status are required.")

    now = datetime.utcnow()
    record_task_telemetry(
        profile_id=get_settings().profile_id,
        activity_type=str(act_type),
        day_of_week=now.weekday(),
        hour_of_day=now.hour,
        estimated_duration_min=int(est_dur),
        actual_duration_min=int(act_dur),
        status=str(status),
        outcome_metric_name=args.get("outcome_metric_name"),
        outcome_metric_value=float(args["outcome_metric_value"]) if "outcome_metric_value" in args and args["outcome_metric_value"] is not None else None,
        error_reason=args.get("error_reason"),
    )
    return tool_result({"status": "recorded", "activity_type": act_type})


async def handle_get_learned_insights(args: dict[str, Any], **kwargs: Any) -> str:
    """Handle daily_life_get_learned_insights."""
    del kwargs
    act_type = args.get("activity_type")
    prefs = get_learned_preferences()
    insights = get_shared_insights()

    if act_type and act_type in prefs:
        prefs = {act_type: prefs[act_type]}

    return tool_result({
        "profile_calibrations": prefs,
        "shared_insights": insights,
    })
