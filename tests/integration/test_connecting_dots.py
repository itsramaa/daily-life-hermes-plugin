from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import get_settings
from core.adapters.brain import BrainAdapter
from core.adapters.calendar import CalendarAdapter
from core.adapters.camoufox import CamoufoxAdapter
from core.adapters.gmail import GmailAdapter
from core.adapters.honcho import HonchoAdapter
from core.adapters.kanban import KanbanAdapter
from core.adapters.tebar import TebarAdapter
from core.briefing import gather_briefing
from core.optimizer import calibrate_activity_durations, get_calibrated_task_duration
from core.planner import build_plan
from core.review import format_eod_review_markdown, run_daily_review
from db import get_daily_plan, init_db, record_task_telemetry
from models import ExecutionTarget, GateVerdict


@pytest.mark.asyncio
async def test_end_to_end_connecting_dots_pipeline(monkeypatch):
    """Integration test verifying end-to-end daily life intelligence pipeline."""
    tmp = tempfile.mkdtemp(prefix="test-e2e-dots-")
    tmp_path = Path(tmp)
    monkeypatch.setenv("HERMES_PROFILE_ID", "hartono")
    monkeypatch.setenv("DAILY_LIFE_DATA_DIR", str(tmp_path))

    settings = get_settings(reload=True)
    db_file = settings.db_path
    init_db(db_file)

    # 1. Brain interoception check
    brain = BrainAdapter(default_tz="UTC")
    brain_summary = await brain.evaluate_decision("daily_planning", now="10:00")
    assert brain_summary.verdict in (GateVerdict.PROCEED, GateVerdict.CAUTION)

    # 2. Resilient Briefing connecting dots
    calendar = CalendarAdapter()
    gmail = GmailAdapter()
    kanban = KanbanAdapter()
    camoufox = CamoufoxAdapter()
    tebar = TebarAdapter()
    honcho = HonchoAdapter()

    assert await kanban.is_available() is True
    assert await camoufox.is_available() is True
    assert await tebar.is_available() is True
    assert await calendar.is_available() is True

    briefing = await gather_briefing(
        brain_summary=brain_summary,
        gmail_adapter=gmail,
        calendar_adapter=calendar,
        kanban_adapter=kanban,
    )
    assert briefing is not None

    # Inject diverse tasks to test connecting dot routing
    briefing.pending_tasks = [
        {"title": "Scrape Cloud Portal Price Tables", "estimated_duration_min": 40},
        {"title": "Triage priority client emails", "estimated_duration_min": 20},
        {"title": "Publish weekly announcement to Threads", "estimated_duration_min": 15},
    ]

    # 3. Planning & Capacity Matching
    mock_kanban = AsyncMock()
    mock_kanban.create_task.return_value = {"status": "created"}

    plan = await build_plan(
        briefing_context=briefing,
        profile_id="hartono",
        plan_date="2026-11-09",
        kanban_adapter=mock_kanban,
        db_path=db_file,
    )

    assert len(plan.tasks) == 4  # 1 alignment + 3 routed tasks
    assert mock_kanban.create_task.call_count == 3

    # Check routing targets
    targets = {t.title: t.execution_target for t in plan.tasks}
    assert targets["Scrape Cloud Portal Price Tables"] == ExecutionTarget.CAMOUFOX_SCENARIO
    assert targets["Triage priority client emails"] == ExecutionTarget.GMAIL_TRIAGE
    assert targets["Publish weekly announcement to Threads"] == ExecutionTarget.TEBAR_PUBLISH

    # Check completion contracts
    for t in plan.tasks:
        assert "outcome" in t.completion_contract
        assert "verification" in t.completion_contract

    # 4. Telemetry logging & Duration Calibration
    # Seed 8 historical runs for scraper showing it takes 60m instead of 40m (1.5x)
    for _ in range(8):
        record_task_telemetry(
            profile_id="hartono",
            activity_type="Scrape Cloud Portal Price Tables",
            day_of_week=0,
            hour_of_day=10,
            estimated_duration_min=40,
            actual_duration_min=60,
            status="completed",
            outcome_metric_name="data_rows",
            outcome_metric_value=500.0,
            db_path=db_file,
        )

    calibrate_activity_durations(profile_id="hartono", min_samples=7, db_path=db_file)
    calibrated = get_calibrated_task_duration("Scrape Cloud Portal Price Tables", 40, profile_id="hartono", db_path=db_file)
    assert calibrated == 60  # Calibrated to 1.5x

    # 5. EOD Review & Reporting
    report = await run_daily_review(
        profile_id="hartono",
        review_date="2026-11-08",
        outcome_metrics={"total_rows_collected": 500, "accuracy_score": 0.98},
        db_path=db_file,
    )
    assert report.profile_id == "hartono"
    assert report.outcomes["total_rows_collected"] == 500

    md = format_eod_review_markdown(profile_id="hartono", review_date="2026-11-08", db_path=db_file)
    assert "total_rows_collected: 500" in md
    assert "[[audio_as_voice]]" in md

    shutil.rmtree(tmp, ignore_errors=True)
    get_settings(reload=True)
