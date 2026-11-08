from __future__ import annotations

"""Subsystem 6: Two-way calendar hardening (points 53,54,57,58,59,60)."""

from datetime import datetime, timedelta

from core.adapters.calendar import (
    CalendarAdapter,
    convert_event_time,
    parse_ics_event_block,
)


# --- Point 53: Host vs client timezone conversion ---------------------------
def test_convert_event_time():
    local = datetime(2026, 11, 8, 9, 0, 0)
    # Host UTC, user UTC+7 -> user sees 16:00.
    shifted = convert_event_time(local, local_tz_offset_hours=0.0, target_tz_offset_hours=7.0)
    assert shifted == datetime(2026, 11, 8, 16, 0, 0)
    # Same offset -> no change.
    assert convert_event_time(local, 7.0, 7.0) == local


# --- Point 54: Calendar invite auto-ingestion (.ics) ------------------------
def test_parse_ics_event_block():
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Team sync\r\n"
        "DTSTART:20261108T090000Z\r\n"
        "DTEND:20261108T100000Z\r\n"
        "LOCATION:Zoom\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    parsed = parse_ics_event_block(ics)
    assert parsed is not None
    assert parsed["SUMMARY"] == "Team sync"
    assert parsed["DTSTART"] == "20261108T090000Z"
    assert parsed["LOCATION"] == "Zoom"
    assert parse_ics_event_block("no event here") is None


# --- Point 57: Calendar mutation sync to Kanban -----------------------------
def test_build_kanban_sync_delta():
    adapter = CalendarAdapter()
    ts = datetime(2026, 11, 9, 10, 0, 0)
    delta = adapter.build_kanban_sync_delta("ev-1", ts)
    assert delta["event_id"] == "ev-1"
    assert delta["kanban_action"] == "update_scheduled_start"
    assert delta["new_start"] == ts.isoformat()


# --- Point 58: Shared calendar throttling -----------------------------------
def test_shared_calendar_throttle():
    adapter = CalendarAdapter()
    now = datetime(2026, 11, 8, 12, 0, 0)
    # First sync (no prior) -> allowed.
    assert adapter.should_sync_shared_calendar(None, now=now) is True
    # Last sync 10 min ago -> throttled.
    last = now - timedelta(minutes=10)
    assert adapter.should_sync_shared_calendar(last, now=now) is False
    # Last sync 20 min ago -> allowed.
    last2 = now - timedelta(minutes=20)
    assert adapter.should_sync_shared_calendar(last2, now=now) is True

    state = adapter.build_sync_throttle_state(now=now)
    state = adapter.schedule_shared_calendar_sync(state, now=now)
    assert state["last_sync"] is not None


# --- Point 59: All-day event handling ---------------------------------------
def test_mark_all_day_event():
    adapter = CalendarAdapter()
    flag = adapter.mark_all_day_event("2026-11-08", "National holiday", events=[{"t": 1}])
    assert flag["all_day"] is True
    assert flag["heavier_task_allowed"] is False
    assert flag["existing_events"] == 1


# --- Point 60: Conflict alert via clarify -----------------------------------
def test_resolve_event_conflict_payload():
    adapter = CalendarAdapter()
    existing = {"title": "Design review", "start": "09:00"}
    candidate = {"title": "Client call", "start": "09:30"}
    result = adapter.resolve_event_conflict(existing, candidate, strategy="reschedule")
    assert result["has_conflict"] is True
    assert result["strategy"] == "reschedule"
    assert len(result["clarify"]["options"]) == 3
