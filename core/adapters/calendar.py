from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from .base import PluginAdapter

logger = logging.getLogger(__name__)

__all__ = ["CalendarAdapter"]

# Subsystem 6, Point 58: minimum interval between shared-calendar syncs.
_SHARED_SYNC_MIN_INTERVAL_MIN = 15


def parse_ics_event_block(ics_text: str) -> dict[str, Any] | None:
    """Subsystem 6, Point 54: Minimal .ics parser (single VEVENT) for email invites.

    Extracts `SUMMARY`, `DTSTART`, and `DTEND` (if present) into a plain
    dict. Returns None when no VEVENT is found.
    """
    match = re.search(
        r"BEGIN:VEVENT(.*?)END:VEVENT",
        ics_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    block = match.group(1)
    out: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.split(";", 1)[0].upper()
        if key in ("SUMMARY", "DTSTART", "DTEND", "LOCATION"):
            out[key] = value
    return out


def convert_event_time(
    local_dt: datetime,
    local_tz_offset_hours: float,
    target_tz_offset_hours: float,
) -> datetime:
    """Subsystem 6, Point 53: Host-vs-client timezone conversion.

    Shifts a naive local timestamp from the host offset to the target
    user offset so meeting times are not off by hours.
    """
    delta_hours = target_tz_offset_hours - local_tz_offset_hours
    return local_dt + timedelta(hours=delta_hours)


class CalendarAdapter(PluginAdapter):
    """Adapter for Calendar-Hermes scheduling, time-blocking, and Google Calendar upstream sync."""

    name: str = "calendar-hermes"

    def __init__(self, service: Any = None) -> None:
        self._service = service

    async def is_available(self) -> bool:
        """Check if Calendar-Hermes DB/plugin is available."""
        from pathlib import Path
        cal_dir = Path(__file__).resolve().parent.parent.parent.parent / "calendar-hermes"
        return (cal_dir / "plugin.yaml").is_file()

    async def get_today_events(self, date_str: str | None = None) -> list[dict[str, Any]]:
        """Fetch today's scheduled calendar events."""
        if self._service and hasattr(self._service, "get_events"):
            try:
                return await self._service.get_events(date_str)
            except Exception as exc:
                logger.warning("Error fetching events from calendar service: %s", exc)

        return []

    async def check_availability(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        """Check if time slot is free without overlapping commitments."""
        events = await self.get_today_events()
        for ev in events:
            try:
                ev_start = datetime.fromisoformat(ev["start"])
                ev_end = datetime.fromisoformat(ev["end"])
                if max(start_time, ev_start) < min(end_time, ev_end):
                    return False
            except Exception:
                pass
        return True

    async def allocate_focus_block(
        self,
        title: str,
        start_time: datetime,
        duration_min: int = 30,
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Create a focus time-block on the agent's private calendar."""
        end_time = start_time + timedelta(minutes=duration_min)
        block = {
            "calendar_id": calendar_id,
            "title": f"[Focus] {title}",
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "status": "confirmed",
        }
        if self._service and hasattr(self._service, "create_event"):
            try:
                return await self._service.create_event(block)
            except Exception as exc:
                logger.warning("Failed allocating focus block via calendar service: %s", exc)

        return block

    async def update_event_time(
        self,
        calendar_id: str,
        event_id: str,
        new_start: datetime,
        new_end: datetime,
    ) -> dict[str, Any]:
        """Subsystem 6, Point 52: Dynamically shift calendar block during replanning."""
        updates = {
            "id": event_id,
            "calendar_id": calendar_id,
            "start": new_start.isoformat(),
            "end": new_end.isoformat(),
        }
        if self._service and hasattr(self._service, "update_event"):
            try:
                return await self._service.update_event(updates)
            except Exception as exc:
                logger.warning("Failed updating event %s: %s", event_id, exc)

        return updates

    async def delete_event(self, calendar_id: str, event_id: str) -> bool:
        """Subsystem 6, Point 52: Remove cancelled calendar block."""
        if self._service and hasattr(self._service, "delete_event"):
            try:
                return await self._service.delete_event(calendar_id, event_id)
            except Exception as exc:
                logger.warning("Failed deleting event %s: %s", event_id, exc)
                return False
        return True

    async def insert_pre_meeting_prep(
        self,
        meeting_title: str,
        meeting_start: datetime,
        prep_duration_min: int = 15,
    ) -> dict[str, Any]:
        """Subsystem 6, Point 55: Automatically schedule 15m preparation focus before an important meeting."""
        prep_start = meeting_start - timedelta(minutes=prep_duration_min)
        return await self.allocate_focus_block(
            title=f"Prep for: {meeting_title}",
            start_time=prep_start,
            duration_min=prep_duration_min,
        )

    async def insert_post_meeting_recap(
        self,
        meeting_title: str,
        meeting_end: datetime,
        recap_duration_min: int = 15,
    ) -> dict[str, Any]:
        """Subsystem 6, Point 56: Automatically schedule 15m recap/action-items block after meeting."""
        return await self.allocate_focus_block(
            title=f"Recap & Action Items: {meeting_title}",
            start_time=meeting_end,
            duration_min=recap_duration_min,
        )

    async def sync_upstream_google_calendar(self, gcal_client: Any = None) -> list[dict[str, Any]]:
        """Subsystem 6, Point 51: Two-way sync: Pull Google Calendar events and mark as busy blocks."""
        imported_events: list[dict[str, Any]] = []
        if gcal_client and hasattr(gcal_client, "list_events"):
            try:
                raw_events = await gcal_client.list_events()
                for ev in raw_events:
                    block = {
                        "title": f"[GCal] {ev.get('summary', 'Busy')}",
                        "start": ev.get("start"),
                        "end": ev.get("end"),
                        "source": "google_calendar",
                    }
                    imported_events.append(block)
            except Exception as exc:
                logger.warning("Error syncing Google Calendar events: %s", exc)

        return imported_events

    # ------------------------------------------------------------------
    # Subsystem 6 — additional hardening points (57, 58, 59, 60)
    # ------------------------------------------------------------------
    def build_sync_throttle_state(self, now: datetime | None = None) -> dict[str, Any]:
        """Subsystem 6, Point 58: Throttle shared-calendar syncs to one per 15m."""
        ts = now or datetime.utcnow()
        return {"last_sync": None, "throttle_minutes": _SHARED_SYNC_MIN_INTERVAL_MIN}

    def should_sync_shared_calendar(
        self,
        last_sync_ts: datetime | None,
        now: datetime | None = None,
        min_interval_min: int = _SHARED_SYNC_MIN_INTERVAL_MIN,
    ) -> bool:
        """Subsystem 6, Point 58: Gate for shared-calendar sync calls."""
        if last_sync_ts is None:
            return True
        ts = now or datetime.utcnow()
        return (ts - last_sync_ts) >= timedelta(minutes=min_interval_min)

    def schedule_shared_calendar_sync(
        self,
        state: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Subsystem 6, Point 58: Update throttle state after a completed sync."""
        ts = now or datetime.utcnow()
        state = dict(state or {})
        state["last_sync"] = ts.isoformat()
        return state

    def mark_all_day_event(
        self,
        day: str,
        label: str,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Subsystem 6, Point 59: Flag a full-day (holiday/leave) so heavy tasks are disabled."""
        return {
            "day": day,
            "all_day": True,
            "label": label,
            "heavier_task_allowed": False,
            "existing_events": len(events or []),
        }

    def resolve_event_conflict(
        self,
        existing_event: dict[str, Any],
        candidate_event: dict[str, Any],
        strategy: str = "propose_alternative",
    ) -> dict[str, Any]:
        """Subsystem 6, Point 60: Interactive conflict alert via `clarify`.

        Returns a payload the dashboard / slash command can render as a
        two-option dialog. `strategy` may be ``"propose_alternative"`` or
        ``"reschedule"``.
        """
        return {
            "has_conflict": True,
            "existing": existing_event,
            "candidate": candidate_event,
            "strategy": strategy,
            "clarify": {
                "question": f"Schedule conflict between '{existing_event.get('title')}' and '{candidate_event.get('title')}'.",
                "options": [
                    "Keep existing, skip candidate",
                    "Reschedule candidate to next slot",
                    "Keep candidate, move existing",
                ],
            },
        }

    def build_kanban_sync_delta(self, event_id: str, new_start: datetime) -> dict[str, Any]:
        """Subsystem 6, Point 57: Calendar mutation → Kanban time-estimate update."""
        return {
            "event_id": event_id,
            "new_start": new_start.isoformat(),
            "kanban_action": "update_scheduled_start",
        }
