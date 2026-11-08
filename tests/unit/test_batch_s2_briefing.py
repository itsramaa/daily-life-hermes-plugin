from __future__ import annotations

"""Subsystem 2: Smart ingestion hardening (points 13,14,16,17,19)."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.briefing import (
    RateLimitedError,
    SEARCH_VENDOR_RING,
    fetch_with_ring_failover,
    get_weather_fallback,
    invalidate_briefing_cache,
    needs_translation,
    normalize_published_at_utc,
)
from db import get_briefing_snapshot, init_db, save_briefing_snapshot


# --- Point 13: Keyless Ring Rate-Limit Fallback -----------------------------
def test_fetch_with_ring_failover_on_429():
    order: list[str] = []

    def fetcher(vendor: str, query: str):
        order.append(vendor)
        if vendor in ("exa", "parallel"):
            raise RateLimitedError(vendor, 429)
        return {"vendor": vendor, "query": query, "hits": ["ok"]}

    res = fetch_with_ring_failover("news", fetcher)
    assert res["vendor"] == "firecrawl"
    assert order == ["exa", "parallel", "firecrawl"]
    assert res["attempts"] == ["exa", "parallel", "firecrawl"]


def test_fetch_with_ring_failover_exhausted():
    def fetcher(vendor, query):
        raise RateLimitedError(vendor, 429)

    res = fetch_with_ring_failover("news", fetcher, ring=SEARCH_VENDOR_RING)
    assert res["vendor"] is None
    assert res["error"] == "ring_exhausted"
    assert len(res["attempts"]) == len(SEARCH_VENDOR_RING)


# --- Point 14: Timezone Normalization of News Timestamps --------------------
def test_normalize_published_at_utc():
    # Naive datetime assumed UTC.
    naive = datetime(2026, 11, 8, 6, 0, 0)
    assert normalize_published_at_utc(naive) == naive

    # Aware datetime converted to UTC.
    from datetime import timezone
    aware = datetime(2026, 11, 8, 13, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    assert normalize_published_at_utc(aware) == datetime(2026, 11, 8, 6, 0, 0)

    # ISO string with Z.
    assert normalize_published_at_utc("2026-11-08T06:00:00Z") == datetime(2026, 11, 8, 6, 0, 0)

    # Epoch seconds.
    assert normalize_published_at_utc(0) == datetime(1970, 1, 1, 0, 0, 0)

    # Unparseable -> None.
    assert normalize_published_at_utc("not-a-date") is None
    assert normalize_published_at_utc(None) is None


# --- Point 16: Weather Sensor Offline Fallback -------------------------------
def test_get_weather_fallback():
    res = get_weather_fallback(month=7)
    assert res["degraded"] is True
    assert res["condition"] == "dry"
    assert res["temperature_c"] > 0
    # Month out of range -> safe default.
    res2 = get_weather_fallback(month=99)
    assert res2["condition"] in ("unknown", "cloudy", "dry", "rainy")


# --- Point 17: Briefing Cache Invalidation ----------------------------------
def test_invalidate_briefing_cache(tmp_path):
    db_file = tmp_path / "b.db"
    init_db(db_file)
    save_briefing_snapshot("2026-11-08", "default", {"data": 1}, db_path=db_file)
    assert get_briefing_snapshot("2026-11-08", "default", db_path=db_file) is not None
    assert invalidate_briefing_cache("default", "2026-11-08", db_path=db_file) is True
    assert get_briefing_snapshot("2026-11-08", "default", db_path=db_file) is None
    # Second time: no row to delete.
    assert invalidate_briefing_cache("default", "2026-11-08", db_path=db_file) is False


# --- Point 19: Multi-language Translation Guidance --------------------------
def test_needs_translation_detection():
    assert needs_translation("Breaking news today")["needs_translation"] is False
    assert needs_translation("Технологии будущего")["detected_script"] == "cyrillic"
    assert needs_translation("日本語のニュース")["detected_script"] in ("japanese", "cjk", "chinese")
    assert needs_translation("중국어 뉴스")["detected_script"] == "korean"
    res = needs_translation("مرحبا")
    assert res["needs_translation"] is True
    assert "Translate" in res["guidance"]
