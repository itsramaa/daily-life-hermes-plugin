from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.planner import build_plan, route_task_execution_target
from db import init_db, record_task_telemetry
from models import (
    BrainStateSummary,
    BriefingContext,
    ExecutionTarget,
    GateVerdict,
)


def test_route_task_execution_target():
    target, scenario = route_task_execution_target("Scrape Cloud Portal Downloads")
    assert target == ExecutionTarget.CAMOUFOX_SCENARIO
    assert scenario == "multi_portal_pipeline"

    target, _ = route_task_execution_target("Triage morning email newsletters")
    assert target == ExecutionTarget.GMAIL_TRIAGE

    target, _ = route_task_execution_target("Publish daily announcement to Threads")
    assert target == ExecutionTarget.TEBAR_PUBLISH

    target, _ = route_task_execution_target("Implement core service unit tests")
    assert target == ExecutionTarget.KANBAN_NATIVE


@pytest.mark.asyncio
async def test_build_plan_veto_halts_planning():
    briefing = BriefingContext(
        brain_summary=BrainStateSummary(verdict=GateVerdict.VETO, reasons=["quiet_hours"]),
        pending_tasks=[{"title": "Pending task 1"}],
    )
    plan = await build_plan(briefing, profile_id="hartono", plan_date="2026-11-08")
    assert len(plan.tasks) == 0


@pytest.mark.asyncio
async def test_build_plan_caution_caps_capacity():
    # Brain under CAUTION (fatigue >= 0.70)
    briefing = BriefingContext(
        brain_summary=BrainStateSummary(
            verdict=GateVerdict.CAUTION,
            fatigue=0.75,
            energy=0.25,
            reasons=["caution_fatigue"],
        ),
        pending_tasks=[
            {"title": f"Backlog item {i}", "estimated_duration_min": 30}
            for i in range(1, 10)
        ],
    )
    plan = await build_plan(briefing, profile_id="hartono", plan_date="2026-11-08")
    # Must be capped at max 2 tasks (alignment + 1 item)
    assert len(plan.tasks) <= 2


@pytest.mark.asyncio
async def test_build_plan_kanban_dispatch():
    mock_kanban = AsyncMock()
    mock_kanban.create_task.return_value = {"status": "created"}

    briefing = BriefingContext(
        brain_summary=BrainStateSummary(verdict=GateVerdict.PROCEED, energy=0.9, fatigue=0.1),
        pending_tasks=[
            {"title": "Scrape portal price matrix", "estimated_duration_min": 30},
            {"title": "Draft post for Threads", "estimated_duration_min": 25},
        ],
    )

    plan = await build_plan(
        briefing,
        profile_id="hartono",
        plan_date="2026-11-09",
        kanban_adapter=mock_kanban,
    )

    assert len(plan.tasks) >= 3  # alignment + 2 routed tasks
    assert mock_kanban.create_task.call_count == 2

    # Check task targets routed correctly
    task_targets = [t.execution_target for t in plan.tasks]
    assert ExecutionTarget.CAMOUFOX_SCENARIO in task_targets
    assert ExecutionTarget.TEBAR_PUBLISH in task_targets


@pytest.mark.asyncio
async def test_build_plan_with_opportunity_discovery(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="test-opp-")
    tmp_path = Path(tmp)
    db_file = tmp_path / "test.db"
    init_db(db_file)

    monkeypatch.setenv("HERMES_PROFILE_ID", "hartono")
    monkeypatch.setenv("DAILY_LIFE_DATA_DIR", str(tmp_path))

    # Seed 4 repetitive tasks to trigger opportunity discovery
    for _ in range(4):
        record_task_telemetry(
            profile_id="hartono",
            activity_type="bank_csv_reconciliation",
            day_of_week=1,
            hour_of_day=10,
            estimated_duration_min=40,
            actual_duration_min=45,
            status="completed",
            db_path=db_file,
        )

    briefing = BriefingContext(
        brain_summary=BrainStateSummary(verdict=GateVerdict.PROCEED, energy=0.9, fatigue=0.1),
        pending_tasks=[{"title": "Standard audit", "estimated_duration_min": 30}],
    )

    plan = await build_plan(
        briefing,
        profile_id="hartono",
        plan_date="2026-11-08",
        db_path=db_file,
    )
    shutil.rmtree(tmp, ignore_errors=True)

    # Opportunity task should be created
    titles = [t.title for t in plan.tasks]
    assert any("Automate Repetitive Workflow" in title for title in titles)
