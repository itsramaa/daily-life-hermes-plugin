from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import get_settings
from db import get_daily_plan, init_db, save_daily_plan
from exceptions import DecisionGateVetoError, LockAcquisitionError
from models import GateVerdict

logger = logging.getLogger(__name__)

__all__ = [
    "AtomicFileLock",
    "make_idempotency_key",
    "is_cycle_completed",
    "run_daily_cycle",
    "run_startup_catchup",
    "format_daily_status_summary",
]


class AtomicFileLock:
    """Cross-platform advisory file lock using msvcrt (Windows) or fcntl (POSIX)."""

    def __init__(self, lock_path: Path, timeout_sec: float = 10.0, retry_interval: float = 0.2, stale_timeout_sec: float = 600.0) -> None:
        self.lock_path = Path(lock_path)
        self.timeout_sec = timeout_sec
        self.retry_interval = retry_interval
        self.stale_timeout_sec = stale_timeout_sec
        self._fd: Any = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Subsystem 10, Point 98: Check for stale locks (> 10m old) from crashed processes
        if self.lock_path.is_file():
            try:
                mtime = self.lock_path.stat().st_mtime
                if time.time() - mtime > self.stale_timeout_sec:
                    logger.warning("Stale lock file detected (>%ss old). Auto-releasing: %s", self.stale_timeout_sec, self.lock_path)
                    self.lock_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("Failed probing stale lock: %s", exc)

        start_time = time.time()

        while True:
            try:
                # Open for read/write, create if not exist
                self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                if sys.platform == "win32":
                    import msvcrt
                    # Lock first byte non-blocking
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (OSError, IOError):
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except Exception:
                        pass
                    self._fd = None

                if time.time() - start_time >= self.timeout_sec:
                    raise LockAcquisitionError(
                        f"Timed out after {self.timeout_sec}s waiting for lock: {self.lock_path}"
                    )
                time.sleep(self.retry_interval)

    def release(self) -> None:
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception as exc:
                logger.debug("Error unlocking fd: %s", exc)
            finally:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None

    def __enter__(self) -> AtomicFileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def make_idempotency_key(profile_id: str, date_str: str, workflow_type: str = "daily_cycle") -> str:
    """Return deterministic idempotency key."""
    return f"{profile_id}:{date_str}:{workflow_type}"


def is_cycle_completed(profile_id: str, date_str: str, db_path: Path | None = None) -> bool:
    """Check if a plan has already been committed for this date and profile."""
    plan = get_daily_plan(date_str, profile_id, db_path=db_path)
    return plan is not None


async def run_daily_cycle(
    profile_id: str | None = None,
    date_str: str | None = None,
    trigger_source: str = "cron",
    force: bool = False,
    now_seam: str | None = None,
) -> dict[str, Any]:
    """Execute the core daily life cycle idempotently."""
    settings = get_settings()
    pid = profile_id or settings.profile_id
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")

    init_db(settings.db_path)

    # Subsystem 10, Point 93: Pre-flight disk space health guard
    from config import check_disk_space_healthy
    if not check_disk_space_healthy(min_mb=50, path=settings.profile_path):
        logger.warning("Low disk space (<50MB). Skipping heavy cycle execution.")
        return {"status": "low_disk_space", "profile_id": pid, "silent": True, "message": "[SILENT]"}

    if not force and is_cycle_completed(pid, today, db_path=settings.db_path):
        logger.info("Daily cycle already completed for %s on %s. Emitting [SILENT].", pid, today)
        return {
            "status": "already_completed",
            "profile_id": pid,
            "plan_date": today,
            "silent": True,
            "message": "[SILENT]",
        }

    lock_file = settings.lock_file_path
    with AtomicFileLock(lock_file, timeout_sec=settings.lock_timeout_sec):
        # Double check after acquiring lock
        if not force and is_cycle_completed(pid, today, db_path=settings.db_path):
            return {
                "status": "already_completed",
                "profile_id": pid,
                "plan_date": today,
                "silent": True,
                "message": "[SILENT]",
            }

        # 1. Cognitive Assessment via BrainAdapter
        from .adapters.brain import BrainAdapter
        from .adapters.calendar import CalendarAdapter
        from .adapters.kanban import KanbanAdapter
        from .adapters.gmail import GmailAdapter
        from .adapters.honcho import HonchoAdapter

        brain = BrainAdapter(default_tz=settings.timezone)
        calendar = CalendarAdapter()
        kanban = KanbanAdapter()
        gmail = GmailAdapter()
        honcho = HonchoAdapter()

        brain_summary = await brain.evaluate_decision("daily_planning", now=now_seam)

        if brain_summary.verdict == GateVerdict.VETO:
            logger.warning("Daily planning vetoed by Biomimetic Brain: %s. Emitting [SILENT].", brain_summary.reasons)
            return {
                "status": "vetoed",
                "profile_id": pid,
                "plan_date": today,
                "brain_summary": brain_summary.model_dump(mode="json"),
                "silent": True,
                "message": "[SILENT]",
            }

        # 2. Resilient Briefing
        from .briefing import gather_briefing
        briefing_context = await gather_briefing(
            brain_summary=brain_summary,
            gmail_adapter=gmail,
            calendar_adapter=calendar,
            kanban_adapter=kanban,
        )
        from db import save_briefing_snapshot
        save_briefing_snapshot(today, pid, briefing_context.model_dump(mode="json"), db_path=settings.db_path)

        # 3. Planning & Capacity Matching
        from .planner import build_plan
        plan = await build_plan(
            briefing_context=briefing_context,
            profile_id=pid,
            plan_date=today,
            kanban_adapter=kanban,
            calendar_adapter=calendar,
            db_path=settings.db_path,
        )

        # 4. Commit Plan
        save_daily_plan(today, pid, plan.model_dump(mode="json"), db_path=settings.db_path)
        logger.info("Daily plan committed successfully for %s on %s with %d tasks.", pid, today, len(plan.tasks))

        return {
            "status": "completed",
            "profile_id": pid,
            "plan_date": today,
            "tasks_count": len(plan.tasks),
            "silent": False,
            "plan": plan.model_dump(mode="json"),
        }


def run_startup_catchup() -> dict[str, Any]:
    """Background startup catch-up handler triggered by gateway:startup hook."""
    settings = get_settings()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # If already completed today, exit immediately with silent marker
    if is_cycle_completed(settings.profile_id, today, db_path=settings.db_path):
        return {"status": "up_to_date", "silent": True}

    # Subsystem 1, Point 2: Decay fatigue during startup if idle
    try:
        from .adapters.brain import BrainAdapter
        brain = BrainAdapter(default_tz=settings.timezone)
        brain.apply_elapsed_decay(hours_elapsed=6.0)
    except Exception as exc:
        logger.debug("Failed applying startup brain decay: %s", exc)

    # If before scheduled planning hour, wait for cron
    if now.hour < settings.planning_hour:
        logger.info("Gateway startup before planning hour (%d:00). Waiting for scheduled cron.", settings.planning_hour)
        return {"status": "pending_schedule", "silent": True}

    logger.info("Gateway started after planning hour (%d:00). Initiating catch-up cycle.", settings.planning_hour)
    import asyncio
    try:
        return asyncio.run(run_daily_cycle(profile_id=settings.profile_id, date_str=today, trigger_source="startup_catchup"))
    except Exception as exc:
        logger.error("Startup catch-up cycle encountered error: %s", exc)
        return {"status": "error", "error": str(exc), "silent": True}


def format_daily_status_summary(profile_id: str | None = None, date_str: str | None = None) -> str:
    """Format markdown summary for /daily status or dashboard."""
    settings = get_settings()
    pid = profile_id or settings.profile_id
    today = date_str or datetime.utcnow().strftime("%Y-%m-%d")

    plan = get_daily_plan(today, pid, db_path=settings.db_path)
    if not plan:
        return f"### Daily Life Status ({today})\n*No plan committed yet for profile `{pid}`.*"

    tasks = plan.get("tasks", [])
    task_lines = "\n".join(
        f"- [{'x' if t.get('completed') else ' '}] **{t.get('title')}** (est: {t.get('estimated_duration_min')}m, target: `{t.get('execution_target')}`)"
        for t in tasks
    )
    return (
        f"### Daily Life Status ({today})\n"
        f"**Profile:** `{pid}` | **Tasks Scheduled:** {len(tasks)}\n\n"
        f"#### Agenda Focus:\n{task_lines or '*No active tasks.*'}"
    )
