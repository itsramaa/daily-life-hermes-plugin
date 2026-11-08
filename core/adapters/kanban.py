from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from .base import PluginAdapter

logger = logging.getLogger(__name__)

__all__ = ["KanbanAdapter"]

# Subsystem 7, Point 67: goal-mode iteration budget.
_GOAL_ITERATION_BUDGET = 100
# Subsystem 7, Point 63: support cards that may never be linked as children.
_BLOCKED_STATUS = "blocked"
# Subsystem 7, Point 69: evidence markers that prove real completion.
_COMPLETION_EVIDENCE_KEYS = ("result_summary", "artifact_path", "exit_code", "verification")

# Subsystem 7, Point 62: type-name lookup table used by validate_handoff_contract.
_TYPE_NAMES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "bytes": bytes,
}


def validate_handoff_contract(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    """Subsystem 7, Point 62: Validate cross-profile handoff JSON contract.

    Uses a minimal JSON-schema-like structure: `schema` is a dict of
    `{key: expected_type_name}` where type names are Python type names
    (``"str"``, ``"int"``, ``"dict"``, ``"list"``). Missing required keys
    or wrong types are reported.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return False, ["payload is not a dict"]
    if not schema:
        return True, []
    for key, expected in schema.items():
        if key not in payload:
            errors.append(f"missing required key: {key}")
            continue
        expected_type = _TYPE_NAMES.get(expected)
        if expected_type is None:
            continue
        if not isinstance(payload[key], expected_type):
            errors.append(f"key '{key}' expected {expected}, got {type(payload[key]).__name__}")
    return (not errors), errors


def _default_liveness_probe() -> Any:
    """Return a platform-specific pid liveness callable, or None on Windows."""
    if os.name == "nt":
        return None  # Windows: os.kill semantics differ; callers must pass probe.
    import signal as _sig

    def _probe(pid: int) -> bool:
        try:
            os.kill(int(pid), _sig.SIGCONT)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists, just no permission
        except ValueError:
            return False

    return _probe


class KanbanAdapter(PluginAdapter):
    """Adapter for Hermes Kanban Board (Hybrid: Private Board & Shared Board) with artifact passing."""

    name: str = "hermes-kanban"

    def __init__(self, db_client: Any = None) -> None:
        self._db_client = db_client

    async def is_available(self) -> bool:
        """Hermes Kanban is always available as core infrastructure."""
        return True

    async def get_active_tasks(
        self,
        board_id: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch active/ready tasks assigned to this profile."""
        if self._db_client and hasattr(self._db_client, "list_tasks"):
            try:
                return await self._db_client.list_tasks(board=board_id, assignee=assignee)
            except Exception as exc:
                logger.warning("Error listing kanban tasks: %s", exc)

        return [
            {
                "task_id": "kanban_init_1",
                "title": "Review morning inbox & priority messages",
                "status": "ready",
                "assignee": assignee or "default",
                "board": board_id or "default",
            }
        ]

    async def create_task(
        self,
        title: str,
        body: str = "",
        assignee: str | None = None,
        board_id: str = "default",
        goal: bool = True,
        completion_contract: dict[str, Any] | None = None,
        originated_by: str | None = None,
    ) -> dict[str, Any]:
        """Create durable task on Kanban with mode --goal and completion_contract."""
        task_data = {
            "title": title,
            "body": body,
            "assignee": assignee,
            "board": board_id,
            "goal": goal,
            "completion_contract": completion_contract or {},
            "status": "ready",
            "originated_by": originated_by or assignee or "default",
        }

        if self._db_client and hasattr(self._db_client, "create_task"):
            try:
                return await self._db_client.create_task(**task_data)
            except Exception as exc:
                logger.error("Failed creating kanban task: %s", exc)

        return task_data

    async def update_task_status(
        self,
        task_id: str,
        new_status: str,
        result_summary: str | None = None,
    ) -> dict[str, Any]:
        """Subsystem 7, Point 68: Update card state (ready, running, review, done, blocked)."""
        update = {
            "task_id": task_id,
            "status": new_status,
            "result_summary": result_summary,
        }
        if self._db_client and hasattr(self._db_client, "update_task"):
            try:
                return await self._db_client.update_task(**update)
            except Exception as exc:
                logger.warning("Failed updating kanban task %s: %s", task_id, exc)

        return update

    async def add_comment(
        self,
        task_id: str,
        author: str,
        comment: str,
    ) -> dict[str, Any]:
        """Subsystem 7, Point 66: Append handoff note or status progress comment to task card."""
        comment_entry = {
            "task_id": task_id,
            "author": author,
            "comment": comment,
        }
        if self._db_client and hasattr(self._db_client, "add_comment"):
            try:
                return await self._db_client.add_comment(**comment_entry)
            except Exception as exc:
                logger.warning("Failed appending comment to kanban card %s: %s", task_id, exc)

        return comment_entry

    async def attach_artifact(
        self,
        task_id: str,
        file_path: Path | str,
        board_slug: str = "default",
    ) -> dict[str, Any]:
        """Subsystem 7, Point 64: Stage output artifact file into Kanban attachments directory."""
        source = Path(file_path)
        if not source.is_file():
            return {"status": "error", "error": f"File {file_path} not found"}

        hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        if board_slug == "default":
            attach_dir = hermes_home / "kanban" / "attachments" / task_id
        else:
            attach_dir = hermes_home / "kanban" / "boards" / board_slug / "attachments" / task_id

        attach_dir.mkdir(parents=True, exist_ok=True)
        dest = attach_dir / source.name

        try:
            shutil.copy2(str(source), str(dest))
            return {
                "status": "attached",
                "task_id": task_id,
                "file_name": source.name,
                "path": str(dest),
            }
        except Exception as exc:
            logger.error("Failed copying artifact to kanban attachments: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def get_delegated_tasks(
        self,
        originated_by: str,
        board_id: str = "shared",
    ) -> list[dict[str, Any]]:
        """Subsystem 7, Point 70: Track tasks delegated by this profile to other agents on the shared board."""
        tasks = await self.get_active_tasks(board_id=board_id)
        return [
            t for t in tasks
            if t.get("originated_by") == originated_by and t.get("assignee") != originated_by
        ]

    # ------------------------------------------------------------------
    # Subsystem 7 — additional hardening points (61, 63, 65, 67, 69)
    # ------------------------------------------------------------------
    async def reclaim_orphaned_workers(
        self,
        board_id: str | None = None,
        is_alive: Any = None,
    ) -> list[str]:
        """Subsystem 7, Point 61: Find `running` tasks whose worker process died.

        Returns the task_ids that were returned to `ready`. `is_alive` is an
        optional `Callable[[int], bool]` liveness probe; when omitted the
        platform default is used (POSIX only — Windows returns nothing).
        """
        tasks = await self.get_active_tasks(board_id=board_id)
        probe = is_alive or _default_liveness_probe()
        if probe is None:
            return []

        reclaimed: list[str] = []
        for task in tasks:
            if task.get("status") != "running":
                continue
            pid = task.get("worker_pid")
            if pid is None:
                continue
            try:
                alive = probe(int(pid))
            except Exception as exc:
                logger.debug("Orphan liveness probe for pid=%s failed: %s", pid, exc)
                continue
            if not alive:
                await self.update_task_status(task.get("task_id"), "ready")
                reclaimed.append(task.get("task_id"))
        return reclaimed

    async def can_link_support_card(
        self,
        support_task: dict[str, Any],
        blocked_tasks: list[dict[str, Any]],
    ) -> bool:
        """Subsystem 7, Point 63: Guard against linking a support card to a blocked parent.

        Refuses the link when the target parent card is in `blocked` status.
        """
        support_id = support_task.get("task_id")
        for parent in blocked_tasks:
            if parent.get("task_id") == support_id and parent.get("status") == _BLOCKED_STATUS:
                return False
        return True

    def resolve_parked_profile_fallback(
        self,
        target_profile: str,
        parked_statuses: set[str] | None = None,
        fallback_profile: str = "default",
    ) -> str:
        """Subsystem 7, Point 65: Reroute a task when the target profile is parked.

        Returns the effective profile name to target. `parked_statuses` may
        be passed from a prior `list_profiles()` call or gateway state.
        """
        statuses = parked_statuses or set()
        if target_profile in statuses:
            return fallback_profile
        return target_profile

    def check_iteration_budget(
        self,
        current_iteration: int,
        budget: int = _GOAL_ITERATION_BUDGET,
        warn_threshold: float = 0.90,
    ) -> dict[str, Any]:
        """Subsystem 7, Point 67: Graceful handling when a worker hits its budget.

        Returns an advisory dict: `status` is `ok` below the warn threshold,
        `warning` inside the warn band, and `graceful_stop` at/over budget.
        """
        if budget <= 0:
            return {"status": "graceful_stop", "reason": "budget_exhausted", "current": current_iteration}
        ratio = current_iteration / budget
        if current_iteration >= budget:
            return {"status": "graceful_stop", "reason": "budget_exhausted", "current": current_iteration}
        if ratio >= warn_threshold:
            return {"status": "warning", "current": current_iteration, "budget": budget}
        return {"status": "ok", "current": current_iteration, "budget": budget}

    def verify_completion_evidence(
        self,
        task: dict[str, Any],
    ) -> tuple[bool, str]:
        """Subsystem 7, Point 69: Anti-fake-completion gate.

        Refuses to mark a task done without observable evidence: a result
        summary, an attached artifact path, an exit code, or an explicit
        verification field.
        """
        if task.get("status") not in ("done", "review"):
            return False, "task is not in done/review"
        for key in _COMPLETION_EVIDENCE_KEYS:
            value = task.get(key)
            if value in (None, "", {}, []):
                continue
            return True, key
        return False, "no completion evidence attached"
