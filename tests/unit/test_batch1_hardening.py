from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from config import check_disk_space_healthy, cleanup_scratch_files
from core.adapters.brain import BrainAdapter
from core.lifecycle import AtomicFileLock
from db import checkpoint_wal, init_db, redact_sensitive_info, vacuum_db


@pytest.fixture
def temp_brain_dir():
    tmp = tempfile.mkdtemp(prefix="test-b1-brain-")
    brain_dir = Path(tmp) / "brain"
    (brain_dir / "fatigue").mkdir(parents=True, exist_ok=True)
    (brain_dir / "dopamine").mkdir(parents=True, exist_ok=True)
    (brain_dir / "basal-ganglia").mkdir(parents=True, exist_ok=True)
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_brain_recovery_and_decay(temp_brain_dir):
    adapter = BrainAdapter(agent_dir=temp_brain_dir)

    fatigue_file = temp_brain_dir / "brain" / "fatigue" / "fatigue-state.json"
    fatigue_file.write_text(json.dumps({"fatigue_level": 0.70, "recovery_needed": True}), encoding="utf-8")

    # 1. Decay after 8 hours idle
    new_fatigue = adapter.apply_elapsed_decay(hours_elapsed=8.0, agent_dir=temp_brain_dir)
    assert new_fatigue < 0.70

    # 2. Check fatigue spike detector
    assert adapter.check_fatigue_spike(0.75, 0.20) is True
    assert adapter.check_fatigue_spike(0.35, 0.20) is False

    # 3. Nightly maintenance
    res = await adapter.run_nightly_maintenance(agent_dir=temp_brain_dir)
    assert "status" in res
    # Fatigue should be normalized to rest baseline
    f_data = json.loads(fatigue_file.read_text(encoding="utf-8"))
    assert f_data["fatigue_level"] <= 0.20


def test_brain_corrupt_state_auto_healing(temp_brain_dir):
    corrupt_file = temp_brain_dir / "brain" / "dopamine" / "novelty-state.json"
    # Write invalid JSON
    corrupt_file.write_text("{invalid_json_content...", encoding="utf-8")

    adapter = BrainAdapter(agent_dir=temp_brain_dir)
    healed = adapter.auto_recover_corrupt_state(agent_dir=temp_brain_dir)
    assert "novelty-state.json" in healed

    # File must now be valid JSON
    assert json.loads(corrupt_file.read_text(encoding="utf-8")) is not None


def test_brain_habit_and_novelty(temp_brain_dir):
    adapter = BrainAdapter(agent_dir=temp_brain_dir)
    novelty_file = temp_brain_dir / "brain" / "dopamine" / "novelty-state.json"
    novelty_file.write_text(json.dumps({"novelty_score": 0.90}), encoding="utf-8")
    assert adapter.check_novelty_saturation(agent_dir=temp_brain_dir) is True

    habit_file = temp_brain_dir / "brain" / "basal-ganglia" / "habit-state.json"
    habit_file.write_text(json.dumps({"habits": {}}), encoding="utf-8")
    adapter.record_routine_habit("morning_briefing", agent_dir=temp_brain_dir)
    habits = json.loads(habit_file.read_text(encoding="utf-8"))["habits"]
    assert habits["morning_briefing"] == 1


def test_system_hardening_sqlite_and_secrets():
    tmp = tempfile.mkdtemp(prefix="test-b1-sys-")
    db_file = Path(tmp) / "test.db"
    init_db(db_file)

    checkpoint_wal(db_file)
    vacuum_db(db_file)

    # Secret redaction
    log_sample = "Config loaded api_key='sk-live1234567890abcdef1234567890' for user."
    sanitized = redact_sensitive_info(log_sample)
    assert "sk-live" not in sanitized
    assert "[REDACTED]" in sanitized

    # Disk space check
    assert check_disk_space_healthy(min_mb=10, path=Path(tmp)) is True

    # Scratch purge
    scratch_dir = Path(tmp) / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "tmp_old.txt").write_text("test")
    # Fake old mtime
    old_time = time.time() - 100000
    os.utime(str(scratch_dir / "tmp_old.txt"), (old_time, old_time))
    purged = cleanup_scratch_files(max_age_hours=1, scratch_dir=scratch_dir)
    assert purged == 1

    shutil.rmtree(tmp, ignore_errors=True)


def test_stale_lock_recovery():
    tmp = tempfile.mkdtemp(prefix="test-b1-lock-")
    lock_file = Path(tmp) / "stale.lock"
    lock_file.write_text("crashed_pid")
    # Backdate mtime 1000 seconds
    old_time = time.time() - 1000
    os.utime(str(lock_file), (old_time, old_time))

    # Lock with stale_timeout_sec=500 should auto-purge the stale lock and succeed
    lock = AtomicFileLock(lock_file, timeout_sec=1.0, stale_timeout_sec=500.0)
    lock.acquire()
    lock.release()
    shutil.rmtree(tmp, ignore_errors=True)
