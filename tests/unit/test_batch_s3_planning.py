from __future__ import annotations

"""Subsystem 3: Adaptive planning hardening (points 25,26,27,29,30)."""

from datetime import datetime, timedelta

from core.planner import (
    decay_backlog,
    default_duration_for_task,
    is_multi_day_milestone,
    match_energy_to_task,
    preempt_for_p0,
)


# --- Point 25: Default duration for unknown tasks ---------------------------
def test_default_duration_by_keyword():
    assert default_duration_for_task("Scrape news articles") == 45
    assert default_duration_for_task("triage inbox") == 20
    assert default_duration_for_task("write blog post") == 40
    assert default_duration_for_task("refactor module") == 60
    # No matching keyword -> 30 default
    assert default_duration_for_task("unknown thing") == 30


# --- Point 26: Energy-task matching -----------------------------------------
def test_match_energy_to_task():
    # Analytical task during peak hours (10:00) -> "peak"
    assert match_energy_to_task("Refactor payment module", current_hour=10) == "peak"
    # Same task at 15:00 (past peak) -> "low"
    assert match_energy_to_task("Refactor payment module", current_hour=15) == "low"
    # Admin task -> always "low"
    assert match_energy_to_task("Triage inbox", current_hour=10) == "low"
    assert match_energy_to_task("Inbox cleanup", current_hour=15) == "low"
    # Neutral task -> "neutral"
    assert match_energy_to_task("Do a thing", current_hour=10) == "neutral"


# --- Point 27: Emergency task preemption ------------------------------------
def test_preempt_for_p0():
    res = preempt_for_p0(current_task_id="t-running", incoming_priority=0)
    assert res["action"] == "preempt"
    assert res["paused_task_id"] == "t-running"
    # P1 does not preempt.
    res2 = preempt_for_p0(current_task_id="t-running", incoming_priority=1)
    assert res2["action"] == "schedule"


# --- Point 29: Backlog carry-over decay -------------------------------------
def test_decay_backlog_archives_stale_non_urgent():
    now = datetime(2026, 11, 8, 12, 0, 0)
    old = (now - timedelta(hours=60)).isoformat()
    fresh = (now - timedelta(hours=1)).isoformat()
    tasks = [
        {"task_id": "old-non-urgent", "created_at": old, "urgent": False},
        {"task_id": "old-urgent", "created_at": old, "urgent": True},
        {"task_id": "fresh", "created_at": fresh},
    ]
    result = decay_backlog(tasks, now=now, decay_hours=48)
    by_id = {t["task_id"]: t for t in result}
    assert by_id["old-non-urgent"]["archived"] is True
    # Urgent and fresh tasks have no `archived` flag at all.
    assert by_id["old-urgent"].get("archived") is not True
    assert by_id["fresh"].get("archived") is not True


# --- Point 30: Multi-day milestone tracking ---------------------------------
def test_is_multi_day_milestone():
    assert is_multi_day_milestone({"multi_day": True}) is True
    assert is_multi_day_milestone({"milestone": True}) is True
    assert is_multi_day_milestone({"title": "Normal task"}) is False
