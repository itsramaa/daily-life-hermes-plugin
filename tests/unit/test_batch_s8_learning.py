from __future__ import annotations

"""Subsystem 8: Statistical Learning hardening (points 73,74,76,77,78,79,80)."""

import pytest

from core.optimizer import (
    apply_sample_ceiling,
    archive_telemetry_rows,
    compute_confidence_degradation,
    is_negative_learning_circuit_breaker,
    normalize_outcome_metric,
    profile_task_resources,
    track_estimation_drift,
)


# --- Point 73: Strict sample ceiling ----------------------------------------
def test_apply_sample_ceiling():
    assert apply_sample_ceiling(10) == 10
    assert apply_sample_ceiling(500, ceiling=200) == 200
    assert apply_sample_ceiling(-5) == 0


# --- Point 74: Confidence score degradation ---------------------------------
def test_compute_confidence_degradation_stable():
    # Stable series (no variance) should keep confidence unchanged.
    conf = compute_confidence_degradation([30, 30, 30, 30, 30], base_confidence=0.9)
    assert conf == 0.9


def test_compute_confidence_degradation_spiky():
    # High variance should reduce confidence.
    conf = compute_confidence_degradation([10, 50, 5, 80, 20], base_confidence=0.9)
    assert conf < 0.9


# --- Point 76: Negative learning circuit breaker ----------------------------
def test_negative_learning_circuit_breaker_systemic():
    assert is_negative_learning_circuit_breaker("Connection timeout 504", success_rate=0.2) is True
    assert is_negative_learning_circuit_breaker("DNS resolution failed", success_rate=0.4) is True
    # Non-systemic failures do not trigger the breaker.
    assert is_negative_learning_circuit_breaker("Validation error in payload", success_rate=0.2) is False
    # High success rate with network errors -> no trigger (anomaly was transient).
    assert is_negative_learning_circuit_breaker("Timeout", success_rate=0.9) is False
    assert is_negative_learning_circuit_breaker(None, success_rate=0.2) is False


# --- Point 77: Estimation drift tracking ------------------------------------
def test_track_estimation_drift():
    from datetime import datetime, timedelta

    now = datetime.utcnow().isoformat()
    older = (datetime.utcnow() - timedelta(days=5)).isoformat()
    telemetry = [
        {"estimated_duration_min": 30, "actual_duration_min": 45, "created_at": now},
        {"estimated_duration_min": 30, "actual_duration_min": 60, "created_at": now},
        {"estimated_duration_min": 20, "actual_duration_min": 10, "created_at": older},
    ]
    drift = track_estimation_drift(telemetry)
    assert "overall_drift" in drift
    assert "per_day" in drift
    assert drift["overall_drift"] > 0


# --- Point 78: Outcome metric normalization ---------------------------------
def test_normalize_outcome_metric():
    assert normalize_outcome_metric("hits_rate", 0.5) == 0.5
    assert normalize_outcome_metric("error_count", 5) == 0.5  # scaled by 10
    assert normalize_outcome_metric("cost_usd", 50) == 0.5    # scaled by 100
    assert normalize_outcome_metric("unknown_metric", 2.0) == 1.0  # clipped
    assert normalize_outcome_metric("hits_rate", -1) == 0.0


# --- Point 79: Telemetry table archiving ------------------------------------
def test_archive_telemetry_rows_counts_old():
    from datetime import datetime, timedelta

    old = (datetime.utcnow() - timedelta(days=100)).isoformat()
    recent = datetime.utcnow().isoformat()
    telemetry = [
        {"created_at": old},
        {"created_at": recent},
        {"created_at": old},
        {},
    ]
    count = archive_telemetry_rows(telemetry, cutoff_days=90)
    assert count == 2


# --- Point 80: Resource profiling -------------------------------------------
def test_profile_task_resources():
    telemetry = [
        {"activity_type": "gmail_triage", "actual_duration_min": 10},
        {"activity_type": "gmail_triage", "actual_duration_min": 20},
        {"activity_type": "kanban", "actual_duration_min": 5},
    ]
    profile = profile_task_resources(telemetry)
    assert profile["gmail_triage"] == 15.0
    assert profile["kanban"] == 5.0
