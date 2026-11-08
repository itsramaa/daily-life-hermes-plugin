from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.adapters.calendar import CalendarAdapter
from core.planner import (
    build_plan,
    calculate_context_switch_penalty,
    check_dependency_deadlock,
    decompose_epic_task,
)
from models import (
    BrainStateSummary,
    BriefingContext,
    ExecutionTarget,
    GateVerdict,
)


def test_check_dependency_deadlock():
    # Deadlocked cycle: A -> B -> A
    cycle_deps = {"task_a": ["task_b"], "task_b": ["task_a"]}
    assert check_dependency_deadlock(cycle_deps) is True

    # Valid acyclic DAG: A -> B -> C
    valid_deps = {"task_a": ["task_b"], "task_b": ["task_c"], "task_c": []}
    assert check_dependency_deadlock(valid_deps) is False


def test_decompose_epic_task():
    # Regular task <= 120m remains 1 task
    small = decompose_epic_task("Quick code fix", 45)
    assert len(small) == 1
    assert small[0]["estimated_duration_min"] == 45

    # Epic task > 120m (e.g. 150m) decomposed into parts
    epic = decompose_epic_task("Rewrite Legacy Backend", 150, max_subtask_min=60)
    assert len(epic) == 3
    assert epic[0]["estimated_duration_min"] == 60
    assert epic[1]["estimated_duration_min"] == 60
    assert epic[2]["estimated_duration_min"] == 30
    assert "Part 1/3" in epic[0]["title"]


def test_calculate_context_switch_penalty():
    assert calculate_context_switch_penalty(ExecutionTarget.KANBAN_NATIVE, ExecutionTarget.CAMOUFOX_SCENARIO) == 10
    assert calculate_context_switch_penalty(ExecutionTarget.CAMOUFOX_SCENARIO, ExecutionTarget.CAMOUFOX_SCENARIO) == 0
    assert calculate_context_switch_penalty(None, ExecutionTarget.CAMOUFOX_SCENARIO) == 0


@pytest.mark.asyncio
async def test_build_plan_weekend_mode():
    briefing = BriefingContext(
        brain_summary=BrainStateSummary(verdict=GateVerdict.PROCEED, energy=0.9, fatigue=0.1),
        pending_tasks=[{"title": f"Task {i}", "estimated_duration_min": 25} for i in range(1, 10)],
    )

    # 2026-11-08 was a Sunday (weekday 6)
    plan = await build_plan(briefing, profile_id="hartono", plan_date="2026-11-08")
    assert len(plan.tasks) <= 2  # Weekend rhythm capped


@pytest.mark.asyncio
async def test_calendar_prep_and_recap_insertion():
    adapter = CalendarAdapter()
    meeting_start = datetime(2026, 11, 9, 14, 0, 0)
    meeting_end = datetime(2026, 11, 9, 15, 0, 0)

    # 1. Pre-meeting prep
    prep = await adapter.insert_pre_meeting_prep("Investor Pitch", meeting_start=meeting_start, prep_duration_min=15)
    assert "Prep for: Investor Pitch" in prep["title"]
    assert prep["end"] == meeting_start.isoformat()

    # 2. Post-meeting recap
    recap = await adapter.insert_post_meeting_recap("Investor Pitch", meeting_end=meeting_end, recap_duration_min=15)
    assert "Recap & Action Items: Investor Pitch" in recap["title"]
    assert recap["start"] == meeting_end.isoformat()

    # 3. Dynamic shift / update
    updated = await adapter.update_event_time("primary", "ev-1", new_start=meeting_start, new_end=meeting_end)
    assert updated["id"] == "ev-1"
    assert updated["start"] == meeting_start.isoformat()


@pytest.mark.asyncio
async def test_calendar_sync_upstream_google():
    mock_gcal = AsyncMock()
    mock_gcal.list_events.return_value = [
        {"summary": "Board Meeting", "start": "2026-11-09T10:00:00", "end": "2026-11-09T11:00:00"}
    ]

    adapter = CalendarAdapter()
    synced = await adapter.sync_upstream_google_calendar(mock_gcal)
    assert len(synced) == 1
    assert "[GCal] Board Meeting" in synced[0]["title"]
    assert synced[0]["source"] == "google_calendar"
