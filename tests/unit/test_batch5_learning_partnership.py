from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from core.opportunity import scaffold_skill_for_opportunity
from core.optimizer import (
    is_duplicate_shared_insight,
    is_telemetry_outlier,
)
from core.review import format_eod_review_markdown
from db import init_db, save_daily_review


def test_is_telemetry_outlier():
    # 10m estimated, 120m actual -> 12x ratio -> outlier
    assert is_telemetry_outlier(10, 120) is True

    # 100m estimated, 5m actual -> 0.05x ratio -> outlier
    assert is_telemetry_outlier(100, 5) is True

    # 40m estimated, 55m actual -> 1.375x ratio -> valid data
    assert is_telemetry_outlier(40, 55) is False


def test_is_duplicate_shared_insight():
    existing = [
        {
            "category": "social_media",
            "finding": "Evening publishing at 19:00 yields higher engagement",
        }
    ]
    # Exact duplicate
    assert is_duplicate_shared_insight("social_media", "Evening publishing at 19:00 yields higher engagement", existing) is True

    # Different category/finding
    assert is_duplicate_shared_insight("data_scraping", "Night scraping avoids rate limits", existing) is False


def test_scaffold_skill_for_opportunity():
    tmp = tempfile.mkdtemp(prefix="test-b5-skills-")
    skills_dir = Path(tmp)

    opp = {
        "activity_type": "bank_reconciliation",
        "slug": "bank-reconciliation",
        "occurrences": 4,
    }
    skill_file = scaffold_skill_for_opportunity(opp, target_skills_dir=skills_dir)
    assert skill_file.is_file()
    content = skill_file.read_text(encoding="utf-8")
    assert "name: bank-reconciliation" in content
    assert "## When to Use" in content
    assert "## Procedure" in content
    assert "## Verification" in content

    shutil.rmtree(tmp, ignore_errors=True)


def test_format_eod_review_tts_word_cap():
    tmp = tempfile.mkdtemp(prefix="test-b5-rev-")
    db_file = Path(tmp) / "daily_life.db"
    init_db(db_file)

    # Save review with 700 words of reflection
    long_reflection = "word " * 700
    save_daily_review(
        "2026-11-08",
        "hartono",
        {
            "review_date": "2026-11-08",
            "profile_id": "hartono",
            "completed_task_ids": ["t-1"],
            "incomplete_task_ids": [],
            "deviations": [],
            "outcomes": {},
            "reflection_notes": long_reflection,
        },
        db_path=db_file,
    )

    md = format_eod_review_markdown(profile_id="hartono", review_date="2026-11-08", db_path=db_file)
    assert "[[audio_as_voice]]" in md
    # Total words in reflection within markdown should be bounded
    assert md.count("word") <= 505

    shutil.rmtree(tmp, ignore_errors=True)
