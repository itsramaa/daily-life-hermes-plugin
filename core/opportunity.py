from __future__ import annotations

from collections import Counter
import logging
import os
import re
from pathlib import Path
from typing import Any

from db import get_activity_telemetry

logger = logging.getLogger(__name__)

__all__ = ["scan_opportunities", "scaffold_skill_for_opportunity"]


def scan_opportunities(profile_id: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Subsystem 9, Point 90: Scan activity history to discover repetitive workflows suitable for automation."""
    telemetry = get_activity_telemetry(profile_id=profile_id, db_path=db_path)
    if not telemetry:
        return []

    activities = [
        row["activity_type"]
        for row in telemetry
        if row.get("status") in ("success", "completed")
    ]
    counts = Counter(activities)
    opportunities: list[dict[str, Any]] = []

    for activity_type, count in counts.items():
        if count >= 3:
            total_duration = sum(
                row.get("actual_duration_min", 0)
                for row in telemetry
                if row["activity_type"] == activity_type
            )
            slug = re.sub(r"[^a-z0-9]+", "-", activity_type.lower()).strip("-")
            opportunities.append({
                "type": "workflow_automation",
                "activity_type": activity_type,
                "slug": slug,
                "occurrences": count,
                "total_time_spent_min": total_duration,
                "estimated_time_savings_min": int(total_duration * 0.6),
                "recommendation": f"Workflow '{activity_type}' occurred {count} times. Automating it could save ~{int(total_duration * 0.6)} mins.",
            })

    return opportunities


def scaffold_skill_for_opportunity(
    opportunity: dict[str, Any],
    target_skills_dir: Path | None = None,
) -> Path:
    """Subsystem 9, Point 90: Automatically scaffold a valid SKILL.md when repetitive workflow is detected."""
    slug = opportunity.get("slug") or re.sub(r"[^a-z0-9]+", "-", opportunity["activity_type"].lower()).strip("-")
    activity = opportunity["activity_type"]

    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    base_dir = target_skills_dir or (hermes_home / "skills")
    skill_dir = base_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    content = f"""---
name: {slug}
description: "Automated procedure for {activity}"
version: 1.0.0
metadata:
  hermes:
    tags: [automation, daily-life, workflow]
---

# {activity.replace('_', ' ').title()} Automation

## When to Use
Use when executing repetitive tasks matching `{activity}`.

## Procedure
1. Initialize task context and required input parameters.
2. Run automated script execution via execute_code or targeted adapter.
3. Verify output integrity and log completion status to activity telemetry.

## Verification
Confirm task completion status is `completed` with zero errors.
"""
    skill_file.write_text(content, encoding="utf-8")
    logger.info("Scaffolded new automated skill at %s", skill_file)
    return skill_file
