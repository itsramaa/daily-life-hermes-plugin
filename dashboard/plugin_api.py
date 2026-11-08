"""Daily Life dashboard — backend FastAPI routes.

Mounted at /api/plugins/daily-life/ by the Hermes Dashboard system.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# Ensure plugin root is in sys.path
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from config import get_settings
from core.adapters.brain import BrainAdapter
from core.lifecycle import is_cycle_completed, run_daily_cycle
from core.opportunity import scan_opportunities
from db import (
    get_activity_telemetry,
    get_daily_plan,
    get_daily_review,
    get_learned_preferences,
    get_shared_insights,
)

router = APIRouter()


class TriggerRequest(BaseModel):
    force: bool = False
    date_str: str | None = None


@router.get("/status")
async def get_overview_status(
    profile: str = Query(default="default", description="Target profile ID"),
) -> dict[str, Any]:
    """Return overview metrics: Brain interoception, plan status, and active counts."""
    settings = get_settings()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    brain = BrainAdapter(default_tz=settings.timezone)
    brain_summary = await brain.evaluate_decision()

    plan = get_daily_plan(today, profile, db_path=settings.db_path)
    tasks = plan.get("tasks", []) if plan else []
    completed_today = is_cycle_completed(profile, today, db_path=settings.db_path)

    return {
        "profile": profile,
        "date": today,
        "cycle_completed": completed_today,
        "brain": brain_summary.model_dump(mode="json"),
        "scheduled_tasks_count": len(tasks),
        "adapters_ready": {
            "brain": await brain.is_available(),
            "calendar": True,
            "kanban": True,
        },
    }


@router.get("/briefing/today")
async def get_active_briefing(
    profile: str = Query(default="default"),
    date_str: str | None = None,
) -> dict[str, Any]:
    """Get today's briefing snapshot and source statuses."""
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    from db import get_briefing_snapshot
    briefing = get_briefing_snapshot(today, profile)
    if not briefing:
        raise HTTPException(status_code=404, detail=f"No briefing found for {today}")
    return {"briefing": briefing}


@router.get("/plan/today")
async def get_active_plan(
    profile: str = Query(default="default"),
    date_str: str | None = None,
) -> dict[str, Any]:
    """Get active daily plan and scheduled time blocks."""
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    plan = get_daily_plan(today, profile)
    if not plan:
        raise HTTPException(status_code=404, detail=f"No plan found for {today}")
    return {"plan": plan}


@router.get("/review/today")
async def get_active_review(
    profile: str = Query(default="default"),
    date_str: str | None = None,
) -> dict[str, Any]:
    """Get EOD review report."""
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    review = get_daily_review(today, profile)
    if not review:
        raise HTTPException(status_code=404, detail=f"No review recorded for {today}")
    return {"review": review}


@router.get("/learning/insights")
async def get_insights(
    profile: str = Query(default="default"),
) -> dict[str, Any]:
    """Get calibrated duration multipliers and published shared insights."""
    calibrations = get_learned_preferences(profile_id=profile)
    shared = get_shared_insights()
    return {
        "profile": profile,
        "learned_calibrations": calibrations,
        "shared_insights": shared,
    }


@router.get("/opportunities")
async def get_automation_opportunities(
    profile: str = Query(default="default"),
) -> dict[str, Any]:
    """Get proactive automation opportunities detected by opportunity scanner."""
    opps = scan_opportunities(profile_id=profile)
    return {
        "profile": profile,
        "opportunities": opps,
    }


@router.post("/cycle/trigger")
async def trigger_cycle(payload: TriggerRequest) -> dict[str, Any]:
    """Manually trigger daily cycle run."""
    try:
        res = await run_daily_cycle(force=payload.force, date_str=payload.date_str, trigger_source="dashboard_api")
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
