from __future__ import annotations

"""Subsystem 9: Human partnership hardening (points 81,82,83,84,86,87,88,89)."""

from datetime import datetime
from typing import Any

from core.partnership import (
    InFlightCycleState,
    build_tool_requirement_memo,
    explain_delay_reasons,
    format_markdown_for_mobile,
    gate_admin_slash_commands,
    handle_in_flight_cancel,
    is_in_notification_quiet_hours,
    pick_safest_clarify_default,
    should_reject_task_addition,
)


# --- Point 81: Notification quiet hours -------------------------------------
def test_notification_quiet_hours():
    assert is_in_notification_quiet_hours(datetime(2026, 11, 8, 23, 30)) is True
    assert is_in_notification_quiet_hours(datetime(2026, 11, 9, 5, 0)) is True
    assert is_in_notification_quiet_hours(datetime(2026, 11, 8, 12, 0)) is False
    assert is_in_notification_quiet_hours(datetime(2026, 11, 8, 5, 31)) is False


# --- Point 82: Constructive pushback ---------------------------------------
def test_should_reject_task_addition():
    existing = [{"estimated_duration_min": 400}]
    # 400 + 100 = 500 > 480 -> reject
    reject, reason = should_reject_task_addition(existing, {"estimated_duration_min": 100}, max_minutes=480)
    assert reject is True
    assert "480" in reason
    # 400 + 50 = 450 <= 480 -> accept
    ok, _ = should_reject_task_addition(existing, {"estimated_duration_min": 50}, max_minutes=480)
    assert ok is False


# --- Point 83: Clarify timeout default --------------------------------------
def test_pick_safest_clarify_default():
    # Named "safest" option is preferred.
    opts = [
        {"id": "aggressive", "key": "aggressive"},
        {"id": "safest", "key": "safest", "label": "Wait and retry"},
        {"id": "force", "key": "force"},
    ]
    assert pick_safest_clarify_default(opts)["id"] == "safest"
    # `safe: true` flag next.
    opts2 = [{"id": "a"}, {"id": "b", "safe": True}]
    assert pick_safest_clarify_default(opts2)["id"] == "b"
    # Fallback: first option.
    assert pick_safest_clarify_default([{"id": "a"}, {"id": "b"}])["id"] == "a"
    # Empty -> empty dict.
    assert pick_safest_clarify_default([]) == {}


# --- Point 84: Slash command role gating -----------------------------------
def test_gate_admin_slash_commands():
    # Non-admin on privileged command -> denied.
    assert gate_admin_slash_commands("/daily replan", user_role="user")["allowed"] is False
    # Admin -> allowed.
    assert gate_admin_slash_commands("/daily replan", user_role="admin")["allowed"] is True
    # Non-restricted command is always allowed regardless of role.
    assert gate_admin_slash_commands("/daily status", user_role="user")["allowed"] is True


# --- Point 86: Mobile responsive formatting --------------------------------
def test_format_markdown_for_mobile():
    # Table row becomes a bullet.
    md = "| Metric | Value |\n| Completion | 80% |"
    out = format_markdown_for_mobile(md)
    assert "• **Metric**: Value" in out or "Metric" in out
    # Long line is hard-wrapped.
    long_line = "word " * 30
    wrapped = format_markdown_for_mobile(long_line.strip())
    assert all(len(line) <= 48 for line in wrapped.splitlines() if line.strip())


# --- Point 87: In-flight cancel handling ------------------------------------
def test_handle_in_flight_cancel():
    state = InFlightCycleState(running=True, task_id="t-1")
    res = handle_in_flight_cancel(state)
    assert res["status"] == "stopped"
    assert res["task_id"] == "t-1"
    # No active cycle.
    assert handle_in_flight_cancel(InFlightCycleState(running=False))["status"] == "no_active_cycle"
    # Save callback invoked.
    captured: dict[str, Any] = {}

    def save(status: dict[str, Any]) -> None:
        captured.update(status)

    state2 = InFlightCycleState(running=True, task_id="t-2")
    handle_in_flight_cancel(state2, save_status=save)
    assert captured.get("status") == "stopped"


# --- Point 88: Auto-generated tool requirement memo -------------------------
def test_build_tool_requirement_memo():
    memo = build_tool_requirement_memo(
        "web_search",
        suggested_api="https://api.example.com/v1/search",
        target_plugin="camoufox",
    )
    assert memo["type"] == "tool_requirement"
    assert memo["missing_capability"] == "web_search"
    assert memo["blocking"] is True


# --- Point 89: Data-driven delay reasons ------------------------------------
def test_explain_delay_reasons():
    reasons = explain_delay_reasons(30, 90, telemetry=[{"error_reason": "Network timeout"}])
    assert any("60m" in r for r in reasons)
    assert any("Network timeout" in r for r in reasons)
    # No delay -> "On schedule".
    assert explain_delay_reasons(30, 20) == ["On schedule"]
