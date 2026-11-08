from __future__ import annotations

import ast
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.briefing import build_briefing_rpc_script, filter_recent_items, gather_briefing
from models import BrainStateSummary, BriefingItem, GateVerdict, SourceStatus


def test_filter_recent_items_36h_rule():
    now = datetime(2026, 11, 8, 12, 0, 0)
    items = [
        BriefingItem(
            source_name="fresh_news",
            title="Fresh Release",
            summary="Released 12h ago",
            published_at=now - timedelta(hours=12),
        ),
        BriefingItem(
            source_name="borderline_news",
            title="Yesterday Release",
            summary="Released 35h ago",
            published_at=now - timedelta(hours=35),
        ),
        BriefingItem(
            source_name="stale_news",
            title="Old Article",
            summary="Published 48h ago",
            published_at=now - timedelta(hours=48),
        ),
        BriefingItem(
            source_name="unverifiable_news",
            title="Undated Article",
            summary="No date specified",
            published_at=None,
        ),
    ]

    filtered = filter_recent_items(items, max_hours=36, reference_time=now)
    assert len(filtered) == 2
    titles = [x.title for x in filtered]
    assert "Fresh Release" in titles
    assert "Yesterday Release" in titles
    assert "Old Article" not in titles
    assert "Undated Article" not in titles


def test_build_briefing_rpc_script_syntax():
    script = build_briefing_rpc_script(topics=["hermes agent", "python 3.12"], max_hours=36)
    assert "from hermes_tools import web_search, web_extract" in script
    assert "json.dumps(results[:10])" in script

    # Validate that generated script is syntactically valid Python
    parsed = ast.parse(script)
    assert parsed is not None


@pytest.mark.asyncio
async def test_gather_briefing_graceful_degradation():
    # Mock calendar adapter that throws an unexpected error
    failing_calendar = AsyncMock()
    failing_calendar.get_today_events.side_effect = ConnectionError("Calendar DB unreachable")

    # Mock working kanban adapter
    working_kanban = AsyncMock()
    working_kanban.get_active_tasks.return_value = [{"task_id": "k-1", "title": "Audit Security"}]

    brain_summary = BrainStateSummary(verdict=GateVerdict.PROCEED, energy=0.9, fatigue=0.1)

    context = await gather_briefing(
        brain_summary=brain_summary,
        calendar_adapter=failing_calendar,
        kanban_adapter=working_kanban,
    )

    # Must NOT crash; should record degraded calendar source
    assert context is not None
    assert "calendar" in context.degraded_sources
    assert len(context.pending_tasks) == 1
    assert context.pending_tasks[0]["title"] == "Audit Security"
    assert context.brain_summary.energy == 0.9


@pytest.mark.asyncio
async def test_gather_briefing_full_context():
    mock_calendar = AsyncMock()
    mock_calendar.get_today_events.return_value = [{"event_id": "ev-1", "summary": "Team Sync"}]

    mock_kanban = AsyncMock()
    mock_kanban.get_active_tasks.return_value = [{"task_id": "t-1", "title": "Scrape portal"}]

    mock_gmail = AsyncMock()
    mock_gmail.fetch_newsletters.return_value = [{"subject": "Weekly Tech Digest"}]

    context = await gather_briefing(
        calendar_adapter=mock_calendar,
        kanban_adapter=mock_kanban,
        gmail_adapter=mock_gmail,
    )

    assert len(context.calendar_events) == 1
    assert len(context.pending_tasks) == 1
    assert len(context.unread_newsletters) == 1
    assert len(context.degraded_sources) == 0
