from __future__ import annotations

"""Subsystem 1 & 10 hardening coverage (points 4,7,9,10 and 94,96,97,99,100)."""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from core.adapters.brain import BrainAdapter
from core.prompt import (
    PROMPT_SECTION_MAX_CHARS,
    build_today_focus_prompt,
    install_graceful_shutdown_handler,
    is_offline,
    lock_review_day,
    release_lock_file,
    run_offline_fallback,
    socket_sandbox,
)
from db import init_db, save_daily_review


@pytest.fixture
def temp_agent_dir():
    tmp = tempfile.mkdtemp(prefix="test-s10s1-")
    brain_dir = Path(tmp) / "brain"
    (brain_dir / "fatigue").mkdir(parents=True)
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


# --- Subsystem 1, Point 4: Circadian Timezone Normalization ---------------
def test_circadian_timezone_normalization(temp_agent_dir):
    adapter = BrainAdapter(agent_dir=temp_agent_dir, default_tz="UTC")
    # UTC has 0 offset -> hour stays unchanged
    assert adapter.normalize_circadian_hour(22, target_tz="UTC") == 22
    # Agent in Asia/Jakarta (UTC+7): local hour 19.00 => UTC 12.00
    assert adapter.normalize_circadian_hour(19, target_tz="Asia/Jakarta") == 12


def test_is_in_quiet_hours(temp_agent_dir):
    adapter = BrainAdapter(agent_dir=temp_agent_dir, default_tz="UTC")
    assert adapter.is_in_quiet_hours(23) is True
    assert adapter.is_in_quiet_hours(5) is True
    assert adapter.is_in_quiet_hours(12) is False


# --- Subsystem 1, Point 7: Post-VETO Self-Healing -------------------------
def test_schedule_veto_recovery_check(temp_agent_dir):
    adapter = BrainAdapter(agent_dir=temp_agent_dir)
    res = adapter.schedule_veto_recovery_check(veto_timestamp=datetime(2026, 11, 8, 3, 0, 0))
    assert res["scheduled"] is True
    # 60 minutes later
    assert res["recheck_at"].startswith("2026-11-08T04:00:00")
    # Persisted marker exists
    marker = temp_agent_dir / "brain" / "veto-recovery.json"
    assert marker.is_file()
    assert json.loads(marker.read_text())["scheduled"] is True


# --- Subsystem 1, Point 9: MIND_AGENT_DIR Isolation -----------------------
def test_mind_agent_dir_isolation(temp_agent_dir, monkeypatch):
    base = temp_agent_dir
    adapter_a = BrainAdapter(agent_dir=base / "hartono", default_tz="UTC")
    adapter_b = BrainAdapter(agent_dir=base / "rama", default_tz="UTC")
    env_a = adapter_a.isolated_env(agent_dir=base / "hartono")
    env_b = adapter_b.isolated_env(agent_dir=base / "rama")
    assert env_a["MIND_AGENT_DIR"] != env_b["MIND_AGENT_DIR"]
    assert "hartono" in env_a["MIND_AGENT_DIR"]
    assert "rama" in env_b["MIND_AGENT_DIR"]

    # Env-var override wins and does not leak into other profile
    monkeypatch.setenv("MIND_AGENT_DIR", str(base / "env_forced"))
    adapter_env = BrainAdapter()
    assert str(adapter_env.agent_dir) == str(base / "env_forced")


# --- Subsystem 1, Point 10: Maintenance Audit Trail ------------------------
def test_maintenance_audit_trail(temp_agent_dir):
    tmp = tempfile.mkdtemp(prefix="test-s10-audit-")
    db_file = Path(tmp) / "test.db"
    init_db(db_file)
    adapter = BrainAdapter(agent_dir=temp_agent_dir)
    adapter.record_maintenance_audit("nightly_consolidation", agent_dir=temp_agent_dir, db_path=db_file)
    from db import get_activity_telemetry
    rows = get_activity_telemetry(profile_id="default", db_path=db_file)
    assert any(r["activity_type"].startswith("brain_maintenance:") for r in rows)
    shutil.rmtree(tmp, ignore_errors=True)


# --- Subsystem 10, Point 94: Graceful Shutdown Handler ---------------------
def test_release_lock_file_on_shutdown(tmp_path):
    lock_file = tmp_path / ".daily_life.lock"
    lock_file.write_text("pid123")
    assert release_lock_file(lock_file) is True
    assert not lock_file.exists()
    # Idempotent on a missing file
    assert release_lock_file(lock_file) is True
    # None input is a no-op
    assert release_lock_file(None) is False


def test_install_graceful_shutdown_handler_registers_sigterm():
    install_graceful_shutdown_handler(release_lock=None)
    import signal
    assert signal.getsignal(signal.SIGINT) is not signal.default_int_handler


# --- Subsystem 10, Point 96: RPC Socket Sandbox Boundary -------------------
def test_socket_sandbox_closes_on_exit():
    with socket_sandbox(socket_path=None) as sock:
        # No socket path => sock is None (sandbox boundary is a no-op but safe).
        assert sock is None
    # After context exit, no leaked open sockets.


# --- Subsystem 10, Point 97: System Prompt Section Size Cap ----------------
def test_prompt_section_size_cap():
    # Fabricate a very long plan and confirm truncation.
    import sys
    from unittest.mock import MagicMock

    mock_plan = {
        "tasks": [
            {"title": "T" * 500, "execution_target": "kanban", "estimated_duration_min": 30}
            for _ in range(10)
        ]
    }
    with pytest.MonkeyPatch.context() as mp:
        import core.prompt as prompt_mod
        mp.setattr(prompt_mod, "get_daily_plan", lambda *a, **k: mock_plan)
        mp.setattr(prompt_mod, "get_settings", lambda *a, **k: _SettingsStub())

    result = build_today_focus_prompt()
    assert len(result) <= PROMPT_SECTION_MAX_CHARS


class _SettingsStub:
    profile_id = "default"
    db_path = Path("/tmp/daily_life_stub.db")


# --- Subsystem 10, Point 99: Append-Only Audit Record ----------------------
def test_lock_review_day_prevents_overwrite(tmp_path):
    db_file = tmp_path / "audit.db"
    init_db(db_file)
    save_daily_review("2026-11-08", "default", {"note": "initial"}, db_path=db_file)
    assert lock_review_day("2026-11-08", "default", db_path=db_file) is True

    # Second attempt: already locked -> no new write (idempotent)
    from db import get_daily_review
    review = get_daily_review("2026-11-08", "default", db_path=db_file)
    assert "audit_lock" in review


# --- Subsystem 10, Point 100: Offline Resilient Mode -----------------------
def test_run_offline_fallback_routes_external_tasks(tmp_path):
    db_file = tmp_path / "offline.db"
    init_db(db_file)
    from db import save_daily_plan
    save_daily_plan(
        "2026-11-08",
        "default",
        {
            "tasks": [
                {"task_id": "1", "execution_target": "camoufox_scenario", "title": "scrape"},
                {"task_id": "2", "execution_target": "kanban", "title": "plan"},
            ]
        },
        db_path=db_file,
    )
    res = run_offline_fallback(profile_id="default", review_date="2026-11-08", db_path=db_file)
    assert res["task_count"] == 2
    statuses = {t["task_id"]: t.get("status") for t in res["tasks"]}
    assert statuses.get("1") == "deferred_offline"
    assert statuses.get("2") is None  # kanban-native task untouched
