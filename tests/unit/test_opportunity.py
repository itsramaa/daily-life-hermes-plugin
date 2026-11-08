from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from core.opportunity import scan_opportunities
from db import init_db, record_task_telemetry


def test_scan_opportunities_empty():
    tmp = tempfile.mkdtemp(prefix="test-empty-")
    db_file = Path(tmp) / "empty.db"
    opps = scan_opportunities(profile_id="empty_profile", db_path=db_file)
    shutil.rmtree(tmp, ignore_errors=True)
    assert opps == []


def test_scan_opportunities_detects_repetition(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="test-opp-scan-")
    tmp_path = Path(tmp)
    db_file = tmp_path / "daily_life.db"
    init_db(db_file)

    monkeypatch.setenv("HERMES_PROFILE_ID", "profil_a")
    monkeypatch.setenv("DAILY_LIFE_DATA_DIR", str(tmp_path))

    # Add 3 recurring completed tasks
    for _ in range(3):
        record_task_telemetry(
            profile_id="profil_a",
            activity_type="reconciliation_workflow",
            day_of_week=0,
            hour_of_day=14,
            estimated_duration_min=30,
            actual_duration_min=40,
            status="completed",
            db_path=db_file,
        )

    # Add 1 sporadic task (should NOT be suggested)
    record_task_telemetry(
        profile_id="profil_a",
        activity_type="rare_migration",
        day_of_week=0,
        hour_of_day=15,
        estimated_duration_min=60,
        actual_duration_min=60,
        status="completed",
        db_path=db_file,
    )

    opps = scan_opportunities(profile_id="profil_a", db_path=db_file)
    shutil.rmtree(tmp, ignore_errors=True)

    assert len(opps) == 1
    assert opps[0]["activity_type"] == "reconciliation_workflow"
    assert opps[0]["occurrences"] == 3
    assert opps[0]["estimated_time_savings_min"] > 0
