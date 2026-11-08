from __future__ import annotations

"""Subsystem 5: External comms hardening (points 44,47,48,49,50)."""

from unittest.mock import AsyncMock

import pytest

from core.adapters.gmail import GmailAdapter
from core.adapters.tebar import TebarAdapter


# --- Point 47: Media aspect-ratio normalization -----------------------------
def test_check_media_aspect_ratio():
    adapter = TebarAdapter()
    # 1:1
    assert adapter.check_media_aspect_ratio(100, 100)["valid"] is True
    # 16:9
    assert adapter.check_media_aspect_ratio(1600, 900)["valid"] is True
    # 9:16
    assert adapter.check_media_aspect_ratio(540, 960)["valid"] is True
    # Anything odd -> invalid
    assert adapter.check_media_aspect_ratio(100, 7)["valid"] is False
    assert adapter.check_media_aspect_ratio(0, 100)["valid"] is False


# --- Point 48: Social feedback loop ----------------------------------------
def test_fetch_engagement_metrics():
    adapter = TebarAdapter()
    baseline = adapter.fetch_engagement_metrics()
    assert baseline["likes"] == 0
    assert baseline["engagement_rate"] == 0.0
    with_ref = adapter.fetch_engagement_metrics(
        {
            "platform": "twitter",
            "post_id": "post-1",
            "likes": 120,
            "replies": 15,
            "shares": 8,
            "engagement_rate": 0.15,
        }
    )
    assert with_ref["likes"] == 120
    assert with_ref["replies"] == 15
    assert with_ref["platform"] == "twitter"


# --- Point 49: Cross-account posting lock -----------------------------------
def test_cross_account_content_lock():
    # Two separate adapters simulate two personas (isolated instances).
    hartono = TebarAdapter(profile_id="hartono")
    rama = TebarAdapter(profile_id="rama")

    content = "Hello world post"

    # Hartono acquires first.
    ok_hartono, holder = hartono.acquire_content_lock(content, "hartono")
    assert ok_hartono is True
    assert holder is None

    # Same adapter (same profile) may re-acquire.
    ok2, _ = hartono.acquire_content_lock(content, "hartono")
    assert ok2 is True

    # Release; now another profile can acquire.
    hartono.release_content_lock(content, "hartono")

    ok_rama, _ = rama.acquire_content_lock(content, "rama")
    assert ok_rama is True

    # When another adapter holds the lock for identical content, we see
    # the holder's profile name.
    rama.release_content_lock(content, "rama")
    rama.acquire_content_lock(content, "rama")
    blocked, holder_profile = hartono.acquire_content_lock(content, "hartono")
    assert blocked is False
    assert holder_profile == "rama"


# --- Point 50: Thread continuity ---------------------------------------------
def test_thread_id_registry():
    adapter = TebarAdapter()
    # No thread yet
    assert adapter.get_thread_id("topic-x") is None
    # Register and re-fetch
    adapter.register_thread_id("topic-x", "thr-100")
    assert adapter.get_thread_id("topic-x") == "thr-100"
    # Overwrite (latest wins)
    adapter.register_thread_id("topic-x", "thr-200")
    assert adapter.get_thread_id("topic-x") == "thr-200"


# --- Point 44: Email attachment auto-download --------------------------------
@pytest.mark.asyncio
async def test_download_attachments_filters_by_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("TEBAR_API_KEY", "")
    adapter = GmailAdapter()

    # Simulate a live Gmail client returning two attachments (one allowed, one not).
    class _MsgAtt:
        def __init__(self, filename: str, data: bytes):
            self.filename = filename
            self.data = data

    fake_client = AsyncMock()
    fake_client.list_attachments = AsyncMock(return_value=[
        _MsgAtt("report.pdf", b"%PDF-1.4"),
        _MsgAtt("exe.bin", b"MZ"),
        _MsgAtt("table.xlsx", b"PK\x03\x04"),
    ])
    adapter._client = fake_client

    manifest = await adapter.download_attachments(
        message_id="msg-1",
        target_dir=tmp_path,
    )
    names = {m["filename"] for m in manifest}
    assert names == {"report.pdf", "table.xlsx"}
    # Files actually written to disk
    for m in manifest:
        assert m["path"].endswith(m["filename"])
        import os
        assert os.path.exists(m["path"])


@pytest.mark.asyncio
async def test_download_attachments_no_client_returns_empty(tmp_path, monkeypatch):
    # Ensure TEBAR_API_KEY is not set and no GmailClient is resolvable.
    monkeypatch.delenv("HERMES_HOME", raising=False)
    adapter = GmailAdapter()
    manifest = await adapter.download_attachments("msg-1", tmp_path)
    assert manifest == []
