from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from models import (
    BriefingContext,
    DailyPlan,
    ExecutionTarget,
    GateVerdict,
    PlannedTaskItem,
)
from .opportunity import scan_opportunities
from .optimizer import get_calibrated_task_duration

logger = logging.getLogger(__name__)

__all__ = [
    "build_plan",
    "route_task_execution_target",
    "check_dependency_deadlock",
    "decompose_epic_task",
    "calculate_context_switch_penalty",
    "format_today_plan_markdown",
    "trigger_interactive_replan",
]


# Subsystem 3, Point 25: Default duration (minutes) by keyword.
_KEYWORD_DURATIONS: dict[str, int] = {
    "research": 30,
    "scrape": 45,
    "scraping": 45,
    "triage": 20,
    "review": 20,
    "email": 15,
    "meeting": 30,
    "write": 40,
    "refactor": 60,
}
# Subsystem 3, Point 26: Peak and low energy windows (0-based hour).
_PEAK_ENERGY_START = 9
_PEAK_ENERGY_END = 13
# Subsystem 3, Point 29: Backlog decay threshold.
_BACKLOG_DECAY_HOURS = 48


def default_duration_for_task(title: str, description: str = "") -> int:
    """Subsystem 3, Point 25: Default duration when no estimate is provided.

    Scans the text for keywords and returns the first matching duration,
    falling back to 30 minutes.
    """
    text = f"{title} {description}".lower()
    for keyword, duration in _KEYWORD_DURATIONS.items():
        if keyword in text:
            return duration
    return 30


def match_energy_to_task(
    title: str,
    description: str = "",
    current_hour: int = 10,
    peak_start: int = _PEAK_ENERGY_START,
    peak_end: int = _PEAK_ENERGY_END,
) -> str:
    """Subsystem 3, Point 26: Energy-task matching.

    Returns ``"peak"`` for analytical/coding work during peak hours and
    ``"low"`` for administrative work, ``"neutral"`` otherwise.
    """
    text = f"{title} {description}".lower()
    analytical = ("code", "coding", "refactor", "design", "analysis", "research", "debug")
    admin = ("email", "triage", "inbox", "schedule", "review", "admin", "routine")
    is_peak = peak_start <= current_hour < peak_end
    if any(k in text for k in analytical):
        return "peak" if is_peak else "low"
    if any(k in text for k in admin):
        return "low"
    return "neutral"


def preempt_for_p0(
    current_task_id: str | None,
    incoming_priority: int,
    running_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Subsystem 3, Point 27: Emergency task preemption for P0 tasks.

    Returns an action dict: ``preempt`` when an active task should be
    paused to make room for the P0, ``schedule`` otherwise.
    """
    if incoming_priority == 0 and current_task_id:
        return {
            "action": "preempt",
            "paused_task_id": current_task_id,
            "reason": "P0 emergency task injected",
        }
    return {"action": "schedule"}


def decay_backlog(
    tasks: list[dict[str, Any]],
    now: datetime | None = None,
    decay_hours: int = _BACKLOG_DECAY_HOURS,
) -> list[dict[str, Any]]:
    """Subsystem 3, Point 29: Archive backlog items older than `decay_hours` without urgency.

    Adds ``archived=True`` and an ``archive_reason`` to stale items; returns
    the full list with those flags set.
    """
    ref = now or datetime.utcnow()
    cutoff = ref - timedelta(hours=decay_hours)
    out: list[dict[str, Any]] = []
    for task in tasks:
        item = dict(task)
        created = task.get("created_at")
        urgent = bool(task.get("urgent") or task.get("priority", 99) <= 1)
        try:
            ts = datetime.fromisoformat(created) if created else ref
        except Exception:
            ts = ref
        if (not urgent) and ts < cutoff:
            item["archived"] = True
            item["archive_reason"] = f"stale > {decay_hours}h"
        out.append(item)
    return out


def is_multi_day_milestone(task: dict[str, Any]) -> bool:
    """Subsystem 3, Point 30: Return True when a task spans multiple days.

    A milestone must not be counted as a failure on day 1 of its lifecycle.
    """
    return bool(task.get("multi_day") or task.get("milestone"))


def check_dependency_deadlock(dependencies: dict[str, list[str]]) -> bool:
    """Subsystem 3, Point 22: Detect cyclic task dependencies using DFS graph cycle search."""
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in dependencies.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(node)
        return False

    for node in dependencies:
        if node not in visited:
            if dfs(node):
                return True
    return False


def decompose_epic_task(task_title: str, estimated_min: int, max_subtask_min: int = 60) -> list[dict[str, Any]]:
    """Subsystem 3, Point 24: Decompose large tasks (> 120m) into manageable subtasks <= max_subtask_min."""
    if estimated_min <= 120:
        return [{"title": task_title, "estimated_duration_min": estimated_min}]

    count = (estimated_min + max_subtask_min - 1) // max_subtask_min
    subtasks: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        dur = min(max_subtask_min, estimated_min - (i - 1) * max_subtask_min)
        subtasks.append({
            "title": f"{task_title} (Part {i}/{count})",
            "estimated_duration_min": dur,
        })
    return subtasks


def calculate_context_switch_penalty(prev_target: ExecutionTarget | None, curr_target: ExecutionTarget) -> int:
    """Subsystem 3, Point 23: Return 10m buffer penalty when switching target execution contexts."""
    if prev_target is not None and prev_target != curr_target:
        return 10
    return 0


def route_task_execution_target(title: str, description: str = "") -> tuple[ExecutionTarget, str | None]:
    """Inspect task content and determine the appropriate connecting dot plugin adapter."""
    text = (f"{title} {description}").lower()

    if any(k in text for k in ("scrape", "browse", "portal", "cloud", "download", "web automation")):
        return ExecutionTarget.CAMOUFOX_SCENARIO, "multi_portal_pipeline"
    elif any(k in text for k in ("email", "newsletter", "inbox", "gmail")):
        return ExecutionTarget.GMAIL_TRIAGE, None
    elif any(k in text for k in ("threads", "twitter", "post", "social", "tebar", "publish")):
        return ExecutionTarget.TEBAR_PUBLISH, None

    return ExecutionTarget.KANBAN_NATIVE, None


async def build_plan(
    briefing_context: BriefingContext,
    profile_id: str,
    plan_date: str,
    kanban_adapter: Any = None,
    calendar_adapter: Any = None,
    available_focus_hours: float = 6.0,
    db_path: Any = None,
) -> DailyPlan:
    """Build adaptive daily agenda matching Brain cognitive capacity against pending backlog."""
    brain = briefing_context.brain_summary
    tasks: list[PlannedTaskItem] = []

    # 1. Respect Brain VETO (e.g. Quiet Hours or Recovery State)
    if brain.verdict == GateVerdict.VETO:
        logger.warning("Planning halted due to Brain VETO: %s", brain.reasons)
        return DailyPlan(
            plan_date=plan_date,
            profile_id=profile_id,
            tasks=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

    # 2. Determine capacity ceiling based on fatigue and weekend mode (Point 28)
    max_tasks = 2 if (brain.verdict == GateVerdict.CAUTION or brain.fatigue >= 0.70) else 6
    try:
        dt = datetime.strptime(plan_date, "%Y-%m-%d")
        if dt.weekday() >= 5:  # Saturday or Sunday
            max_tasks = min(max_tasks, 2)
    except Exception:
        pass

    allocated_minutes = 0
    max_minutes = int(available_focus_hours * 60)
    prev_target: ExecutionTarget | None = None

    # 3. Add base alignment task
    alignment_task = PlannedTaskItem(
        task_id=f"plan_{plan_date}_1",
        title="Morning Alignment & Daily Briefing Review",
        priority=1,
        execution_target=ExecutionTarget.KANBAN_NATIVE,
        estimated_duration_min=20,
        completion_contract={
            "outcome": "Briefing items reviewed and agenda approved",
            "verification": "Daily plan committed to SQLite",
            "constraints": "Keep within morning focus window",
        },
    )
    tasks.append(alignment_task)
    allocated_minutes += 20
    prev_target = ExecutionTarget.KANBAN_NATIVE

    # 4. Check for Proactive Opportunity & Workflow Automations
    try:
        opps = scan_opportunities(profile_id=profile_id, db_path=db_path)
        for opp in opps[:1]:
            if len(tasks) < max_tasks and (allocated_minutes + 45) <= max_minutes:
                # Subsystem 9, Point 90: Automatically scaffold the SKILL.md draft on disk
                try:
                    from .opportunity import scaffold_skill_for_opportunity
                    scaffold_skill_for_opportunity(opp)
                except Exception as s_err:
                    logger.debug("Failed auto-scaffolding skill: %s", s_err)

                tasks.append(
                    PlannedTaskItem(
                        task_id=f"plan_{plan_date}_opp",
                        title=f"Automate Repetitive Workflow: {opp['activity_type']}",
                        priority=2,
                        execution_target=ExecutionTarget.KANBAN_NATIVE,
                        estimated_duration_min=45,
                        completion_contract={
                            "outcome": f"Script created to automate {opp['activity_type']}",
                            "verification": "pytest pass on automation script",
                            "constraints": "Do not break existing manual triggers",
                        },
                    )
                )
                allocated_minutes += 45
    except Exception as exc:
        logger.debug("Failed scanning opportunities: %s", exc)

    # 5. Route pending tasks from briefing backlog
    for idx, raw_task in enumerate(briefing_context.pending_tasks, start=2):
        if len(tasks) >= max_tasks:
            break

        title = raw_task.get("title", f"Task {idx}")
        raw_est = raw_task.get("estimated_duration_min", 30)

        # Subsystem 3, Point 24: Decompose epic tasks if > 120m
        subtasks = decompose_epic_task(title, raw_est, max_subtask_min=60)

        for sub_idx, sub in enumerate(subtasks, start=1):
            if len(tasks) >= max_tasks:
                break

            sub_title = sub["title"]
            target, scenario = route_task_execution_target(sub_title)
            raw_est_min = sub["estimated_duration_min"]

            # Subsystem 3, Point 23: Context-switch penalty
            penalty = calculate_context_switch_penalty(prev_target, target)
            est_min = get_calibrated_task_duration(
                activity_type=sub_title,
                raw_estimate_min=raw_est_min,
                profile_id=profile_id,
                db_path=db_path,
            ) + penalty

            # Subsystem 3, Point 21: Buffer time (+15m)
            buffer_min = 15

            if allocated_minutes + est_min + buffer_min > max_minutes:
                logger.info("Task '%s' exceeds available focus budget. Delegating to shared board.", sub_title)
                target_board = "shared"
            else:
                target_board = profile_id

            item = PlannedTaskItem(
                task_id=f"plan_{plan_date}_{idx}_{sub_idx}",
                title=sub_title,
                priority=raw_task.get("priority", 2),
                execution_target=target,
                target_scenario=scenario,
                estimated_duration_min=est_min,
                completion_contract={
                    "outcome": f"Complete {sub_title}",
                    "verification": "Worker reports success status",
                    "boundaries": f"Board: {target_board}",
                },
            )
            tasks.append(item)
            allocated_minutes += (est_min + buffer_min)
            prev_target = target

            if kanban_adapter and hasattr(kanban_adapter, "create_task"):
                try:
                    await kanban_adapter.create_task(
                        title=sub_title,
                        assignee=profile_id,
                        board_id=target_board,
                        goal=True,
                        completion_contract=item.completion_contract,
                    )
                except Exception as exc:
                    logger.warning("Error dispatching task '%s' to kanban: %s", sub_title, exc)

    return DailyPlan(
        plan_date=plan_date,
        profile_id=profile_id,
        tasks=tasks,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )


def format_today_plan_markdown(profile_id: str | None = None) -> str:
    """Helper to format current plan as human readable markdown."""
    from .lifecycle import format_daily_status_summary
    return format_daily_status_summary(profile_id=profile_id)


def trigger_interactive_replan(profile_id: str | None = None) -> str:
    """Trigger manual replan."""
    return "Replanning requested. Checking Brain fatigue and active calendar allocations."
