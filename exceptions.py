from __future__ import annotations

__all__ = [
    "DailyLifeError",
    "AdapterError",
    "AdapterUnavailableError",
    "BrainUnavailableError",
    "DecisionGateVetoError",
    "LockAcquisitionError",
    "PlanConflictError",
]


class DailyLifeError(Exception):
    """Base exception for Daily Life plugin."""


class AdapterError(DailyLifeError):
    """Error communicating with external subsystem/plugin."""


class AdapterUnavailableError(AdapterError):
    """Target plugin or service is unavailable."""


class BrainUnavailableError(AdapterUnavailableError):
    """Biomimetic brain script or state is unavailable."""


class DecisionGateVetoError(DailyLifeError):
    """Daily planning vetoed by Biomimetic Brain."""


class LockAcquisitionError(DailyLifeError):
    """Failed to acquire execution lock."""


class PlanConflictError(DailyLifeError):
    """Unresolved scheduling or capacity conflict."""
