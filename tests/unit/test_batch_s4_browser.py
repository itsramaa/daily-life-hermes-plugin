from __future__ import annotations

"""Subsystem 4: Autonomous browser hardening (points 32,33,34,36,37,38,39,40)."""

from core.adapters.camoufox import CamoufoxAdapter


def _adapter() -> CamoufoxAdapter:
    return CamoufoxAdapter()


# --- Point 32: Profile session & cookie retention ---------------------------
def test_resolve_persistent_session_dir(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("HERMES_PROFILE", "hartono")
    # Ensure the profile dir resolution can create the browser subdir.
    os.environ["DAILY_LIFE_DATA_DIR"] = str(tmp_path)
    adapter = _adapter()
    dir_path = adapter.resolve_persistent_session_dir("hartono")
    assert dir_path.is_dir()
    assert "hartono" in str(dir_path)


# --- Point 33: Selector self-healing ---------------------------------------
def test_pick_selector_self_healing():
    adapter = _adapter()
    available = ["#btn-login", "a.link"]
    chosen = adapter.pick_selector(
        candidates=["button.submit", "#btn-login", "text=Masuk"],
        available=available,
    )
    assert chosen == "#btn-login"
    # No availability info -> first candidate.
    assert adapter.pick_selector(candidates=["a", "b"], available=None) == "a"
    # None when empty.
    assert adapter.pick_selector(candidates=[], available=[]) is None


# --- Point 34: Headless to headful fallback --------------------------------
def test_should_fallback_to_headful():
    adapter = _adapter()
    assert adapter.should_fallback_to_headful(attempt=1) is False
    assert adapter.should_fallback_to_headful(attempt=2) is True
    assert adapter.should_fallback_to_headful(attempt=3, max_headless_attempts=2) is True


# --- Point 36: Domain proxy routing ----------------------------------------
def test_select_proxy_strategy():
    adapter = _adapter()
    assert adapter.select_proxy_strategy("https://bank.example.co.id") == "residential"
    assert adapter.select_proxy_strategy("https://www.linkedin.com/in/x") == "residential"
    assert adapter.select_proxy_strategy("https://blog.example.com") == "direct"


# --- Point 37: Browser memory leak prevention ------------------------------
def test_build_teardown_plan_closes_resources(tmp_path):
    adapter = _adapter()
    shot = tmp_path / "step1.png"
    shot.write_bytes(b"x")
    adapter.track_screenshot(shot)
    plan = adapter.build_teardown_plan()
    assert plan["close_playwright"] is True
    assert plan["dispose_contexts"] is True
    assert str(shot) in plan["cleanup_screenshots"]
    assert plan["screenshot_count"] == 1


# --- Point 38: Nested iframe navigation ------------------------------------
def test_build_iframe_selector():
    adapter = _adapter()
    # Single target -> as-is.
    assert adapter.build_iframe_selector(["button.submit"]) == "button.submit"
    # Nested -> frame chain.
    chain = adapter.build_iframe_selector(["frame#outer", "#inner", "button.go"])
    assert ">>" in chain
    assert chain.endswith("button.go")


# --- Point 39: Screencast & screenshot cleanup ------------------------------
def test_cleanup_screenshots(tmp_path):
    adapter = _adapter()
    s1 = tmp_path / "a.png"
    s2 = tmp_path / "b.png"
    s1.write_bytes(b"x")
    s2.write_bytes(b"y")
    adapter.track_screenshot(s1)
    adapter.track_screenshot(s2)
    removed = adapter.cleanup_screenshots()
    assert removed == 2
    assert not s1.exists()
    # Tracked set is empty after cleanup.
    assert adapter.build_teardown_plan()["screenshot_count"] == 0


# --- Point 40: Step-by-step diagnostic replay ------------------------------
def test_step_diagnostics_and_replay():
    adapter = _adapter()
    adapter.record_step_log(0, selector="#user", error=None)
    adapter.record_step_log(1, selector="#pass", error="element not visible")
    adapter.record_step_log(2, selector="#submit", error=None)

    diags = adapter.get_step_diagnostics()
    assert len(diags) == 3
    replay = adapter.build_replay_plan("scen-1")
    assert replay["total_steps"] == 3
    assert replay["replay_from_step"] == 1
    assert len(replay["failed_steps"]) == 1
