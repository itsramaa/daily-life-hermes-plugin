from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure plugins root is in sys.path
PLUGINS_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PLUGINS_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGINS_ROOT))

from common.profile_resolver import (
    get_active_profile_name,
    get_hermes_home,
    get_profile_dir,
    resolve_profile_env,
    resolve_profile_path,
)
import importlib.util
from config import DailyLifeSettings, get_settings
from core.adapters.camoufox import CamoufoxAdapter
from gmail.config import GmailConfig
from tebar.config import TebarConfig

def _load_gcal_config():
    p = PLUGINS_ROOT / "google-calendar" / "config.py"
    spec = importlib.util.spec_from_file_location("google_calendar_config", str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["google_calendar_config"] = mod
    spec.loader.exec_module(mod)
    return mod.CalendarConfig

CalendarConfig = _load_gcal_config()


@pytest.fixture
def multi_profile_env(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="test-multi-prof-")
    tmp_path = Path(tmp)
    home_dir = tmp_path / "hermes_home"
    home_dir.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(home_dir))

    # Create 2 profile dirs
    (home_dir / "profiles" / "hartono").mkdir(parents=True)
    (home_dir / "profiles" / "coder").mkdir(parents=True)

    yield tmp_path, home_dir
    shutil.rmtree(tmp, ignore_errors=True)


def test_profile_resolver_scoping(multi_profile_env, monkeypatch):
    _, home_dir = multi_profile_env

    # 1. Default profile
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_PROFILE_ID", raising=False)
    assert get_active_profile_name() == "default"
    assert get_profile_dir("default") == home_dir.resolve()

    # 2. Hartono profile
    monkeypatch.setenv("HERMES_PROFILE", "hartono")
    assert get_active_profile_name() == "hartono"
    hartono_dir = get_profile_dir("hartono")
    assert hartono_dir == (home_dir / "profiles" / "hartono").resolve()

    # 3. Environment scoping (TEBAR_API_KEY_HARTONO vs TEBAR_API_KEY)
    monkeypatch.setenv("TEBAR_API_KEY", "global_key_123")
    monkeypatch.setenv("TEBAR_API_KEY_HARTONO", "hartono_secret_456")

    assert resolve_profile_env("TEBAR_API_KEY", profile_name="default") == "global_key_123"
    assert resolve_profile_env("TEBAR_API_KEY", profile_name="hartono") == "hartono_secret_456"

    # 4. Token path resolution
    # Seed token only in hartono profile
    hartono_token = hartono_dir / "google_token.json"
    hartono_token.write_text('{"token": "hartono_auth"}', encoding="utf-8")

    assert resolve_profile_path("google_token.json", profile_name="hartono") == hartono_token


def test_gmail_and_gcal_multi_profile_config(multi_profile_env, monkeypatch):
    _, home_dir = multi_profile_env

    hartono_dir = home_dir / "profiles" / "hartono"
    hartono_token = hartono_dir / "google_token.json"
    hartono_token.write_text('{"token": "h_tok"}', encoding="utf-8")

    # Seed root fallback token for default
    root_token = home_dir / "google_token.json"
    root_token.write_text('{"token": "root_tok"}', encoding="utf-8")

    # Gmail config resolution
    g_hartono = GmailConfig.from_env(profile_name="hartono")
    assert g_hartono.token_path == hartono_token

    g_default = GmailConfig.from_env(profile_name="default")
    assert g_default.token_path == root_token

    # Google calendar config resolution with custom calendar ID
    monkeypatch.setenv("GOOGLE_CALENDAR_ID_HARTONO", "hartono_work_cal@gmail.com")
    cal_hartono = CalendarConfig.from_env(profile_name="hartono")
    assert cal_hartono.default_calendar_id == "hartono_work_cal@gmail.com"
    assert cal_hartono.token_path == hartono_token


def test_tebar_multi_profile_config(multi_profile_env, monkeypatch):
    monkeypatch.setenv("TEBAR_API_KEY", "tb_default_key")
    monkeypatch.setenv("TEBAR_API_KEY_HARTONO", "tb_hartono_key")
    monkeypatch.setenv("TEBAR_PROFILE_ID_HARTONO", "tebar_profile_99")

    cfg_default = TebarConfig.from_env(profile_name="default")
    assert cfg_default.api_key == "tb_default_key"
    assert cfg_default.profile_id is None

    cfg_hartono = TebarConfig.from_env(profile_name="hartono")
    assert cfg_hartono.api_key == "tb_hartono_key"
    assert cfg_hartono.profile_id == "tebar_profile_99"


@pytest.mark.asyncio
async def test_camoufox_browser_profile_auto_binding(multi_profile_env, monkeypatch):
    adapter = CamoufoxAdapter()
    res = await adapter.execute_scenario(
        scenario_id="sample_test",
        mode="persistent",
        profile_id="hartono",
    )
    assert res["mode"] == "persistent"
    assert "hartono" in str(res.get("user_data_dir", ""))


def test_daily_life_multi_profile_settings(multi_profile_env):
    s_default = get_settings(profile_id="default", reload=True)
    s_hartono = get_settings(profile_id="hartono", reload=True)

    assert s_default.profile_id == "default"
    assert s_hartono.profile_id == "hartono"
    assert s_default.db_path != s_hartono.db_path
    assert "hartono" in str(s_hartono.db_path)


def test_google_workspace_setup_script_flag(multi_profile_env, monkeypatch):
    # Import setup module dynamically to test set_active_profile
    setup_path = PLUGINS_ROOT.parent / "skills" / "productivity" / "google-workspace" / "scripts" / "setup.py"
    spec = importlib.util.spec_from_file_location("google_workspace_setup", str(setup_path))
    gw_setup = importlib.util.module_from_spec(spec)
    sys.modules["google_workspace_setup"] = gw_setup
    spec.loader.exec_module(gw_setup)

    gw_setup.set_active_profile("hartono")
    assert "hartono" in str(gw_setup.TOKEN_PATH)

    gw_setup.set_active_profile("default")
    assert "hartono" not in str(gw_setup.TOKEN_PATH)


def test_file_viewer_profile_root_scoping(multi_profile_env, monkeypatch):
    sys.path.insert(0, str(PLUGINS_ROOT / "file-viewer" / "dashboard"))
    import plugin_api as fv_api

    monkeypatch.setenv("HERMES_PROFILE", "hartono")
    roots = fv_api._env_roots()
    assert len(roots) >= 1
    assert any("hartono" in str(r) for r in roots)


# ---------------------------------------------------------------------------
# Multi-profile master plan, Fase 5: end-to-end 2-persona isolation tests
# ---------------------------------------------------------------------------
def test_google_token_isolation_two_profiles(multi_profile_env, monkeypatch):
    """Two profiles resolving their own token file — no cross-overwrite."""
    _, home_dir = multi_profile_env

    # Seed each profile's token with a distinct marker.
    for profile, marker in (("hartono", "TOK_HARTONO"), ("coder", "TOK_CODER")):
        pdir = home_dir / "profiles" / profile
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "google_token.json").write_text(f'{{"t": "{marker}"}}', encoding="utf-8")

    hartono_path = resolve_profile_path("google_token.json", profile_name="hartono", fallback_root=home_dir)
    coder_path = resolve_profile_path("google_token.json", profile_name="coder", fallback_root=home_dir)

    assert hartono_path != coder_path
    assert json.loads(hartono_path.read_text())["t"] == "TOK_HARTONO"
    assert json.loads(coder_path.read_text())["t"] == "TOK_CODER"

    # Mutating one profile's token must not affect the other.
    hartono_path.write_text('{"t": "HARTONO_V2"}', encoding="utf-8")
    assert json.loads(hartono_path.read_text())["t"] == "HARTONO_V2"
    assert json.loads(coder_path.read_text())["t"] == "TOK_CODER"  # untouched


def test_gmail_gcal_config_per_profile_token(multi_profile_env, monkeypatch):
    _, home_dir = multi_profile_env
    for profile in ("hartono", "coder"):
        pdir = home_dir / "profiles" / profile
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "google_token.json").write_text("{}")

    g_hartono = GmailConfig.from_env(profile_name="hartono")
    g_coder = GmailConfig.from_env(profile_name="coder")
    assert "hartono" in str(g_hartono.token_path)
    assert "coder" in str(g_coder.token_path)
    assert g_hartono.token_path != g_coder.token_path
    assert g_hartono.token_path.is_file()
    assert g_coder.token_path.is_file()

    cal_hartono = CalendarConfig.from_env(profile_name="hartono")
    cal_coder = CalendarConfig.from_env(profile_name="coder")
    assert cal_hartono.token_path != cal_coder.token_path


def test_camoufox_browser_profile_isolation(multi_profile_env):
    """Cookie/session dirs are namespaced per persona and never collide."""
    adapter = CamoufoxAdapter()
    dir_a = adapter.resolve_persistent_session_dir("hartono")
    dir_b = adapter.resolve_persistent_session_dir("coder")
    assert dir_a != dir_b
    assert "hartono" in str(dir_a)
    assert "coder" in str(dir_b)
    # Simulated cookie stores land in separate subtrees.
    (dir_a / "Cookies").write_text("hartono_cookies")
    assert (dir_b / "Cookies").is_file() is False
    assert (dir_a / "Cookies").read_text() == "hartono_cookies"


def test_oauth_refresh_lock_serializes_writers(multi_profile_env):
    """Concurrent writers to the same token file never hold the lock together.

    Record (thread, entry, exit) instants; assert zero overlap between the
    two threads' critical sections.
    """
    import threading
    import time

    _, home_dir = multi_profile_env
    pdir = home_dir / "profiles" / "hartono"
    pdir.mkdir(parents=True, exist_ok=True)
    token = pdir / "google_token.json"
    token.write_text('{"t": "v0"}', encoding="utf-8")

    from common.profile_resolver import google_token_lock

    spans: list[tuple[str, float, float]] = []
    errors: list[Exception] = []
    release = threading.Event()

    def writer(name: str, version: str):
        try:
            with google_token_lock(token, timeout_sec=5.0):
                entry = time.monotonic()
                release.wait(timeout=5.0)  # hold the lock while the other waits
                token.write_text(json.dumps({"t": version}), encoding="utf-8")
                exit_ = time.monotonic()
                spans.append((name, entry, exit_))
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=("t1", "v1"))
    t2 = threading.Thread(target=writer, args=("t2", "v2"))
    t1.start()
    time.sleep(0.1)  # let t1 acquire the lock
    t2.start()
    time.sleep(0.2)
    release.set()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not errors, f"OAuth lock raised: {errors}"
    assert len(spans) == 2, f"expected 2 recorded spans, got {spans}"
    (n1, e1, x1), (n2, e2, x2) = sorted(spans, key=lambda s: s[1])
    # No overlap: the earlier span must end before the later one starts.
    assert x1 <= e2 + 1e-6, f"critical sections overlapped: {spans}"
    # Exactly one coherent final value persisted.
    final = json.loads(token.read_text())
    assert final["t"] in ("v1", "v2")


def test_oauth_lock_creates_sidecar_file(multi_profile_env):
    _, home_dir = multi_profile_env
    pdir = home_dir / "profiles" / "coder"
    pdir.mkdir(parents=True, exist_ok=True)
    token = pdir / "google_token.json"
    token.write_text("{}")

    from common.profile_resolver import google_token_lock

    with google_token_lock(token):
        sidecar = token.parent / "google_token.json.lock"
        assert sidecar.is_file()
    # Lock file remains on disk after release (sidecar, not a temp).
    assert sidecar.is_file()
