from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from models import BrainStateSummary, GateVerdict
from exceptions import BrainUnavailableError
from .base import PluginAdapter

from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = ["BrainAdapter"]

# Subsystem 1, Point 4: Default quiet-hour window (23:00 → 06:00).
_QUIET_START_HOUR = 23
_QUIET_END_HOUR = 6
# Subsystem 1, Point 7: minutes after a VETO to re-check biological recovery.
_VETO_RECHECK_MINUTES = 60


def _parse_tz(tz_name: str) -> "ZoneInfo | None":
    """Wrap zoneinfo to tolerate invalid tz strings without crashing."""
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return None


def _tz_offset_hours(tz_name: str, at: datetime | None = None) -> float:
    """Return UTC offset in hours for the given IANA tz (best-effort, 0.0 on error)."""
    tz_info = _parse_tz(tz_name)
    if tz_info is None:
        return 0.0
    dt = at or datetime.now(timezone.utc)
    offset = dt.astimezone(tz_info).utcoffset()
    return offset.total_seconds() / 3600.0 if offset is not None else 0.0


def _shift_hour_utc(hour: int, tz_offset_hours: float) -> int:
    """Shift a local hour-of-day back to UTC (modulo 24)."""
    return (hour - int(tz_offset_hours)) % 24


class BrainAdapter(PluginAdapter):
    """Adapter for Biomimetic Brain interoception state and decision gate."""

    name: str = "biomimetic-brain"

    def __init__(
        self,
        script_path: Path | None = None,
        agent_dir: Path | None = None,
        default_tz: str = "UTC",
        profile_id: str | None = None,
    ) -> None:
        base_plugins_dir = Path(__file__).resolve().parent.parent.parent.parent
        if script_path is not None:
            self.script_path = Path(script_path)
        else:
            self.script_path = base_plugins_dir / "biomimetic-brain" / "scripts" / "decision-gate.py"

        self.consolidate_script = base_plugins_dir / "biomimetic-brain" / "scripts" / "consolidate-mind.py"
        self.profile_id = profile_id
        self.agent_dir = Path(agent_dir) if agent_dir is not None else self._resolve_profile_agent_dir()
        self.default_tz = default_tz

    def _resolve_profile_agent_dir(self) -> Path | None:
        """Subsystem 1, Point 9: Isolate brain state per profile via MIND_AGENT_DIR."""
        env_dir = os.environ.get("MIND_AGENT_DIR", "").strip()
        if env_dir:
            return Path(env_dir)
        try:
            from config import get_settings
            return get_settings(profile_id=self.profile_id).profile_path / "brain"
        except Exception as exc:
            logger.debug("Could not resolve profile brain dir: %s", exc)
            return None

    def isolated_env(self, agent_dir: Path | None = None, **overrides: str) -> dict[str, str]:
        """Subsystem 1, Point 9: Build a subprocess env pinned to one profile's brain dir.

        Prevents one profile's state from leaking into another when a single
        gateway process runs many personas in sequence.
        """
        env = self._resolve_env(agent_dir=agent_dir, **overrides)
        target = agent_dir or self.agent_dir
        if target:
            env["MIND_AGENT_DIR"] = str(target)
        return env

    def _resolve_env(
        self,
        agent_dir: Path | None = None,
        now: str | None = None,
        tz: str | None = None,
    ) -> dict[str, str]:
        env = dict(os.environ)
        target_dir = agent_dir or self.agent_dir
        if target_dir:
            env["MIND_AGENT_DIR"] = str(target_dir)

        if now:
            env["MIND_NOW"] = now
        if tz or self.default_tz:
            env["MIND_TZ"] = tz or self.default_tz

        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def is_available_sync(self) -> bool:
        """Synchronous availability check (used by sync methods)."""
        return self.script_path.is_file()

    async def is_available(self) -> bool:
        """Check if decision-gate script exists and is executable."""
        return self.script_path.is_file()

    # ------------------------------------------------------------------
    # Subsystem 1, Point 4: Circadian Timezone Normalization
    # ------------------------------------------------------------------
    def normalize_circadian_hour(
        self,
        local_hour: int,
        target_tz: str | None = None,
        now_utc: datetime | None = None,
    ) -> int:
        """Convert a host-machine local hour into the target agent's timezone.

        Subsystem 1, Point 4: Ensures quiet-hour evaluation uses the agent's
        `MIND_TZ`, not the machine clock. Returns UTC-normalized hour for
        the target tz.
        """
        tz = target_tz or self.default_tz
        offset_hours = _tz_offset_hours(tz, at=now_utc)
        return _shift_hour_utc(local_hour, offset_hours)

    def is_in_quiet_hours(self, local_hour: int, target_tz: str | None = None) -> bool:
        """Return True if the given local hour falls in the quiet window."""
        tz = target_tz or self.default_tz
        offset_hours = _tz_offset_hours(tz)
        normalized_hour = _shift_hour_utc(local_hour, offset_hours)
        return (
            normalized_hour >= _QUIET_START_HOUR
            or normalized_hour < _QUIET_END_HOUR
        )

    # ------------------------------------------------------------------
    # Subsystem 1, Point 7: Post-VETO Self-Healing Path
    # ------------------------------------------------------------------
    def schedule_veto_recovery_check(
        self,
        veto_timestamp: datetime | None = None,
        agent_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Return recovery checkpoint plan (60 min after VETO) without blocking.

        Subsystem 1, Point 7: If a VETO was issued, plan a re-check after
        `_VETO_RECHECK_MINUTES` to confirm fatigue has returned to a safe
        level. Does not block the caller.
        """
        target_dir = agent_dir or self.agent_dir
        ts = veto_timestamp or datetime.utcnow()
        recheck_at = ts + timedelta(minutes=_VETO_RECHECK_MINUTES)

        result = {
            "scheduled": True,
            "recheck_at": recheck_at.isoformat(),
            "agent_dir": str(target_dir) if target_dir else None,
            "fatigue_target_file": str(target_dir / "brain" / "fatigue" / "fatigue-state.json") if target_dir else None,
        }

        # Persist the recheck marker so the next startup cycle can act on it
        if target_dir:
            marker = target_dir / "brain" / "veto-recovery.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            try:
                marker.write_text(json.dumps(result), encoding="utf-8")
            except Exception as exc:
                logger.debug("Failed persisting veto recovery marker: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Subsystem 1, Point 10: Maintenance Audit Trail
    # ------------------------------------------------------------------
    def record_maintenance_audit(
        self,
        action: str,
        agent_dir: Path | None = None,
        profile_id: str | None = None,
        db_path: Path | None = None,
    ) -> None:
        """Record a brain maintenance action into the activity telemetry ledger.

        Subsystem 1, Point 10: Every nightly recovery / decay / corruption
        heal is traced for auditability.
        """
        try:
            from db import record_task_telemetry
            now = datetime.utcnow()
            pid = profile_id or (
                __import__("config").get_settings().profile_id
                if profile_id is None
                else None
            )
            record_task_telemetry(
                profile_id=pid or "default",
                activity_type=f"brain_maintenance:{action}",
                day_of_week=now.weekday(),
                hour_of_day=now.hour,
                estimated_duration_min=1,
                actual_duration_min=0,
                status="success",
                db_path=db_path,
            )
        except Exception as exc:
            logger.debug("Failed recording maintenance audit for '%s': %s", action, exc)

    def auto_recover_corrupt_state(self, agent_dir: Path | None = None) -> list[str]:
        """Subsystem 1, Point 5: Scan state JSON files and restore corrupted ones to default."""
        target_dir = agent_dir or self.agent_dir
        if not target_dir or not (target_dir / "brain").is_dir():
            return []

        recovered: list[str] = []
        for json_path in (target_dir / "brain").rglob("*.json"):
            if ".example." in json_path.name:
                continue
            try:
                json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupted brain state detected in %s (%s). Restoring baseline.", json_path, exc)
                # If sibling example file exists, copy it, else write minimal valid object
                example = json_path.with_name(json_path.stem + ".example.json")
                if example.is_file():
                    json_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
                else:
                    json_path.write_text("{}", encoding="utf-8")
                recovered.append(str(json_path.name))

        return recovered

    async def evaluate_decision(
        self,
        decision_label: str = "daily_planning",
        mode: str = "standard",
        agent_dir: Path | None = None,
        now: str | None = None,
        tz: str | None = None,
    ) -> BrainStateSummary:
        """Run decision-gate.py and return structured BrainStateSummary."""
        # Auto-heal any corrupt state files before evaluating
        self.auto_recover_corrupt_state(agent_dir=agent_dir)

        if not await self.is_available():
            logger.warning("Biomimetic brain script not found at %s. Returning fallback proceed.", self.script_path)
            return BrainStateSummary(
                verdict=GateVerdict.PROCEED,
                reason_code="brain_unavailable_fallback",
                reasons=["decision-gate.py script not found on disk"],
            )

        cmd = [sys.executable, str(self.script_path), decision_label, mode]
        env = self._resolve_env(agent_dir=agent_dir, now=now, tz=tz)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            exit_code = proc.returncode or 0
        except Exception as exc:
            logger.error("Failed executing decision-gate: %s", exc)
            raise BrainUnavailableError(f"Failed to execute decision gate: {exc}") from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        data: dict[str, Any] = {}
        if stdout_text:
            try:
                data = json.loads(stdout_text)
            except json.JSONDecodeError as err:
                logger.warning("decision-gate stdout was not valid JSON: %s (stdout: %s)", err, stdout_text)

        verdict_map = {0: GateVerdict.PROCEED, 1: GateVerdict.VETO, 2: GateVerdict.CAUTION}
        verdict = verdict_map.get(exit_code, GateVerdict.PROCEED)

        reason_code = data.get("reason") or data.get("reason_code") or ("proceed" if exit_code == 0 else "veto" if exit_code == 1 else "caution")
        inputs = data.get("inputs", {})

        fatigue_val = float(inputs.get("fatigue", 0.2))
        arousal_val = float(inputs.get("arousal", 0.5))
        energy_val = float(inputs.get("energy", max(0.0, min(1.0, 1.0 - fatigue_val))))

        circadian = "quiet_hours" if data.get("quiet_hours") else "active"
        missing = data.get("missing_state_files", [])
        detail = data.get("detail", reason_code)
        reasons = [detail]

        return BrainStateSummary(
            verdict=verdict,
            reason_code=reason_code,
            energy=energy_val,
            fatigue=fatigue_val,
            circadian_phase=circadian,
            reasons=reasons,
            missing_files=missing,
        )

    async def run_nightly_maintenance(self, agent_dir: Path | None = None) -> dict[str, Any]:
        """Subsystem 1, Point 1: Execute consolidate-mind script and reset fatigue to healthy baseline."""
        target_dir = agent_dir or self.agent_dir
        if not self.consolidate_script.is_file():
            logger.debug("Consolidate-mind script not found at %s.", self.consolidate_script)
            return {"status": "script_missing"}

        env = self._resolve_env(agent_dir=target_dir)
        cmd = [sys.executable, str(self.consolidate_script)]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            rc = proc.returncode or 0

            # Reset fatigue level to refreshed rest baseline in state file
            if target_dir:
                fatigue_file = target_dir / "brain" / "fatigue" / "fatigue-state.json"
                if fatigue_file.is_file():
                    try:
                        f_data = json.loads(fatigue_file.read_text(encoding="utf-8"))
                        f_data["fatigue_level"] = 0.15
                        f_data["recovery_needed"] = False
                        fatigue_file.write_text(json.dumps(f_data, indent=2), encoding="utf-8")
                    except Exception as err:
                        logger.debug("Failed updating fatigue state after rest: %s", err)

            return {
                "status": "completed" if rc == 0 else "failed",
                "returncode": rc,
                "stdout": stdout.decode("utf-8", errors="replace")[:300],
            }
        except Exception as exc:
            logger.error("Error executing consolidate-mind script: %s", exc)
            return {"status": "error", "error": str(exc)}

    def apply_elapsed_decay(self, hours_elapsed: float, agent_dir: Path | None = None) -> float:
        """Subsystem 1, Point 2: Decay fatigue during prolonged offline / idle periods."""
        target_dir = agent_dir or self.agent_dir
        if not target_dir:
            return 0.2

        fatigue_file = target_dir / "brain" / "fatigue" / "fatigue-state.json"
        if not fatigue_file.is_file():
            return 0.2

        try:
            data = json.loads(fatigue_file.read_text(encoding="utf-8"))
            current_fatigue = float(data.get("fatigue_level", 0.5))
            # Decay: reduce fatigue by 0.05 every 4 hours idle, bounded floor 0.10
            decay = (hours_elapsed / 4.0) * 0.05
            new_fatigue = max(0.10, round(current_fatigue - decay, 2))
            data["fatigue_level"] = new_fatigue
            fatigue_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return new_fatigue
        except Exception as exc:
            logger.debug("Failed applying elapsed fatigue decay: %s", exc)
            return 0.2

    def check_fatigue_spike(self, current_fatigue: float, previous_fatigue: float) -> bool:
        """Subsystem 1, Point 3: Return True if fatigue spiked sharply (> 0.40 increase)."""
        return (current_fatigue - previous_fatigue) >= 0.40

    def check_novelty_saturation(self, agent_dir: Path | None = None) -> bool:
        """Subsystem 1, Point 6: Return True if novelty is saturated (>= 0.85)."""
        target_dir = agent_dir or self.agent_dir
        if not target_dir:
            return False

        novelty_file = target_dir / "brain" / "dopamine" / "novelty-state.json"
        if novelty_file.is_file():
            try:
                data = json.loads(novelty_file.read_text(encoding="utf-8"))
                return float(data.get("novelty_score", 0.0)) >= 0.85
            except Exception:
                pass
        return False

    def record_routine_habit(self, habit_name: str, agent_dir: Path | None = None) -> None:
        """Subsystem 1, Point 8: Increment habit loop counter in basal-ganglia/habit-state.json."""
        target_dir = agent_dir or self.agent_dir
        if not target_dir:
            return

        habit_file = target_dir / "brain" / "basal-ganglia" / "habit-state.json"
        if habit_file.is_file():
            try:
                data = json.loads(habit_file.read_text(encoding="utf-8"))
                habits = data.setdefault("habits", {})
                habits[habit_name] = habits.get(habit_name, 0) + 1
                habit_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.debug("Failed recording routine habit %s: %s", habit_name, exc)

    async def get_state(self, agent_dir: Path | None = None) -> dict[str, Any]:
        """Read folded brain-state.json if present, else synthesize from gate."""
        target_dir = agent_dir or self.agent_dir
        if target_dir:
            state_file = target_dir / "brain-state.json"
            if state_file.is_file():
                try:
                    return json.loads(state_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.debug("Failed reading brain-state.json: %s", exc)

        summary = await self.evaluate_decision(agent_dir=agent_dir)
        return summary.model_dump(mode="json")
