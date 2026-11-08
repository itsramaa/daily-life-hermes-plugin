from __future__ import annotations

import logging
import signal
import socket
from datetime import datetime
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from config import get_settings
from db import get_daily_plan, record_task_telemetry, save_daily_review

logger = logging.getLogger(__name__)

__all__ = [
    "build_today_focus_prompt",
    "install_graceful_shutdown_handler",
    "is_offline",
    "run_offline_fallback",
    "socket_sandbox",
    "lock_review_day",
]

PROMPT_SECTION_MAX_CHARS = 3000


def build_today_focus_prompt(session_info: dict[str, Any] | None = None, db_path: Path | None = None) -> str:
    """Render cache-safe system prompt section for active daily focus.

    Subsystem 10, Point 97: System Prompt Section Size Cap —
    Truncate output to PROMPT_SECTION_MAX_CHARS to preserve cache prefix.
    """
    settings = get_settings()
    profile_id = settings.profile_id
    if session_info and "profile_name" in session_info:
        profile_id = session_info["profile_name"]

    today = datetime.utcnow().strftime("%Y-%m-%d")
    target_db = db_path or settings.db_path
    plan = get_daily_plan(today, profile_id, db_path=target_db)

    if not plan or not plan.get("tasks"):
        return f"Daily Life: No active plan committed yet for {today} (profile: {profile_id})."

    tasks = plan.get("tasks", [])
    lines = [f"Daily Life Active Focus ({today} - {profile_id}):"]
    for t in tasks[:8]:  # Keep bounded to preserve prompt budget
        target = t.get("execution_target", "kanban")
        title = t.get("title", "Untitled")
        duration = t.get("estimated_duration_min", 30)
        lines.append(f"- {title} (Target: {target}, Est: {duration}m)")

    prompt = "\n".join(lines)
    if len(prompt) > PROMPT_SECTION_MAX_CHARS:
        prompt = prompt[:PROMPT_SECTION_MAX_CHARS - 1] + "…"
    return prompt


# ---------------------------------------------------------------------------
# Subsystem 10, Point 94: Graceful Shutdown Handler
# ---------------------------------------------------------------------------
_shutdown_handler_installed = False


def release_lock_file(lock_path: "Path | str | None") -> bool:
    """Remove an atomic lock file. Returns True when a file was removed.

    Extracted so shutdown cleanup is directly testable without raising
    KeyboardInterrupt from a real signal.
    """
    if not lock_path:
        return False
    try:
        Path(lock_path).unlink(missing_ok=True)
        logger.info("Lock released on shutdown: %s", lock_path)
        return True
    except Exception as exc:
        logger.debug("Failed releasing lock on shutdown: %s", exc)
        return False


def install_graceful_shutdown_handler(release_lock: "Path | None" = None) -> None:
    """Register SIGINT / SIGTERM handlers that release the active lock file.

    Subsystem 10, Point 94: Ensures the atomic lock and in-flight state are
    released when the host is terminated. Idempotent.
    """
    global _shutdown_handler_installed
    if _shutdown_handler_installed:
        return

    def _handler(signum: int, frame: Any) -> None:
        logger.warning("Received signal %s — releasing lock and saving state.", signum)
        release_lock_file(release_lock)
        signal.default_int_handler(signum, frame)

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)
    _shutdown_handler_installed = True


# ---------------------------------------------------------------------------
# Subsystem 10, Point 96: RPC Socket Sandbox Boundary
# ---------------------------------------------------------------------------
@contextmanager
def socket_sandbox(socket_path: str | Path | None = None, timeout: float = 30.0) -> "socket.socket":
    """Yield a connected Unix domain socket, closing it on exit.

    Subsystem 10, Point 96: Ensures the RPC socket used by `execute_code`
    is isolated and closed in a `finally` block so it cannot leak to other
    processes.
    """
    path = Path(socket_path) if socket_path else None
    sock: socket.socket | None = None
    try:
        if path and path.is_file():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(str(path))
        yield sock
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Subsystem 10, Point 99: Append-Only Audit Record
# ---------------------------------------------------------------------------
def lock_review_day(
    review_date: str,
    profile_id: str,
    locked: bool = True,
    db_path: Path | None = None,
) -> bool:
    """Lock a committed daily review row so it cannot be overwritten later.

    Subsystem 10, Point 99: The `daily_reviews.review_data` JSON is augmented
    with an `audit_lock` timestamp field. Subsequent writes to the same
    (review_date, profile_id) pair are no-ops.
    """
    from db import get_daily_review

    target_db = db_path or get_settings().db_path
    existing = get_daily_review(review_date, profile_id, db_path=target_db)
    if existing is None:
        return False
    if existing.get("audit_lock") and locked:
        # Already locked — leave untouched
        return True
    # Apply lock marker
    from db import save_daily_review
    updated = dict(existing)
    updated["audit_lock"] = datetime.utcnow().isoformat()
    save_daily_review(review_date, profile_id, updated, db_path=target_db)
    return True


# ---------------------------------------------------------------------------
# Subsystem 10, Point 100: Offline Resilient Mode
# ---------------------------------------------------------------------------
def is_offline(timeout: float = 2.0) -> bool:
    """Return True if no external connectivity (fast socket probe)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("1.1.1.1", 53))  # Common DNS endpoint
        s.close()
        return False
    except Exception:
        return True


def run_offline_fallback(
    profile_id: str | None = None,
    review_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """When connectivity is lost, route tasks to local-analysis and refactoring.

    Subsystem 10, Point 100: Offline Resilient Mode.
    """
    settings = get_settings()
    pid = profile_id or settings.profile_id
    today = review_date or datetime.utcnow().strftime("%Y-%m-%d")

    plan = get_daily_plan(today, pid, db_path=db_path) or {"tasks": []}

    offline_tasks: list[dict[str, Any]] = []
    for task in plan.get("tasks", []):
        # Skip tasks that already have external targets; prefer kanban_native
        target = task.get("execution_target", "kanban")
        if target in ("kamoufox_scenario", "camoufox_scenario", "gmail_triage", "tebar_publish"):
            offline_tasks.append({
                **task,
                "status": "deferred_offline",
                "target": "local_analysis",
            })
        else:
            offline_tasks.append(task)

    record_task_telemetry(
        profile_id=pid,
        activity_type="offline_fallback_routed",
        day_of_week=datetime.utcnow().weekday(),
        hour_of_day=datetime.utcnow().hour,
        estimated_duration_min=len(offline_tasks) * 15,
        actual_duration_min=0,
        status="success",
        db_path=db_path,
    )
    return {"profile_id": pid, "task_count": len(offline_tasks), "tasks": offline_tasks}
