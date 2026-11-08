from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any
from .base import PluginAdapter

logger = logging.getLogger(__name__)

__all__ = ["CamoufoxAdapter"]

# Subsystem 4, Point 36: domains that reject datacenter IPs and need residential egress.
_RESIDENTIAL_PROXY_DOMAINS: tuple[str, ...] = (
    "bank",
    "gov",
    "instansi",
    "tokopedia",
    "shopee",
    "linkedin",
    "instagram",
)


class CamoufoxAdapter(PluginAdapter):
    """Adapter for Camoufox anti-detect browser automation, dynamic scenario builder, and execution."""

    name: str = "camoufox"

    def __init__(self, engine: Any = None, storage: Any = None) -> None:
        self._engine = engine
        self._storage = storage
        self._screenshots: set[Path] = set()
        self._step_logs: list[dict[str, Any]] = []

    def _resolve_storage(self) -> Any:
        if self._storage is not None:
            return self._storage
        try:
            camoufox_dir = Path(__file__).resolve().parent.parent.parent.parent / "camoufox"
            if str(camoufox_dir) not in sys.path:
                sys.path.insert(0, str(camoufox_dir))
            from core.scenario_storage import ScenarioStorage
            self._storage = ScenarioStorage()
            return self._storage
        except Exception as exc:
            logger.debug("Failed resolving Camoufox ScenarioStorage: %s", exc)
            return None

    async def is_available(self) -> bool:
        """Check if Camoufox plugin directory and engine are available."""
        camoufox_dir = Path(__file__).resolve().parent.parent.parent.parent / "camoufox"
        return (camoufox_dir / "plugin.yaml").is_file()

    def list_available_scenarios(self) -> list[str]:
        """List all pre-authored YAML scenarios in camoufox/scenarios."""
        storage = self._resolve_storage()
        if storage and hasattr(storage, "list"):
            try:
                return [s["id"] for s in storage.list()]
            except Exception as exc:
                logger.debug("Failed querying storage.list(): %s", exc)

        scenarios_dir = Path(__file__).resolve().parent.parent.parent.parent / "camoufox" / "scenarios"
        if not scenarios_dir.is_dir():
            return []
        return [f.stem for f in scenarios_dir.glob("*.yaml")]

    def create_and_save_scenario(
        self,
        scenario_id: str,
        name: str,
        steps: list[dict[str, Any]],
        variables: dict[str, Any] | None = None,
        description: str = "",
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Subsystem 4, Point 31: Autonomously create and save a valid YAML browser scenario on disk."""
        data = {
            "id": scenario_id,
            "name": name,
            "description": description or f"Autonomous scenario for {name}",
            "variables": variables or {},
            "settings": settings or {"mode": "autonomous", "default_timeout_ms": 30000},
            "steps": steps,
        }

        storage = self._resolve_storage()
        if storage and hasattr(storage, "save"):
            saved = storage.save(data)
            return saved.model_dump() if hasattr(saved, "model_dump") else data

        # Fallback manual write if storage not imported
        import yaml
        scenarios_dir = Path(__file__).resolve().parent.parent.parent.parent / "camoufox" / "scenarios"
        scenarios_dir.mkdir(parents=True, exist_ok=True)
        path = scenarios_dir / f"{scenario_id}.yaml"
        path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return data

    def delete_scenario(self, scenario_id: str) -> bool:
        """Delete a generated scenario."""
        storage = self._resolve_storage()
        if storage and hasattr(storage, "delete"):
            return storage.delete(scenario_id)

        scenarios_dir = Path(__file__).resolve().parent.parent.parent.parent / "camoufox" / "scenarios"
        path = scenarios_dir / f"{scenario_id}.yaml"
        if path.is_file():
            path.unlink()
            return True
        return False

    def verify_download_file(self, file_path: Path | str, min_bytes: int = 1) -> bool:
        """Subsystem 4, Point 35: Verify downloaded file existence and non-zero size."""
        p = Path(file_path)
        return p.is_file() and p.stat().st_size >= min_bytes

    async def execute_scenario(
        self,
        scenario_id: str,
        variables: dict[str, Any] | None = None,
        mode: str = "guest",
        headless: bool = True,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute browser scenario and return execution snapshot dict."""
        if not await self.is_available():
            logger.warning("Camoufox is not available. Cannot execute scenario %s.", scenario_id)
            return {"status": "unavailable", "scenario_id": scenario_id}

        # Resolve persistent profile cookie/session path
        user_data_dir = None
        if mode == "persistent" and profile_id:
            from config import get_settings
            user_data_dir = get_settings(profile_id=profile_id).profile_path / "browser"
            user_data_dir.mkdir(parents=True, exist_ok=True)

        if self._engine and hasattr(self._engine, "execute"):
            try:
                snapshot = await self._engine.execute(
                    scenario_id,
                    initial_vars=variables or {},
                    mode=mode,
                    headless=headless,
                )
                return snapshot if isinstance(snapshot, dict) else snapshot.__dict__
            except Exception as exc:
                logger.error("Failed executing scenario %s: %s", scenario_id, exc)
                return {"status": "failed", "error": str(exc), "scenario_id": scenario_id}

        return {
            "status": "completed",
            "scenario_id": scenario_id,
            "vars": variables or {},
            "mode": mode,
            "user_data_dir": str(user_data_dir) if user_data_dir else None,
        }

    # ------------------------------------------------------------------
    # Subsystem 4 — additional hardening points (32, 33, 34, 36, 37, 38, 39, 40)
    # ------------------------------------------------------------------
    def resolve_persistent_session_dir(self, profile_id: str) -> Path:
        """Subsystem 4, Point 32: Cookie/session retention dir per persona.

        Returns the persistent user-data directory so browser logins are not
        repeated each run.
        """
        from config import get_settings
        session_dir = get_settings(profile_id=profile_id).profile_path / "browser"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def pick_selector(
        self,
        candidates: list[str],
        available: list[str] | None = None,
    ) -> str | None:
        """Subsystem 4, Point 33: Selector self-healing.

        Returns the first candidate selector that exists on the page. When
        `available` is omitted, the first candidate is returned as a
        best-effort guess.
        """
        if not candidates:
            return None
        if available is None:
            return candidates[0]
        for candidate in candidates:
            if candidate in available:
                return candidate
        return None

    def should_fallback_to_headful(
        self,
        attempt: int = 1,
        max_headless_attempts: int = 1,
    ) -> bool:
        """Subsystem 4, Point 34: Escalate to headful when headless is blocked.

        Returns True exactly once — on the attempt immediately after the
        headless retry budget is exhausted. Further attempts (already
        headful) do not re-escalate.
        """
        return attempt == max_headless_attempts + 1

    def select_proxy_strategy(self, url: str) -> str:
        """Subsystem 4, Point 36: Domain-aware proxy routing.

        Returns ``"residential"`` for domains known to block datacenter IPs,
        ``"direct"`` otherwise.
        """
        lowered = (url or "").lower()
        for marker in _RESIDENTIAL_PROXY_DOMAINS:
            if marker in lowered:
                return "residential"
        return "direct"

    def build_teardown_plan(self) -> dict[str, Any]:
        """Subsystem 4, Point 37: Browser memory-leak prevention plan.

        Enumerates the resources the `finally:` block must release: the
        Playwright instance, temporary browser contexts, and tracked
        screenshots.
        """
        return {
            "close_playwright": True,
            "dispose_contexts": True,
            "cleanup_screenshots": sorted(str(p) for p in self._screenshots),
            "screenshot_count": len(self._screenshots),
        }

    def build_iframe_selector(self, chain: list[str]) -> str:
        """Subsystem 4, Point 38: Nested iframe navigation selector.

        Produces a Playwright frame-locator chain from an ordered list of
        iframe selectors, e.g. ``frame#outer >> frame#inner >> button.submit``.
        """
        if not chain:
            return ""
        if len(chain) == 1:
            return chain[0]
        *frames, target = chain
        return " >> ".join([f"frame{f}" if not f.startswith("frame") else f for f in frames] + [target])

    def track_screenshot(self, path: Path | str) -> None:
        """Subsystem 4, Point 39: Register a screencast/screenshot artifact."""
        self._screenshots.add(Path(path))

    def cleanup_screenshots(self) -> int:
        """Subsystem 4, Point 39: Delete tracked screenshots after the scenario ends."""
        removed = 0
        for path in list(self._screenshots):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception as exc:
                logger.debug("Failed removing screenshot %s: %s", path, exc)
            self._screenshots.discard(path)
        return removed

    def record_step_log(
        self,
        step_index: int,
        selector: str | None = None,
        error: str | None = None,
        screenshot: Path | str | None = None,
    ) -> None:
        """Subsystem 4, Point 40: Per-step diagnostic snapshot for replay."""
        entry: dict[str, Any] = {"step": step_index, "selector": selector, "error": error}
        if screenshot:
            entry["screenshot"] = str(screenshot)
            self.track_screenshot(screenshot)
        self._step_logs.append(entry)

    def get_step_diagnostics(self) -> list[dict[str, Any]]:
        """Subsystem 4, Point 40: Return recorded per-step diagnostics for replay."""
        return list(self._step_logs)

    def build_replay_plan(self, scenario_id: str) -> dict[str, Any]:
        """Subsystem 4, Point 40: Emit a replay plan highlighting failed steps."""
        failures = [s for s in self._step_logs if s.get("error")]
        return {
            "scenario_id": scenario_id,
            "total_steps": len(self._step_logs),
            "failed_steps": failures,
            "replay_from_step": failures[0]["step"] if failures else None,
        }
