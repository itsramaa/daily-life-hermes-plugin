from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config import get_settings
from db import get_activity_telemetry, get_daily_plan, get_daily_review, save_daily_review
from models import DailyReviewReport

logger = logging.getLogger(__name__)

__all__ = ["run_daily_review", "format_eod_review_markdown"]


async def run_daily_review(
    profile_id: str | None = None,
    review_date: str | None = None,
    outcome_metrics: dict[str, Any] | None = None,
    db_path: Any = None,
) -> DailyReviewReport:
    """Evaluate completed vs incomplete tasks, calculate outcome metrics and save report."""
    settings = get_settings()
    pid = profile_id or settings.profile_id
    today = review_date or datetime.utcnow().strftime("%Y-%m-%d")

    plan = get_daily_plan(today, pid, db_path=db_path)
    tasks = plan.get("tasks", []) if plan else []

    # Get actual telemetry recorded today
    telemetry = get_activity_telemetry(profile_id=pid, db_path=db_path)
    completed: list[str] = []
    incomplete: list[str] = []
    deviations: list[str] = []

    for t in tasks:
        tid = t.get("task_id", "unknown")
        # Check if completed flag in plan or matching telemetry completed
        matching_tel = [r for r in telemetry if tid in r.get("activity_type", "")]
        is_done = t.get("completed", False) or any(r.get("status") in ("completed", "success") for r in matching_tel)
        if is_done:
            completed.append(tid)
        else:
            incomplete.append(tid)

    total_est = sum(t.get("estimated_duration_min", 30) for t in tasks)
    total_act = sum(r.get("actual_duration_min", 0) for r in telemetry)
    if total_act > 0 and total_est > 0:
        ratio = total_act / total_est
        if abs(ratio - 1.0) > 0.25:
            deviations.append(f"Duration variance: actual took {total_act}m vs estimated {total_est}m ({ratio:.2f}x).")

    outcomes = {
        "completion_rate": len(completed) / max(1, len(tasks)),
        "total_estimated_min": total_est,
        "total_actual_min": total_act,
    }
    if outcome_metrics:
        outcomes.update(outcome_metrics)

    reflection = (
        f"EOD Review for {pid} on {today}. Completed {len(completed)} of {len(tasks)} tasks. "
        f"Total active time: {total_act}m."
    )

    report = DailyReviewReport(
        review_date=today,
        profile_id=pid,
        completed_task_ids=completed,
        incomplete_task_ids=incomplete,
        deviations=deviations,
        outcomes=outcomes,
        reflection_notes=reflection,
    )

    save_daily_review(today, pid, report.model_dump(mode="json"), db_path=db_path)

    # Subsystem 1, Point 1: Trigger nightly brain recovery & mind consolidation
    try:
        from core.adapters.brain import BrainAdapter
        brain = BrainAdapter(default_tz=settings.timezone)
        await brain.run_nightly_maintenance()
    except Exception as exc:
        logger.debug("Daily review nightly brain maintenance failed: %s", exc)

    # Subsystem 10, Point 91: SQLite WAL checkpoint
    try:
        from db import checkpoint_wal
        checkpoint_wal(db_path=db_path)
    except Exception as exc:
        logger.debug("Failed running checkpoint_wal during review: %s", exc)

    return report


def format_eod_review_markdown(profile_id: str | None = None, review_date: str | None = None, db_path: Any = None) -> str:
    """Format markdown summary for /daily review command with audio directive."""
    settings = get_settings()
    pid = profile_id or settings.profile_id
    today = review_date or datetime.utcnow().strftime("%Y-%m-%d")

    target_db = db_path or settings.db_path
    review = get_daily_review(today, pid, db_path=target_db)
    if not review:
        return f"### Evening Review ({today})\n*No review submitted yet for profile `{pid}`.*"

    completed_count = len(review.get("completed_task_ids", []))
    incomplete_count = len(review.get("incomplete_task_ids", []))
    deviations = review.get("deviations", [])
    outcomes = review.get("outcomes", {})

    dev_lines = "\n".join(f"- {d}" for d in deviations) if deviations else "- On schedule, no major deviations."
    metrics_str = ", ".join(f"{k}: {v}" for k, v in outcomes.items() if k not in ("total_estimated_min", "total_actual_min"))

    # Subsystem 9, Point 85: Cap text to ~500 words before TTS generation
    reflection = review.get("reflection_notes", "")
    words = reflection.split()
    if len(words) > 500:
        reflection = " ".join(words[:500]) + "..."

    return (
        f"### Evening Review ({today})\n"
        f"**Profile:** `{pid}` | **Tasks Done:** {completed_count} | **Remaining:** {incomplete_count}\n\n"
        f"#### Outcome & Business Metrics:\n"
        f"{metrics_str or 'Baseline metrics tracked.'}\n\n"
        f"#### Duration & Deviations:\n"
        f"{dev_lines}\n\n"
        f"**Reflection:** {reflection}\n\n"
        f"[[audio_as_voice]]"
    )
