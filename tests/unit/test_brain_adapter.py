from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from core.adapters.brain import BrainAdapter
from models import GateVerdict


@pytest.fixture
def temp_agent_dir():
    tmp = tempfile.mkdtemp(prefix="test-brain-agent-")
    brain_dir = Path(tmp) / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_brain_adapter_availability():
    adapter = BrainAdapter()
    assert await adapter.is_available() is True


@pytest.mark.asyncio
async def test_brain_adapter_proceed_daytime(temp_agent_dir):
    adapter = BrainAdapter()
    # Test seam: 14:00 (midday, outside quiet hours 23:00 - 05:30)
    summary = await adapter.evaluate_decision(
        decision_label="test_daytime_plan",
        mode="standard",
        agent_dir=temp_agent_dir,
        now="14:00",
        tz="UTC",
    )
    assert summary.verdict == GateVerdict.PROCEED
    assert summary.circadian_phase == "active"
    assert summary.energy > 0.0


@pytest.mark.asyncio
async def test_brain_adapter_quiet_hours_caution(temp_agent_dir):
    adapter = BrainAdapter()
    # Test seam: 02:00 (inside quiet hours 23:00 - 05:30)
    summary = await adapter.evaluate_decision(
        decision_label="test_night_plan",
        mode="standard",
        agent_dir=temp_agent_dir,
        now="02:00",
        tz="UTC",
    )
    assert summary.verdict == GateVerdict.CAUTION
    assert summary.circadian_phase == "quiet_hours"
    assert "caution_quiet_hours" in summary.reason_code


@pytest.mark.asyncio
async def test_brain_adapter_veto_high_fatigue(temp_agent_dir):
    # Seed high fatigue state file to trigger veto_fatigue rule
    fatigue_dir = temp_agent_dir / "brain" / "fatigue"
    fatigue_dir.mkdir(parents=True, exist_ok=True)
    fatigue_file = fatigue_dir / "fatigue-state.json"
    fatigue_file.write_text(json.dumps({"fatigue_level": 0.85}), encoding="utf-8")

    adapter = BrainAdapter()
    summary = await adapter.evaluate_decision(
        decision_label="test_exhausted_plan",
        mode="standard",
        agent_dir=temp_agent_dir,
        now="14:00",
        tz="UTC",
    )
    assert summary.verdict == GateVerdict.VETO
    assert "veto_fatigue" in summary.reason_code
    assert summary.fatigue == 0.85


@pytest.mark.asyncio
async def test_brain_adapter_fallback_on_missing_script(temp_agent_dir):
    adapter = BrainAdapter(script_path=Path("/non/existent/script.py"))
    assert await adapter.is_available() is False

    summary = await adapter.evaluate_decision(
        decision_label="test_fallback",
        agent_dir=temp_agent_dir,
    )
    assert summary.verdict == GateVerdict.PROCEED
    assert summary.reason_code == "brain_unavailable_fallback"
