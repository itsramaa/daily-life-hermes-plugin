from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

__all__ = [
    "GateVerdict",
    "BrainStateSummary",
    "SourceStatus",
    "BriefingItem",
    "BriefingContext",
    "ExecutionTarget",
    "PlannedTaskItem",
    "DailyPlan",
    "DailyReviewReport",
    "LearnedInsight",
]


class GateVerdict(str, Enum):
    PROCEED = "proceed"
    CAUTION = "caution"
    VETO = "veto"


class BrainStateSummary(BaseModel):
    verdict: GateVerdict = GateVerdict.PROCEED
    reason_code: str = "proceed"
    energy: float = Field(default=0.8, ge=0.0, le=1.0)
    fatigue: float = Field(default=0.2, ge=0.0, le=1.0)
    circadian_phase: str = "active"
    reasons: list[str] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)


class SourceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BriefingItem(BaseModel):
    source_name: str
    title: str
    summary: str
    url: str | None = None
    published_at: datetime | None = None
    status: SourceStatus = SourceStatus.OK


class BriefingContext(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    brain_summary: BrainStateSummary = Field(default_factory=BrainStateSummary)
    calendar_events: list[dict[str, Any]] = Field(default_factory=list)
    pending_tasks: list[dict[str, Any]] = Field(default_factory=list)
    unread_newsletters: list[dict[str, Any]] = Field(default_factory=list)
    external_news: list[BriefingItem] = Field(default_factory=list)
    degraded_sources: list[str] = Field(default_factory=list)


class ExecutionTarget(str, Enum):
    KANBAN_NATIVE = "kanban_native"
    CAMOUFOX_SCENARIO = "camoufox_scenario"
    GMAIL_TRIAGE = "gmail_triage"
    TEBAR_PUBLISH = "tebar_publish"


class PlannedTaskItem(BaseModel):
    task_id: str
    title: str
    priority: int = 2
    execution_target: ExecutionTarget = ExecutionTarget.KANBAN_NATIVE
    target_scenario: str | None = None
    target_parameters: dict[str, Any] = Field(default_factory=dict)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_min: int = 30
    completion_contract: dict[str, Any] = Field(default_factory=dict)


class DailyPlan(BaseModel):
    plan_date: str
    profile_id: str
    tasks: list[PlannedTaskItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DailyReviewReport(BaseModel):
    review_date: str
    profile_id: str
    completed_task_ids: list[str] = Field(default_factory=list)
    incomplete_task_ids: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    outcomes: dict[str, Any] = Field(default_factory=dict)
    reflection_notes: str = ""


class LearnedInsight(BaseModel):
    id: str
    category: str
    finding: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    sample_size: int = Field(ge=1)
    source_profile: str
    advisory_weight: str = "medium"
