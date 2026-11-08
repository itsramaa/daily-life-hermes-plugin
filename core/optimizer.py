from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
import statistics
from typing import Any

from config import get_settings
from db import (
    get_activity_telemetry,
    get_learned_preferences,
    get_shared_insights,
    save_learned_preference,
    save_shared_insight,
)
from models import LearnedInsight

logger = logging.getLogger(__name__)

__all__ = [
    "calibrate_activity_durations",
    "publish_shared_insights",
    "get_calibrated_task_duration",
    "is_telemetry_outlier",
    "is_duplicate_shared_insight",
    "apply_sample_ceiling",
    "compute_confidence_degradation",
    "is_negative_learning_circuit_breaker",
    "track_estimation_drift",
    "normalize_outcome_metric",
    "archive_telemetry_rows",
    "profile_task_resources",
]

# Subsystem 8, Point 73: hard caps on sample counts per activity category.
_DEFAULT_MIN_SAMPLES: int = 7
_MAX_SAMPLES: int = 200
# Subsystem 8, Point 74: recent-variance window (last N runs) for confidence decay.
_CONFIDENCE_WINDOW: int = 5
# Subsystem 8, Point 77: drift-tracking horizon.
_DRIFT_HORIZON_DAYS: int = 30
# Subsystem 8, Point 78: canonical outcome metric names → unit scalers.
_OUTCOME_METRIC_SCALERS: dict[str, float] = {
    "duration_min": 1.0,
    "error_count": 10.0,
    "token_used": 10000.0,
    "cost_usd": 100.0,
    "hits_rate": 1.0,  # already in [0,1]
}


def is_telemetry_outlier(estimated_min: int, actual_min: int) -> bool:
    """Subsystem 8, Point 71: Discard extreme outliers (> 5x or < 0.1x of estimate) to protect calibration."""
    if estimated_min <= 0 or actual_min <= 0:
        return False
    ratio = actual_min / estimated_min
    return ratio > 5.0 or ratio < 0.10


def is_duplicate_shared_insight(category: str, finding: str, existing_insights: list[dict[str, Any]]) -> bool:
    """Subsystem 8, Point 75: Avoid inserting duplicate or redundant insights into shared catalog."""
    clean_find = re.sub(r"[^a-z0-9]", "", finding.lower())
    for item in existing_insights:
        if item.get("category") == category:
            existing_clean = re.sub(r"[^a-z0-9]", "", item.get("finding", "").lower())
            if clean_find == existing_clean or (len(clean_find) > 20 and clean_find in existing_clean):
                return True
    return False


def calibrate_activity_durations(
    profile_id: str | None = None,
    min_samples: int = 7,
    db_path: Path | None = None,
) -> dict[str, float]:
    """Level 1 - Private Learning: Calibrate duration multipliers with outlier filtering and time decay."""
    pid = profile_id or get_settings().profile_id
    telemetry = get_activity_telemetry(profile_id=pid, db_path=db_path)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in telemetry:
        act = row["activity_type"]
        grouped[act].append(row)

    multipliers: dict[str, float] = {}
    now = datetime.utcnow()

    for act_type, rows in grouped.items():
        # Point 71: Filter out extreme outliers
        valid_rows = [
            r for r in rows
            if not is_telemetry_outlier(r.get("estimated_duration_min", 0), r.get("actual_duration_min", 0))
        ]
        sample_count = len(valid_rows)
        if sample_count < min_samples:
            continue

        # Point 73: Strict sample ceiling — cap the effective count per category.
        effective_samples = apply_sample_ceiling(sample_count)

        # Point 76: Negative learning circuit breaker — skip calibration when the
        # history shows systemic (network/infra) failures rather than behaviour.
        recent_failure_reason = valid_rows[-1].get("error_reason") if valid_rows else None
        success_rate_raw = (
            len([r for r in valid_rows if r.get("status") in ("success", "completed")]) / max(1, sample_count)
        )
        if is_negative_learning_circuit_breaker(recent_failure_reason, success_rate_raw):
            logger.info(
                "Skipping calibration for '%s': systemic failure detected (%s).",
                act_type,
                recent_failure_reason,
            )
            continue

        # Point 72: Time-decay weighting (1.0 for <= 14d, 0.5 for older)
        weighted_act = 0.0
        weighted_est = 0.0

        for r in valid_rows:
            row_created = r.get("created_at")
            weight = 1.0
            if row_created:
                try:
                    dt = datetime.fromisoformat(row_created)
                    if now - dt > timedelta(days=14):
                        weight = 0.5
                except Exception:
                    pass

            weighted_act += (r.get("actual_duration_min", 0) * weight)
            weighted_est += (r.get("estimated_duration_min", 0) * weight)

        if weighted_est > 0:
            multiplier = round(weighted_act / weighted_est, 2)
        else:
            multiplier = 1.0

        successful_rows = [r for r in valid_rows if r.get("status") in ("success", "completed")]
        success_rate = len(successful_rows) / max(1, sample_count)

        hour_counts: dict[int, int] = defaultdict(int)
        day_counts: dict[int, int] = defaultdict(int)
        for r in successful_rows:
            hour_counts[r["hour_of_day"]] += 1
            day_counts[r["day_of_week"]] += 1

        best_hour = max(hour_counts.keys(), key=lambda h: hour_counts[h]) if hour_counts else 14
        best_day = max(day_counts.keys(), key=lambda d: day_counts[d]) if day_counts else 1

        confidence = min(0.95, round(success_rate * min(1.0, sample_count / 20.0), 2))

        # Point 74: Confidence degradation when recent-run variance spikes.
        recent_actuals = [r.get("actual_duration_min", 0) for r in valid_rows[-_CONFIDENCE_WINDOW:]]
        confidence = compute_confidence_degradation(recent_actuals, base_confidence=confidence)

        save_learned_preference(
            profile_id=pid,
            activity_type=act_type,
            duration_multiplier=multiplier,
            optimal_window_start=best_hour,
            optimal_window_end=(best_hour + 2) % 24,
            best_day_of_week=best_day,
            sample_count=effective_samples,
            confidence_score=confidence,
            db_path=db_path,
        )
        multipliers[act_type] = multiplier

    return multipliers


def publish_shared_insights(
    profile_id: str | None = None,
    min_samples: int = 30,
    min_confidence: float = 0.80,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Level 2 - Collective Advisory: Publish validated high-confidence insights to shared catalog."""
    pid = profile_id or get_settings().profile_id
    calibrations = get_learned_preferences(profile_id=pid, db_path=db_path)
    existing_insights = get_shared_insights(db_path=db_path)
    published: list[dict[str, Any]] = []

    for act_type, data in calibrations.items():
        sample_count = data.get("sample_count", 0)
        confidence = data.get("confidence_score", 0.0)

        if sample_count >= min_samples and confidence >= min_confidence:
            mult = data.get("duration_multiplier", 1.0)
            window_start = data.get("optimal_window_start", 14)
            insight_id = f"ins_{pid}_{act_type}_{window_start}h"
            finding = (
                f"Activity '{act_type}' consistently requires {mult:.2f}x estimated duration. "
                f"Optimal execution window peaks around {window_start:02d}:00."
            )

            # Point 75: Avoid duplicate insertion
            if is_duplicate_shared_insight("scheduling_optimization", finding, existing_insights):
                continue

            insight = LearnedInsight(
                id=insight_id,
                category="scheduling_optimization",
                finding=finding,
                confidence_score=confidence,
                sample_size=sample_count,
                source_profile=pid,
                advisory_weight="high" if confidence >= 0.90 else "medium",
            )
            save_shared_insight(insight.model_dump(), db_path=db_path)
            existing_insights.append(insight.model_dump())
            published.append(insight.model_dump())

    return published


def get_calibrated_task_duration(
    activity_type: str,
    raw_estimate_min: int,
    profile_id: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Return realistic duration calibrated by historical performance."""
    pid = profile_id or get_settings().profile_id
    prefs = get_learned_preferences(profile_id=pid, db_path=db_path)

    if activity_type in prefs:
        mult = prefs[activity_type].get("duration_multiplier", 1.0)
        return max(1, int(round(raw_estimate_min * mult)))

    return raw_estimate_min


# ---------------------------------------------------------------------------
# Subsystem 8 — additional hardening points (73, 74, 76, 77, 78, 79, 80)
# ---------------------------------------------------------------------------

def apply_sample_ceiling(sample_count: int, ceiling: int = _MAX_SAMPLES) -> int:
    """Subsystem 8, Point 73: Cap effective sample count to prevent runaway weights."""
    return max(0, min(sample_count, ceiling))


def compute_confidence_degradation(
    recent_durations: list[int],
    base_confidence: float = 0.95,
    window: int = _CONFIDENCE_WINDOW,
) -> float:
    """Subsystem 8, Point 74: Penalise confidence when the last-N variance spikes.

    `recent_durations` is a list of *actual* duration minutes in run order
    (newest last). A high coefficient of variation across the window shrinks
    confidence; stable runs keep it.
    """
    if not recent_durations or base_confidence <= 0.0:
        return 0.0
    tail = recent_durations[-window:]
    if len(tail) < 2:
        return round(base_confidence, 2)
    mean = statistics.fmean(tail)
    if mean <= 0:
        return round(base_confidence, 2)
    try:
        stdev = statistics.pstdev(tail)
    except statistics.StatisticsError:
        return round(base_confidence, 2)
    cv = stdev / mean
    # CV 0.0 → no penalty; CV >= 1.0 → half the confidence.
    penalty = min(0.5, cv * 0.25)
    return round(max(0.0, base_confidence - penalty), 2)


def is_negative_learning_circuit_breaker(
    failure_reason: str | None,
    success_rate: float = 1.0,
) -> bool:
    """Subsystem 8, Point 76: Halt learning when failures are systemic.

    Prevents spurious correlations such as "Monday is a bad day" when the
    root cause is a persistent network outage.
    """
    if not failure_reason:
        return False
    lowered = failure_reason.lower()
    system_markers = ("timeout", "network", "503", "504", "connection", "dns")
    is_systemic = any(marker in lowered for marker in system_markers)
    # Trigger the circuit breaker only when systemic AND success rate is low.
    return is_systemic and success_rate < 0.5


def track_estimation_drift(
    telemetry: list[dict[str, Any]],
    horizon_days: int = _DRIFT_HORIZON_DAYS,
) -> dict[str, float]:
    """Subsystem 8, Point 77: Measure agent's time-estimation accuracy over time.

    Returns per-day ratios grouped by calendar day, plus a mean drift factor.
    """
    by_day: dict[str, list[float]] = defaultdict(list)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=horizon_days)

    for row in telemetry:
        est = row.get("estimated_duration_min") or 0
        act = row.get("actual_duration_min") or 0
        if est <= 0 or act <= 0:
            continue
        created = row.get("created_at")
        try:
            day_ts = datetime.fromisoformat(created)
        except Exception:
            continue
        if day_ts < cutoff:
            continue
        day = day_ts.strftime("%Y-%m-%d")
        by_day[day].append(act / est)

    mean_by_day = {day: statistics.fmean(vals) for day, vals in by_day.items()}
    overall = statistics.fmean(mean_by_day.values()) if mean_by_day else 1.0
    return {"per_day": mean_by_day, "overall_drift": round(overall, 4)}


def normalize_outcome_metric(name: str, value: float) -> float:
    """Subsystem 8, Point 78: Scale heterogeneous outcome metrics to [0, 1].

    `name` is the metric name from the telemetry ledger. Unknown names fall
    back to raw value (clipped to [0, 1]).
    """
    if value <= 0:
        return 0.0
    scaler = _OUTCOME_METRIC_SCALERS.get(name, 1.0)
    return min(1.0, max(0.0, value / scaler)) if scaler != 1.0 else min(1.0, max(0.0, value))


def archive_telemetry_rows(
    telemetry: list[dict[str, Any]],
    cutoff_days: int = 90,
    db_path: Path | None = None,
) -> int:
    """Subsystem 8, Point 79: Identify telemetry rows older than `cutoff_days`.

    Returns count of rows to archive. Caller is responsible for writing to
    the archive table; this helper keeps the hot table lean.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=cutoff_days)
    to_move = 0
    for row in telemetry:
        created = row.get("created_at")
        if not created:
            continue
        try:
            ts = datetime.fromisoformat(created)
        except Exception:
            continue
        if ts < cutoff:
            to_move += 1
    return to_move


def profile_task_resources(
    telemetry: list[dict[str, Any]],
    window_minutes: int = 60,
) -> dict[str, float]:
    """Subsystem 8, Point 80: Estimate mean CPU-time cost per activity.

    Uses `actual_duration_min` as a proxy for compute spend when no CPU
    profiler data is recorded. Returns a per-activity rolling average.
    """
    sums: dict[str, list[float]] = defaultdict(list)
    for row in telemetry:
        act = row.get("activity_type")
        if not act:
            continue
        dur = float(row.get("actual_duration_min") or 0)
        sums[act].append(dur)
    return {act: round(statistics.fmean(vals), 2) for act, vals in sums.items() if vals}
