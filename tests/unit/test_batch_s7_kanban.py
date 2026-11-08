from __future__ import annotations

"""Subsystem 7: Hybrid Kanban lifecycle hardening (points 61,63,65,67,69 + contract 62)."""

from unittest.mock import AsyncMock

import pytest

from core.adapters.kanban import KanbanAdapter, validate_handoff_contract


def _adapter_with_tasks(tasks: list[dict]) -> KanbanAdapter:
    client = AsyncMock()
    client.list_tasks = AsyncMock(return_value=tasks)
    client.update_task = AsyncMock(return_value={})
    return KanbanAdapter(db_client=client)


# --- Point 62: Cross-profile contract schema --------------------------------
def test_validate_handoff_contract():
    ok, errors = validate_handoff_contract(
        {"task_id": "t1", "payload": {"a": 1}},
        schema={"task_id": "str", "payload": "dict"},
    )
    assert ok is True
    assert errors == []

    bad, errs = validate_handoff_contract(
        {"task_id": 123},
        schema={"task_id": "str", "payload": "dict"},
    )
    assert bad is False
    assert any("missing" in e for e in errs)
    assert any("expected str" in e for e in errs)


# --- Point 61: Orphaned worker reclaiming -----------------------------------
@pytest.mark.asyncio
async def test_reclaim_orphaned_workers():
    tasks = [
        {"task_id": "t1", "status": "running", "worker_pid": 999},
        {"task_id": "t2", "status": "running", "worker_pid": 1000},
        {"task_id": "t3", "status": "ready", "worker_pid": 1001},
    ]
    adapter = _adapter_with_tasks(tasks)
    # Simulate: pid 999 is dead, 1000 alive.
    reclaimed = await adapter.reclaim_orphaned_workers(
        board_id="default",
        is_alive=lambda pid: pid != 999,
    )
    assert reclaimed == ["t1"]
    # t2 still running -> not reclaimed
    assert "t2" not in reclaimed


# --- Point 63: Support card deadlock guard ----------------------------------
@pytest.mark.asyncio
async def test_support_card_deadlock_guard():
    adapter = _adapter_with_tasks([])
    support = {"task_id": "sup-1"}
    blocked_parents = [{"task_id": "sup-1", "status": "blocked"}]
    assert await adapter.can_link_support_card(support, blocked_parents) is False

    healthy_parents = [{"task_id": "sup-2", "status": "ready"}]
    assert await adapter.can_link_support_card({"task_id": "sup-2"}, healthy_parents) is True


# --- Point 65: Parked profile fallback --------------------------------------
def test_parked_profile_fallback():
    adapter = _adapter_with_tasks([])
    # Target parked -> route to fallback profile
    assert adapter.resolve_parked_profile_fallback(
        "hartono",
        parked_statuses={"hartono"},
        fallback_profile="default",
    ) == "default"
    # Target available -> keep
    assert adapter.resolve_parked_profile_fallback(
        "hartono",
        parked_statuses=set(),
        fallback_profile="default",
    ) == "hartono"


# --- Point 67: Goal iteration budget fallback --------------------------------
def test_iteration_budget_graceful_stop():
    adapter = _adapter_with_tasks([])
    assert adapter.check_iteration_budget(10)["status"] == "ok"
    assert adapter.check_iteration_budget(90, budget=100)["status"] == "warning"
    assert adapter.check_iteration_budget(100, budget=100)["status"] == "graceful_stop"
    assert adapter.check_iteration_budget(150, budget=100)["status"] == "graceful_stop"


# --- Point 69: Anti-fake completion gate ------------------------------------
def test_anti_fake_completion_gate():
    adapter = _adapter_with_tasks([])
    # No evidence -> refuse
    ok, reason = adapter.verify_completion_evidence({"task_id": "t", "status": "done"})
    assert ok is False
    # With artifact evidence -> accept
    ok2, key = adapter.verify_completion_evidence(
        {"task_id": "t", "status": "done", "artifact_path": "/tmp/x.csv"}
    )
    assert ok2 is True
    assert key == "artifact_path"
    # Not in done/review -> refuse regardless
    ok3, _ = adapter.verify_completion_evidence(
        {"task_id": "t", "status": "running", "artifact_path": "/tmp/x.csv"}
    )
    assert ok3 is False
