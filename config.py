from __future__ import annotations

import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["DailyLifeSettings", "get_settings", "check_disk_space_healthy", "cleanup_scratch_files"]


class DailyLifeSettings(BaseSettings):
    """Central configuration for Daily Life plugin with profile awareness."""

    profile_id: str = Field(default="default", alias="HERMES_PROFILE_ID")
    # Data root for the plugin's per-profile DB + lock files. Anchored to
    # HERMES_HOME so that per-profile gateways (which point HERMES_HOME at
    # their own home dir) do not collide on a cwd-relative path. Override
    # with DAILY_LIFE_DATA_DIR for tests or a non-standard root.
    data_dir: Path = Field(default=Path("daily-life"), alias="DAILY_LIFE_DATA_DIR")
    planning_hour: int = Field(default=7, ge=0, le=23)
    review_hour: int = Field(default=21, ge=0, le=23)
    timezone: str = Field(default="UTC", alias="MIND_TZ")
    lock_timeout_sec: float = Field(default=30.0, gt=0)
    briefing_gather_timeout_sec: float = Field(default=20.0, gt=0)
    news_freshness_max_hours: int = Field(default=36, gt=0)

    @property
    def profile_path(self) -> Path:
        """Resolve and ensure private profile directory."""
        # Check if custom data_dir was explicitly set (e.g. tests or env override)
        if "DAILY_LIFE_DATA_DIR" in os.environ:
            override = Path(os.environ["DAILY_LIFE_DATA_DIR"]).expanduser()
            if not override.is_absolute():
                helpful_home = os.environ.get("HERMES_HOME", "").strip()
                root = Path(helpful_home).expanduser() if helpful_home else Path.home() / ".hermes"
                override = root / override
            path = override / self.profile_id
        else:
            try:
                import sys
                common_dir = Path(__file__).resolve().parent.parent / "common"
                if str(common_dir.parent) not in sys.path:
                    sys.path.insert(0, str(common_dir.parent))
                from common.profile_resolver import get_profile_dir
                path = get_profile_dir(self.profile_id) / "daily-life"
            except Exception:
                # Never fall back to a cwd-relative path: a per-profile gateway
                # must keep this scoped to its own HERMES_HOME.
                helpful_home = os.environ.get("HERMES_HOME", "").strip()
                root = Path(helpful_home).expanduser() if helpful_home else Path.home() / ".hermes"
                path = root / "daily-life" / self.profile_id

        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        """Local SQLite database path per profile."""
        return self.profile_path / "daily_life.db"

    @property
    def lock_file_path(self) -> Path:
        """Atomic lock file path per profile."""
        return self.profile_path / ".daily_life.lock"


_settings_by_profile: dict[str, DailyLifeSettings] = {}


def get_settings(profile_id: str | None = None, reload: bool = False) -> DailyLifeSettings:
    """Return context-aware settings instance keyed by profile_id."""
    global _settings_by_profile
    pid = profile_id or os.environ.get("HERMES_PROFILE") or os.environ.get("HERMES_PROFILE_ID") or "default"
    pid = str(pid).strip()

    if reload or pid not in _settings_by_profile:
        _settings_by_profile[pid] = DailyLifeSettings(HERMES_PROFILE_ID=pid)
    return _settings_by_profile[pid]


import shutil
import tempfile
import time

def check_disk_space_healthy(min_mb: int = 500, path: Path | None = None) -> bool:
    """Subsystem 10, Point 93: Return True if disk has at least min_mb available."""
    target_path = path or get_settings().profile_path
    try:
        usage = shutil.disk_usage(str(target_path))
        free_mb = usage.free / (1024 * 1024)
        return free_mb >= min_mb
    except Exception:
        return True


def cleanup_scratch_files(max_age_hours: int = 24, scratch_dir: Path | None = None) -> int:
    """Subsystem 10, Point 95: Auto-purge temporary scratch files."""
    target = scratch_dir or Path(tempfile.gettempdir()) / "hermes-daily"
    if not target.is_dir():
        return 0

    removed = 0
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    for p in target.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return removed
