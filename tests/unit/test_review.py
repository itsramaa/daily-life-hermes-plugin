from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from core.review import format_eod_review_markdown, run_daily_review
from db import init_db, record_task_telemetry, save_daily_plan


@pytest.mark.asyncio
async def test_run_daily_review_calculation():
    tmp = tempfile.mkdtemp(prefix="test-review-")
    db_file = Path(tmp) / "daily_life.db"
    init_db(db_file)

    # 1. Seed plan with 2 tasks
    save_daily_plan(
        "2026-11-08",
        "hartono",
        {
            "tasks": [
                {"task_id": "t-1", "title": "Scrape Portal", "estimated_duration_min": 30, "completed": True},
                {"task_id": "t-2", "title": "Triage Inbox", "estimated_duration_min": 20, "completed": False},
            ]
        },
        db_path=db_file,
    )

    # 2. Record telemetry showing variance
    record_task_telemetry(
        profile_id="hartono",
        activity_type="t-1",
        day_of_week=0,
        hour_of_day=10,
        estimated_duration_min=30,
        actual_duration_min=80,
        status="completed",
        db_path=db_file,
    )

    # 3. Run EOD Review with business outcome metrics
    report = await run_daily_review(
        profile_id="hartono",
        review_date="2026-11-08",
        outcome_metrics={"pages_scraped": 150, "accuracy_rate": 0.99},
        db_path=db_file,
    )

    assert report.review_date == "2026-11-08"
    assert "t-1" in report.completed_task_ids
    assert "t-2" in report.incomplete_task_ids
    assert report.outcomes["completion_rate"] == 0.5
    assert report.outcomes["pages_scraped"] == 150
    assert report.outcomes["accuracy_rate"] == 0.99
    assert len(report.deviations) >= 1  # 50m vs 50m total

    # 4. Format markdown
    md = format_eod_review_markdown(profile_id="hartono", review_date="2026-11-08", db_path=db_file)
    assert "Evening Review (2026-11-08)" in md
    assert "pages_scraped: 150" in md
    assert "[[audio_as_voice]]" in md

    shutil.rmtree(tmp, ignore_errors=True)
