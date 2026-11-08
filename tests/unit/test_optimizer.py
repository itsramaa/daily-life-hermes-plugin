from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from core.optimizer import (
    calibrate_activity_durations,
    get_calibrated_task_duration,
    publish_shared_insights,
)
from db import (
    get_shared_insights,
    init_db,
    record_task_telemetry,
)


def test_calibrate_activity_durations_sample_threshold():
    tmp = tempfile.mkdtemp(prefix="test-opt-")
    db_file = Path(tmp) / "daily_life.db"
    init_db(db_file)

    # 1. Add 4 samples (< 7 threshold)
    for _ in range(4):
        record_task_telemetry(
            profile_id="hartono",
            activity_type="deep_research",
            day_of_week=2,
            hour_of_day=19,
            estimated_duration_min=20,
            actual_duration_min=40,
            status="completed",
            db_path=db_file,
        )

    # Threshold N >= 7 prevents premature overfitting
    mults = calibrate_activity_durations(profile_id="hartono", min_samples=7, db_path=db_file)
    assert mults == {}
    assert get_calibrated_task_duration("deep_research", 20, profile_id="hartono", db_path=db_file) == 20

    # 2. Add 4 more samples (Total N = 8 >= 7)
    for _ in range(4):
        record_task_telemetry(
            profile_id="hartono",
            activity_type="deep_research",
            day_of_week=2,
            hour_of_day=19,
            estimated_duration_min=20,
            actual_duration_min=36,
            status="completed",
            db_path=db_file,
        )

    mults = calibrate_activity_durations(profile_id="hartono", min_samples=7, db_path=db_file)
    assert "deep_research" in mults
    # Total actual: 4*40 + 4*36 = 160 + 144 = 304. Total est: 8*20 = 160.
    # 304 / 160 = 1.9x
    assert mults["deep_research"] >= 1.8

    calibrated_dur = get_calibrated_task_duration("deep_research", 20, profile_id="hartono", db_path=db_file)
    assert calibrated_dur >= 36  # Automatically scaled from 20m to ~38m

    shutil.rmtree(tmp, ignore_errors=True)


def test_publish_shared_insights_collective_knowledge():
    tmp = tempfile.mkdtemp(prefix="test-opt-pub-")
    db_file = Path(tmp) / "daily_life.db"
    init_db(db_file)

    # Seed 32 samples (N >= 30) with 100% success rate at 19:00
    for _ in range(32):
        record_task_telemetry(
            profile_id="hartono",
            activity_type="threads_evening_post",
            day_of_week=0,
            hour_of_day=19,
            estimated_duration_min=15,
            actual_duration_min=15,
            status="success",
            outcome_metric_name="engagement",
            outcome_metric_value=250.0,
            db_path=db_file,
        )

    calibrate_activity_durations(profile_id="hartono", min_samples=7, db_path=db_file)
    published = publish_shared_insights(profile_id="hartono", min_samples=30, min_confidence=0.80, db_path=db_file)

    assert len(published) == 1
    assert published[0]["source_profile"] == "hartono"
    assert published[0]["sample_size"] == 32
    assert "threads_evening_post" in published[0]["finding"]

    # Verify other profiles can query published shared insights
    shared_rows = get_shared_insights(db_path=db_file)
    assert len(shared_rows) == 1
    assert shared_rows[0]["category"] == "scheduling_optimization"

    shutil.rmtree(tmp, ignore_errors=True)
