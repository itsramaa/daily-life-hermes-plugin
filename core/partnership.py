from __future__ import annotations

"""Subsystem 9: Human partnership & notification triage hardening.

Points 81, 82, 83, 84, 86, 87, 88, 89 live in this single module so the
review, review-command, and hook layers all share one code path.
"""

import json
import logging
import re
from datetime import datetime, time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "is_in_notification_quiet_hours",
    "should_reject_task_addition",
    "pick_safest_clarify_default",
    "gate_admin_slash_commands",
    "format_markdown_for_mobile",
    "handle_in_flight_cancel",
    "build_tool_requirement_memo",
    "explain_delay_reasons",
]

# Subsystem 9, Point 81: quiet-hours window for chat notifications.
_QUIET_START = time(23, 0, 0)
_QUIET_END = time(5, 30, 0)
# Subsystem 9, Point 83: default timeout before auto-picking the safest option.
_CLARIFY_TIMEOUT_MINUTES = 120


# ---------------------------------------------------------------------------
# Point 81 — Notification quiet hours
# ---------------------------------------------------------------------------
def is_in_notification_quiet_hours(now: datetime | None = None) -> bool:
    """Subsystem 9, Point 81: Hold non-urgent notifications during 23:00-05:30."""
    ts = now or datetime.now()
    t = ts.time()
    # 23:00 → 23:59:59 falls in quiet window.
    return t >= _QUIET_START or t < _QUIET_END


# ---------------------------------------------------------------------------
# Point 82 — Constructive pushback on capacity overflow
# ---------------------------------------------------------------------------
def should_reject_task_addition(
    existing_tasks: list[dict[str, Any]],
    new_task: dict[str, Any],
    max_minutes: int = 480,
) -> tuple[bool, str]:
    """Subsystem 9, Point 82: Agent refuses new work when the schedule is full.

    Returns ``(reject, reason)``; ``reason`` is empty when accepted.
    """
    current_total = sum(int(t.get("estimated_duration_min", 0)) for t in existing_tasks)
    new_total = int(new_task.get("estimated_duration_min", 0))
    if current_total + new_total > max_minutes:
        return True, f"Capacity full ({current_total}m + {new_total}m > {max_minutes}m)"
    return False, ""


# ---------------------------------------------------------------------------
# Point 83 — Clarify timeout default
# ---------------------------------------------------------------------------
def pick_safest_clarify_default(options: list[dict[str, Any]]) -> dict[str, Any]:
    """Subsystem 9, Point 83: When `clarify` times out, pick the safest option.

    Preference order:
    1. option whose key is literally ``"safest"``
    2. option with ``"safe": true``
    3. first option (deterministic fallback)
    """
    if not options:
        return {}
    for opt in options:
        if opt.get("id") == "safest" or opt.get("key") == "safest":
            return opt
    for opt in options:
        if opt.get("safe") is True:
            return opt
    return options[0]


# ---------------------------------------------------------------------------
# Point 84 — Slash command role gating
# ---------------------------------------------------------------------------
_ADMIN_ONLY_COMMANDS: frozenset[str] = frozenset({
    "/daily replan",
    "/daily trigger",
    "/daily review --force",
})


def gate_admin_slash_commands(command: str, user_role: str = "user") -> dict[str, Any]:
    """Subsystem 9, Point 84: Restrict privileged /daily sub-commands to Admin."""
    cmd = (command or "").strip()
    for restricted in _ADMIN_ONLY_COMMANDS:
        if cmd.lower().startswith(restricted.lower()):
            allowed = user_role.lower() in ("admin", "owner")
            return {
                "allowed": allowed,
                "reason": None if allowed else "admin_only",
                "restricted_command": restricted,
            }
    return {"allowed": True, "reason": None, "restricted_command": None}


# ---------------------------------------------------------------------------
# Point 86 — Mobile responsive formatting
# ---------------------------------------------------------------------------
_MOBILE_MAX_LINE = 48


def format_markdown_for_mobile(text: str, hard_wrap: bool = True) -> str:
    """Subsystem 9, Point 86: Rewrap paragraphs for Telegram/Discord mobile.

    Tables become compact bullets; long lines are wrapped at `_MOBILE_MAX_LINE`.
    """
    if not text:
        return ""

    lines: list[str] = []
    for raw_line in text.splitlines():
        # Convert simple two-column table rows into bullets.
        table_match = re.match(r"^\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", raw_line)
        if table_match and not table_match.group(1).startswith("-"):
            key, value = table_match.group(1), table_match.group(2)
            if key in ("", "-") and set(value) <= set("- "):
                continue
            lines.append(f"• **{key}**: {value}")
            continue

        if not hard_wrap:
            lines.append(raw_line)
            continue

        # Simple greedy word-wrap to avoid overflowing narrow chat surfaces.
        words = raw_line.split(" ")
        buf: list[str] = []
        current_len = 0
        for word in words:
            if not buf:
                buf.append(word)
                current_len = len(word)
                continue
            if current_len + 1 + len(word) <= _MOBILE_MAX_LINE:
                buf.append(word)
                current_len += 1 + len(word)
            else:
                lines.append(" ".join(buf))
                buf = [word]
                current_len = len(word)
        if buf:
            lines.append(" ".join(buf))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Point 87 — In-flight cancel handling
# ---------------------------------------------------------------------------
class InFlightCycleState:
    """Subsystem 9, Point 87: lightweight state used by the cancel path."""

    def __init__(self, running: bool = False, task_id: str | None = None) -> None:
        self.running = running
        self.task_id = task_id
        self.cancel_requested_at: str | None = None

    def request_cancel(self, reason: str = "user /stop") -> None:
        self.cancel_requested_at = datetime.utcnow().isoformat()
        logger.info("In-flight cycle cancel requested: %s", reason)

    def drain_and_mark_stopped(self) -> dict[str, Any]:
        if not self.running:
            return {"status": "no_active_cycle"}
        return {
            "status": "stopped",
            "task_id": self.task_id,
            "cancelled_at": self.cancel_requested_at or datetime.utcnow().isoformat(),
        }


def handle_in_flight_cancel(state: InFlightCycleState, save_status: Any = None) -> dict[str, Any]:
    """Subsystem 9, Point 87: Clean `/stop` handling for a running daily cycle."""
    if not state.running:
        return {"status": "no_active_cycle"}
    state.request_cancel()
    result = state.drain_and_mark_stopped()
    if save_status is not None:
        try:
            save_status(result)
        except Exception as exc:
            logger.debug("Failed saving cancel status: %s", exc)
    return result


# ---------------------------------------------------------------------------
# Point 88 — Auto-generated tool requirement memo
# ---------------------------------------------------------------------------
def build_tool_requirement_memo(
    missing_capability: str,
    suggested_api: str | None = None,
    target_plugin: str | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Subsystem 9, Point 88: Structured API/permission spec when capability is short."""
    return {
        "type": "tool_requirement",
        "missing_capability": missing_capability,
        "suggested_api": suggested_api,
        "target_plugin": target_plugin,
        "blocking": blocking,
        "requested_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Point 89 — Data-driven delay reasons
# ---------------------------------------------------------------------------
def explain_delay_reasons(
    estimated_min: int,
    actual_min: int,
    telemetry: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Subsystem 9, Point 89: Objective, telemetry-backed delay narrative."""
    reasons: list[str] = []
    diff = actual_min - estimated_min
    if diff <= 0:
        return ["On schedule"]

    reasons.append(f"Ran {diff}m over estimate ({actual_min}m vs {estimated_min}m).")
    if telemetry:
        for row in telemetry:
            err = row.get("error_reason")
            if err:
                reasons.append(f"Recorded error during run: {err}")
            status = row.get("status")
            if status in ("failed", "error"):
                reasons.append(f"Status flagged `{status}` in telemetry ledger.")
    return reasons
