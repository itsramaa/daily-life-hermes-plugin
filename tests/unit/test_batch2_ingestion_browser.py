from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.adapters.camoufox import CamoufoxAdapter
from core.briefing import (
    clean_html_text,
    deduplicate_news_items,
    inspect_document_coverage,
    is_clickbait_or_spam,
    is_paywall_or_blocked,
)
from models import BriefingItem


def test_clean_html_text():
    raw = "<p>Hello <b>World</b>! &amp; welcome.<style>body{color:red;}</style><script>alert(1)</script></p>"
    cleaned = clean_html_text(raw)
    assert "Hello World! & welcome." in cleaned
    assert "alert" not in cleaned
    assert "style" not in cleaned


def test_is_paywall_or_blocked():
    assert is_paywall_or_blocked("Attention: Cloudflare Ray ID: 819283912. Access Denied.") is True
    assert is_paywall_or_blocked("Please subscribe to read the full article.") is True
    assert is_paywall_or_blocked("Here is the full open access tutorial on Python.") is False


def test_is_clickbait_or_spam():
    assert is_clickbait_or_spam("You won't believe what happened next!") is True
    assert is_clickbait_or_spam("Claim Free Crypto Now!") is True
    assert is_clickbait_or_spam("Release Notes for Linux Kernel 6.12") is False


def test_deduplicate_news_items():
    now = datetime.utcnow()
    items = [
        BriefingItem(source_name="s1", title="Hermes Agent 0.22 Released", summary="...", published_at=now, url="http://a.com/1"),
        BriefingItem(source_name="s2", title="hermes agent 0.22 released!", summary="...", published_at=now, url="http://b.com/2"), # duplicate title
        BriefingItem(source_name="s3", title="Different Title", summary="...", published_at=now, url="http://a.com/1"), # duplicate url
        BriefingItem(source_name="s4", title="Unique News Story", summary="...", published_at=now, url="http://c.com/3"),
    ]

    deduped = deduplicate_news_items(items)
    assert len(deduped) == 2
    assert deduped[0].title == "Hermes Agent 0.22 Released"
    assert deduped[1].title == "Unique News Story"


def test_inspect_document_coverage():
    scanned_text = "[EXTRACTION COVERAGE WARNING: 198 of 311 pages in this PDF yielded no text.]"
    res = inspect_document_coverage(scanned_text)
    assert res["is_scanned_pdf"] is True
    assert res["vision_needed"] is True

    normal_text = "This is a normal document with all text layers intact."
    res2 = inspect_document_coverage(normal_text)
    assert res2["is_scanned_pdf"] is False
    assert res2["vision_needed"] is False


def test_camoufox_dynamic_scenario_builder():
    tmp = tempfile.mkdtemp(prefix="test-b2-camoufox-")
    tmp_path = Path(tmp)

    class MockStorage:
        def __init__(self, d):
            self.d = d
        def save(self, data):
            p = self.d / f"{data['id']}.yaml"
            p.write_text("scenario_saved: true", encoding="utf-8")
            return p
        def delete(self, sid):
            p = self.d / f"{sid}.yaml"
            if p.is_file():
                p.unlink()
                return True
            return False

    adapter = CamoufoxAdapter(storage=MockStorage(tmp_path))

    # 1. Create and save scenario dynamically
    steps = [
        {"name": "Go to portal", "action": "goto", "url": "https://portal.example.com"},
        {"name": "Click Download", "action": "click", "selector": "button#dl"},
    ]
    res = adapter.create_and_save_scenario(
        scenario_id="portal_auto_dl",
        name="Auto Download Portal Report",
        steps=steps,
    )
    assert res["id"] == "portal_auto_dl"
    assert (tmp_path / "portal_auto_dl.yaml").is_file()

    # 2. Verify download file
    sample_dl = tmp_path / "report.csv"
    sample_dl.write_text("id,val\n1,100")
    assert adapter.verify_download_file(sample_dl, min_bytes=5) is True

    # 3. Delete scenario
    assert adapter.delete_scenario("portal_auto_dl") is True
    assert not (tmp_path / "portal_auto_dl.yaml").is_file()

    shutil.rmtree(tmp, ignore_errors=True)
