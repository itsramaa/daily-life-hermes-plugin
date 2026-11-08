from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "get_connection",
    "init_db",
    "save_daily_plan",
    "get_daily_plan",
    "save_briefing_snapshot",
    "get_briefing_snapshot",
    "save_daily_review",
    "get_daily_review",
    "record_tool_telemetry",
    "record_task_telemetry",
    "get_activity_telemetry",
    "save_learned_preference",
    "get_learned_preferences",
    "save_shared_insight",
    "get_shared_insights",
    "checkpoint_wal",
    "vacuum_db",
    "redact_sensitive_info",
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    plan_data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_date, profile_id)
);

CREATE TABLE IF NOT EXISTS briefing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    briefing_date TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    briefing_data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(briefing_date, profile_id)
);

CREATE TABLE IF NOT EXISTS daily_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    review_data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(review_date, profile_id)
);

CREATE TABLE IF NOT EXISTS activity_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    day_of_week INTEGER NOT NULL,
    hour_of_day INTEGER NOT NULL,
    estimated_duration_min INTEGER NOT NULL,
    actual_duration_min INTEGER NOT NULL,
    status TEXT NOT NULL,
    outcome_metric_name TEXT,
    outcome_metric_value REAL,
    brain_fatigue_start REAL,
    error_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learned_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    duration_multiplier REAL DEFAULT 1.0,
    optimal_window_start INTEGER,
    optimal_window_end INTEGER,
    best_day_of_week INTEGER,
    sample_count INTEGER DEFAULT 0,
    confidence_score REAL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(profile_id, activity_type)
);

CREATE TABLE IF NOT EXISTS shared_insights (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    finding TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    sample_size INTEGER NOT NULL,
    source_profile TEXT NOT NULL,
    advisory_weight TEXT DEFAULT 'medium',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return an open SQLite connection with WAL mode enabled and schema ensured."""
    target_path = db_path or get_settings().db_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Initialize database tables idempotently."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def save_daily_plan(plan_date: str, profile_id: str, plan_dict: dict[str, Any], db_path: Path | None = None) -> None:
    """Save or update active daily plan."""
    now_iso = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO daily_plans (plan_date, profile_id, plan_data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_date, profile_id) DO UPDATE SET
                plan_data = excluded.plan_data,
                updated_at = excluded.updated_at
            """,
            (plan_date, profile_id, json.dumps(plan_dict), now_iso, now_iso),
        )


def get_daily_plan(plan_date: str, profile_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Fetch stored daily plan for a profile on a given date."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT plan_data FROM daily_plans WHERE plan_date = ? AND profile_id = ?",
            (plan_date, profile_id),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["plan_data"])
    return None


def save_briefing_snapshot(briefing_date: str, profile_id: str, briefing_dict: dict[str, Any], db_path: Path | None = None) -> None:
    """Save or update briefing snapshot."""
    now_iso = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO briefing_snapshots (briefing_date, profile_id, briefing_data, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(briefing_date, profile_id) DO UPDATE SET
                briefing_data = excluded.briefing_data
            """,
            (briefing_date, profile_id, json.dumps(briefing_dict), now_iso),
        )


def get_briefing_snapshot(briefing_date: str, profile_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Fetch stored briefing snapshot."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT briefing_data FROM briefing_snapshots WHERE briefing_date = ? AND profile_id = ?",
            (briefing_date, profile_id),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["briefing_data"])
    return None


def save_daily_review(review_date: str, profile_id: str, review_dict: dict[str, Any], db_path: Path | None = None) -> None:
    """Save EOD daily review."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO daily_reviews (review_date, profile_id, review_data, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(review_date, profile_id) DO UPDATE SET
                review_data = excluded.review_data
            """,
            (review_date, profile_id, json.dumps(review_dict), datetime.utcnow().isoformat()),
        )


def get_daily_review(review_date: str, profile_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Fetch stored daily review."""
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT review_data FROM daily_reviews WHERE review_date = ? AND profile_id = ?",
            (review_date, profile_id),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["review_data"])
    return None


def record_task_telemetry(
    profile_id: str,
    activity_type: str,
    day_of_week: int,
    hour_of_day: int,
    estimated_duration_min: int,
    actual_duration_min: int,
    status: str,
    outcome_metric_name: str | None = None,
    outcome_metric_value: float | None = None,
    brain_fatigue_start: float | None = None,
    error_reason: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Record a completed task attempt in the activity telemetry ledger."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO activity_telemetry (
                profile_id, activity_type, day_of_week, hour_of_day,
                estimated_duration_min, actual_duration_min, status,
                outcome_metric_name, outcome_metric_value, brain_fatigue_start,
                error_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                activity_type,
                day_of_week,
                hour_of_day,
                estimated_duration_min,
                actual_duration_min,
                status,
                outcome_metric_name,
                outcome_metric_value,
                brain_fatigue_start,
                error_reason,
                datetime.utcnow().isoformat(),
            ),
        )


def record_tool_telemetry(
    tool_name: str,
    duration_ms: float,
    status: str,
    task_id: str | None = None,
    error_type: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Observer hook helper: convert raw post_tool_call hook payload into telemetry."""
    now = datetime.utcnow()
    duration_min = max(1, int(duration_ms / 60000.0))
    profile_id = get_settings().profile_id
    record_task_telemetry(
        profile_id=profile_id,
        activity_type=f"tool:{tool_name}",
        day_of_week=now.weekday(),
        hour_of_day=now.hour,
        estimated_duration_min=duration_min,
        actual_duration_min=duration_min,
        status="success" if status in ("success", "ok", "") else "failed",
        error_reason=error_type,
        db_path=db_path,
    )


def get_activity_telemetry(activity_type: str | None = None, profile_id: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Retrieve activity telemetry rows."""
    pid = profile_id or get_settings().profile_id
    with get_connection(db_path) as conn:
        if activity_type:
            cursor = conn.execute(
                "SELECT * FROM activity_telemetry WHERE profile_id = ? AND activity_type = ? ORDER BY id ASC",
                (pid, activity_type),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM activity_telemetry WHERE profile_id = ? ORDER BY id ASC",
                (pid,),
            )
        return [dict(row) for row in cursor.fetchall()]


def save_learned_preference(
    profile_id: str,
    activity_type: str,
    duration_multiplier: float,
    optimal_window_start: int | None,
    optimal_window_end: int | None,
    best_day_of_week: int | None,
    sample_count: int,
    confidence_score: float,
    db_path: Path | None = None,
) -> None:
    """Upsert calibrated preference metrics."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO learned_preferences (
                profile_id, activity_type, duration_multiplier,
                optimal_window_start, optimal_window_end, best_day_of_week,
                sample_count, confidence_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, activity_type) DO UPDATE SET
                duration_multiplier = excluded.duration_multiplier,
                optimal_window_start = excluded.optimal_window_start,
                optimal_window_end = excluded.optimal_window_end,
                best_day_of_week = excluded.best_day_of_week,
                sample_count = excluded.sample_count,
                confidence_score = excluded.confidence_score,
                updated_at = excluded.updated_at
            """,
            (
                profile_id,
                activity_type,
                duration_multiplier,
                optimal_window_start,
                optimal_window_end,
                best_day_of_week,
                sample_count,
                confidence_score,
                datetime.utcnow().isoformat(),
            ),
        )


def get_learned_preferences(profile_id: str | None = None, db_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Retrieve all learned preference calibrations for a profile."""
    pid = profile_id or get_settings().profile_id
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM learned_preferences WHERE profile_id = ?",
            (pid,),
        )
        return {row["activity_type"]: dict(row) for row in cursor.fetchall()}


def save_shared_insight(insight: dict[str, Any], db_path: Path | None = None) -> None:
    """Publish a collective insight."""
    now_iso = datetime.utcnow().isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO shared_insights (
                id, category, finding, confidence_score, sample_size,
                source_profile, advisory_weight, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                finding = excluded.finding,
                confidence_score = excluded.confidence_score,
                sample_size = excluded.sample_size,
                advisory_weight = excluded.advisory_weight,
                updated_at = excluded.updated_at
            """,
            (
                insight["id"],
                insight["category"],
                insight["finding"],
                insight["confidence_score"],
                insight["sample_size"],
                insight["source_profile"],
                insight.get("advisory_weight", "medium"),
                now_iso,
                now_iso,
            ),
        )


def get_shared_insights(category: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Retrieve published shared insights."""
    with get_connection(db_path) as conn:
        if category:
            cursor = conn.execute(
                "SELECT * FROM shared_insights WHERE category = ? ORDER BY confidence_score DESC",
                (category,),
            )
        else:
            cursor = conn.execute("SELECT * FROM shared_insights ORDER BY confidence_score DESC")
        return [dict(row) for row in cursor.fetchall()]


def checkpoint_wal(db_path: Path | None = None) -> None:
    """Subsystem 10, Point 91: Execute SQLite WAL checkpoint to keep size lean."""
    try:
        with get_connection(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception as exc:
        logger.debug("Failed running PRAGMA wal_checkpoint: %s", exc)


def vacuum_db(db_path: Path | None = None) -> None:
    """Subsystem 10, Point 91: Rebuild database file to reclaim free pages."""
    try:
        with get_connection(db_path) as conn:
            conn.execute("VACUUM;")
    except Exception as exc:
        logger.debug("Failed vacuuming database: %s", exc)


def redact_sensitive_info(text: str) -> str:
    """Subsystem 10, Point 92: Sanitize API keys, tokens, and passwords from log strings."""
    redacted = re.sub(
        r"(?i)(api[_-]?key|secret|token|password|bearer\s+)[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}['\"]?",
        r"\1=[REDACTED]",
        text,
    )
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED_KEY]", redacted)
    return redacted
