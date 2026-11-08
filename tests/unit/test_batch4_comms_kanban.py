from __future__ import annotations

from datetime import datetime, timedelta
import shutil
import tempfile
from pathlib import Path

import pytest

from core.adapters.gmail import GmailAdapter
from core.adapters.kanban import KanbanAdapter
from core.adapters.tebar import TebarAdapter


@pytest.mark.asyncio
async def test_gmail_adapter_triage_and_draft():
    adapter = GmailAdapter()
    assert isinstance(adapter.check_oauth_token_health(), bool)

    # Draft email
    draft = await adapter.draft_email_reply(
        thread_id="th-100",
        to="client@example.com",
        subject="Project Timeline",
        body="Here is the updated timeline.",
    )
    assert draft["requires_approval"] is True
    assert draft["subject"] == "Re: Project Timeline"


def test_tebar_adapter_character_limits_and_cooldown():
    adapter = TebarAdapter()

    # Character limit checks
    long_text = "A" * 350
    x_post = adapter.enforce_platform_character_limit(long_text, platform="x")
    assert len(x_post) <= 280
    assert x_post.endswith("...")

    threads_post = adapter.enforce_platform_character_limit(long_text, platform="threads")
    assert len(threads_post) == 350

    # Cooldown check
    now = datetime.utcnow()
    recent = now - timedelta(minutes=45)
    assert adapter.check_shadowban_cooldown(last_post_time=recent, min_hours=2.0) is False

    past = now - timedelta(hours=3)
    assert adapter.check_shadowban_cooldown(last_post_time=past, min_hours=2.0) is True


@pytest.mark.asyncio
async def test_tebar_schedule_draft_approval_gate():
    adapter = TebarAdapter()
    draft = await adapter.schedule_draft(
        platform="threads",
        content="Exciting update coming soon!",
        scheduled_for="2026-11-08T19:00:00",
    )
    assert draft["requires_approval"] is True
    assert draft["status"] == "drafted_approval_pending"


@pytest.mark.asyncio
async def test_kanban_adapter_lifecycle_and_attachments(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="test-b4-kanban-")
    tmp_path = Path(tmp)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    adapter = KanbanAdapter()

    # 1. Update task status
    update = await adapter.update_task_status("t-123", "review", result_summary="Done with analysis")
    assert update["status"] == "review"

    # 2. Add comment
    comment = await adapter.add_comment("t-123", "hartono", "Completed scraping, attached CSV")
    assert comment["author"] == "hartono"

    # 3. Attach artifact file
    sample_file = tmp_path / "scraped_data.csv"
    sample_file.write_text("id,name,value\n1,alpha,100")

    attach_res = await adapter.attach_artifact("t-123", sample_file)
    assert attach_res["status"] == "attached"
    attached_file = Path(attach_res["path"])
    assert attached_file.is_file()
    assert attached_file.read_text() == "id,name,value\n1,alpha,100"

    shutil.rmtree(tmp, ignore_errors=True)
