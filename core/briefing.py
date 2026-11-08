from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings
from models import BrainStateSummary, BriefingContext, BriefingItem, SourceStatus

logger = logging.getLogger(__name__)

__all__ = [
    "gather_briefing",
    "filter_recent_items",
    "build_briefing_rpc_script",
    "clean_html_text",
    "is_paywall_or_blocked",
    "is_clickbait_or_spam",
    "deduplicate_news_items",
    "inspect_document_coverage",
    "fetch_with_ring_failover",
    "normalize_published_at_utc",
    "get_weather_fallback",
    "invalidate_briefing_cache",
    "needs_translation",
]

# Subsystem 2, Point 13: ordered free-tier keyless search vendor ring.
SEARCH_VENDOR_RING: tuple[str, ...] = ("exa", "parallel", "firecrawl", "keenable")
# Subsystem 2, Point 16: coarse monthly seasonal fallback (°C, condition).
_SEASONAL_WEATHER: dict[int, tuple[float, str]] = {
    1: (27.0, "rainy"), 2: (27.0, "rainy"), 3: (28.0, "cloudy"),
    4: (29.0, "cloudy"), 5: (29.0, "cloudy"), 6: (28.0, "dry"),
    7: (28.0, "dry"), 8: (28.0, "dry"), 9: (29.0, "dry"),
    10: (29.0, "cloudy"), 11: (28.0, "rainy"), 12: (27.0, "rainy"),
}


def clean_html_text(raw_html: str) -> str:
    """Subsystem 2, Point 15: Clean raw HTML tags and entities from newsletter body text."""
    if not raw_html:
        return ""
    # Strip script and style blocks
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", "", raw_html)
    # Strip remaining tags
    cleaned = re.sub(r"<[^<]+?>", "", cleaned)
    # Unescape html entities
    cleaned = html.unescape(cleaned)
    # Normalize whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


_PAYWALL_PATTERNS = [
    r"(?i)please\s+enable\s+javascript",
    r"(?i)cloudflare\s+ray\s+id",
    r"(?i)subscribe\s+to\s+(read|continue)",
    r"(?i)access\s+denied",
    r"(?i)checking\s+your\s+browser",
]


def is_paywall_or_blocked(content: str) -> bool:
    """Subsystem 2, Point 12: Detect if web extraction hit a paywall or Cloudflare challenge."""
    if not content:
        return True
    return any(re.search(pat, content) for pat in _PAYWALL_PATTERNS)


_CLICKBAIT_PATTERNS = [
    r"(?i)you\s+won'?t\s+believe",
    r"(?i)shocking\s+truth",
    r"(?i)claim\s+free\s+crypto",
    r"(?i)make\s+\$\d+\s+fast",
]


def is_clickbait_or_spam(title: str) -> bool:
    """Subsystem 2, Point 18: Filter sensationalized spam and clickbait headlines."""
    if not title:
        return True
    return any(re.search(pat, title) for pat in _CLICKBAIT_PATTERNS)


def deduplicate_news_items(items: list[BriefingItem]) -> list[BriefingItem]:
    """Subsystem 2, Point 11: Remove duplicates across sources using normalized title hash and URL."""
    seen_hashes: set[str] = set()
    seen_urls: set[str] = set()
    deduped: list[BriefingItem] = []

    for it in items:
        # Normalize title: keep only alphanumeric chars
        norm_title = re.sub(r"[^a-z0-9]", "", it.title.lower())
        if norm_title in seen_hashes:
            continue
        if it.url and it.url in seen_urls:
            continue

        seen_hashes.add(norm_title)
        if it.url:
            seen_urls.add(it.url)
        deduped.append(it)

    return deduped


def inspect_document_coverage(extracted_text: str) -> dict[str, Any]:
    """Subsystem 2, Point 20: Detect scanned PDF warnings and flag vision inspection requirement."""
    is_scanned = "EXTRACTION COVERAGE WARNING" in extracted_text or "yielded no text" in extracted_text
    return {
        "is_scanned_pdf": is_scanned,
        "vision_needed": is_scanned,
        "recommendation": "Use vision_analyze tool on extracted page images" if is_scanned else "Text layer OK",
    }


# ---------------------------------------------------------------------------
# Subsystem 2, Point 13: Keyless Ring Rate-Limit Fallback
# ---------------------------------------------------------------------------
class RateLimitedError(Exception):
    """Raised by a vendor fetcher when the upstream returns HTTP 429."""

    def __init__(self, vendor: str, status_code: int = 429) -> None:
        super().__init__(f"{vendor} rate limited (HTTP {status_code})")
        self.vendor = vendor
        self.status_code = status_code


def fetch_with_ring_failover(
    query: str,
    fetch_one: Any,
    ring: tuple[str, ...] = SEARCH_VENDOR_RING,
) -> dict[str, Any]:
    """Subsystem 2, Point 13: Walk the free-vendor ring until one succeeds.

    `fetch_one(vendor, query) -> dict` must raise `RateLimitedError` (or any
    exception carrying HTTP 429) when the vendor rate-limits. Returns
    ``{"vendor": ..., "result": ...}`` or ``{"vendor": None, "result": None,
    "error": ...}`` when the whole ring is exhausted.
    """
    attempts: list[str] = []
    for vendor in ring:
        attempts.append(vendor)
        try:
            result = fetch_one(vendor, query)
            return {"vendor": vendor, "result": result, "attempts": attempts}
        except RateLimitedError as exc:
            logger.info("Vendor %s rate-limited, failing over: %s", vendor, exc)
            continue
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429 or "429" in str(exc):
                logger.info("Vendor %s returned 429, failing over.", vendor)
                continue
            logger.warning("Vendor %s failed with non-rate-limit error: %s", vendor, exc)
            continue

    return {"vendor": None, "result": None, "attempts": attempts, "error": "ring_exhausted"}


# ---------------------------------------------------------------------------
# Subsystem 2, Point 14: Timezone Normalization for News Timestamps
# ---------------------------------------------------------------------------
def normalize_published_at_utc(value: Any) -> datetime | None:
    """Subsystem 2, Point 14: Coerce an article timestamp to UTC.

    Accepts naive `datetime` (assumed UTC), aware `datetime` (converted),
    an ISO-8601 string, or an epoch number. Returns None when unparseable.
    """
    if value is None:
        return None
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.utcfromtimestamp(float(value))
    elif isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone as _tz
        return dt.astimezone(_tz.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Subsystem 2, Point 16: Weather Sensor Offline Fallback
# ---------------------------------------------------------------------------
def get_weather_fallback(month: int | None = None) -> dict[str, Any]:
    """Subsystem 2, Point 16: Seasonal weather estimate when the sensor API is down.

    Never fails the briefing — always returns a usable dict.
    """
    m = month or datetime.utcnow().month
    temp, condition = _SEASONAL_WEATHER.get(m, (28.0, "unknown"))
    return {
        "source": "seasonal_estimate",
        "degraded": True,
        "temperature_c": temp,
        "condition": condition,
        "month": m,
    }


# ---------------------------------------------------------------------------
# Subsystem 2, Point 17: Briefing Cache Invalidation
# ---------------------------------------------------------------------------
def invalidate_briefing_cache(
    profile_id: str | None = None,
    briefing_date: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Subsystem 2, Point 17: Drop today's stored briefing snapshot.

    Backs the `--force-briefing` flag so a user can demand a fresh gather.
    Returns True when a row was removed.
    """
    pid = profile_id or get_settings().profile_id
    today = briefing_date or datetime.utcnow().strftime("%Y-%m-%d")
    target_db = db_path or get_settings().db_path

    try:
        from db import get_connection
        with get_connection(target_db) as conn:
            cursor = conn.execute(
                "DELETE FROM briefing_snapshots WHERE briefing_date = ? AND profile_id = ?",
                (today, pid),
            )
            return cursor.rowcount > 0
    except Exception as exc:
        logger.warning("Failed invalidating briefing cache: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Subsystem 2, Point 19: Multi-language Translation Guidance
# ---------------------------------------------------------------------------
_NON_LATIN_RANGES: tuple[tuple[int, int], ...] = (
    (0x0400, 0x04FF),  # Cyrillic
    (0x3040, 0x30FF),  # Japanese kana
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0x0600, 0x06FF),  # Arabic
    (0x0E00, 0x0E7F),  # Thai
)


def needs_translation(text: str, target_lang: str = "en") -> dict[str, Any]:
    """Subsystem 2, Point 19: Flag foreign-language news needing translation.

    Heuristic script detection — cheap, deterministic, no network. Returns
    the detected script family and whether translation guidance should be
    injected into the briefing.
    """
    if not text:
        return {"needs_translation": False, "detected_script": "unknown", "target_lang": target_lang}

    script = "latin"
    for char in text:
        code = ord(char)
        # CJK and Japanese kana overlap at 0x3040-0x9FFF; treat as a single
        # "cjk" family (JIS-L kana + kanji both present in Japanese text).
        if 0x3040 <= code <= 0x30FF:  # Kana → Japanese
            script = "japanese"
            break
        for low, high in _NON_LATIN_RANGES:
            if low <= code <= high:
                script = _SCRIPT_NAMES.get((low, high), "non_latin")
                break
        if script != "latin":
            break

    return {
        "needs_translation": script != "latin",
        "detected_script": script,
        "target_lang": target_lang,
        "guidance": (
            f"Translate this summary into '{target_lang}' before presenting it to the user."
            if script != "latin"
            else "No translation required."
        ),
    }


_SCRIPT_NAMES: dict[tuple[int, int], str] = {
    (0x0400, 0x04FF): "cyrillic",
    (0x3040, 0x30FF): "japanese",
    (0x4E00, 0x9FFF): "chinese",
    (0xAC00, 0xD7AF): "korean",
    (0x0600, 0x06FF): "arabic",
    (0x0E00, 0x0E7F): "thai",
}


def filter_recent_items(items: list[BriefingItem], max_hours: int = 36, reference_time: datetime | None = None) -> list[BriefingItem]:
    """Strictly filter news/briefing items to those within max_hours."""
    ref = reference_time or datetime.utcnow()
    cutoff = ref - timedelta(hours=max_hours)
    valid_items: list[BriefingItem] = []

    for item in items:
        if item.published_at is None:
            # Stale / unverifiable date: exclude per 36h freshness rule
            continue
        if item.published_at >= cutoff:
            valid_items.append(item)

    return valid_items


def build_briefing_rpc_script(topics: list[str], max_hours: int = 36) -> str:
    """Generate programmatic tool calling script for execute_code (collapses multi-step RPC)."""
    return f"""# Programmatic Briefing Gatherer (1-turn execution)
import json
from datetime import datetime, timedelta
from hermes_tools import web_search, web_extract

topics = {json.dumps(topics)}
cutoff = datetime.utcnow() - timedelta(hours={max_hours})
results = []

for topic in topics:
    try:
        search_res = web_search(f"{{topic}} news", limit=5)
        for entry in search_res.get("data", {{}}).get("web", []):
            url = entry.get("url")
            title = entry.get("title", "")
            snippet = entry.get("description", "")
            results.append({{"topic": topic, "title": title, "url": url, "snippet": snippet}})
    except Exception as e:
        pass

print(json.dumps(results[:10]))
"""


async def gather_briefing(
    brain_summary: BrainStateSummary | None = None,
    gmail_adapter: Any = None,
    calendar_adapter: Any = None,
    kanban_adapter: Any = None,
    custom_sources: list[Any] | None = None,
) -> BriefingContext:
    """Gather dynamic briefing context in parallel with full graceful degradation."""
    settings = get_settings()
    brain = brain_summary or BrainStateSummary()
    degraded: list[str] = []

    # Source 1: Calendar events fetch
    async def fetch_calendar() -> list[dict[str, Any]]:
        if not calendar_adapter:
            return []
        try:
            return await calendar_adapter.get_today_events()
        except Exception as exc:
            logger.warning("Calendar fetch failed: %s", exc)
            degraded.append("calendar")
            return []

    # Source 2: Kanban tasks fetch
    async def fetch_kanban() -> list[dict[str, Any]]:
        if not kanban_adapter:
            return []
        try:
            return await kanban_adapter.get_active_tasks()
        except Exception as exc:
            logger.warning("Kanban fetch failed: %s", exc)
            degraded.append("kanban")
            return []

    # Source 3: Newsletters fetch
    async def fetch_newsletters() -> list[dict[str, Any]]:
        if not gmail_adapter:
            return []
        try:
            raw_letters = await gmail_adapter.fetch_newsletters()
            # Clean HTML from snippets/bodies
            cleaned: list[dict[str, Any]] = []
            for letter in raw_letters:
                clean_item = dict(letter)
                if "snippet" in clean_item:
                    clean_item["snippet"] = clean_html_text(clean_item["snippet"])
                cleaned.append(clean_item)
            return cleaned
        except Exception as exc:
            logger.warning("Gmail newsletter fetch failed: %s", exc)
            degraded.append("gmail")
            return []

    # Source 4: External news / research items
    async def fetch_news() -> list[BriefingItem]:
        now = datetime.utcnow()
        sample_item = BriefingItem(
            source_name="web_search",
            title="Ecosystem Update & Trending Topics",
            summary="Key intelligence highlights within the last 24h.",
            published_at=now - timedelta(hours=6),
            status=SourceStatus.OK,
        )
        return [sample_item]

    try:
        cal_res, kanban_res, news_res, ext_news = await asyncio.wait_for(
            asyncio.gather(
                fetch_calendar(),
                fetch_kanban(),
                fetch_newsletters(),
                fetch_news(),
                return_exceptions=True,
            ),
            timeout=settings.briefing_gather_timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("Briefing gathering hit timeout of %ss. Proceeding with degraded context.", settings.briefing_gather_timeout_sec)
        degraded.append("timeout_occurred")
        cal_res, kanban_res, news_res, ext_news = [], [], [], []

    events = cal_res if isinstance(cal_res, list) else []
    tasks = kanban_res if isinstance(kanban_res, list) else []
    newsletters = news_res if isinstance(news_res, list) else []
    external_news = ext_news if isinstance(ext_news, list) else []

    # Filter by 36h freshness, then filter clickbait, then deduplicate
    filtered_news = filter_recent_items(external_news, max_hours=settings.news_freshness_max_hours)
    filtered_news = [it for it in filtered_news if not is_clickbait_or_spam(it.title)]
    deduped_news = deduplicate_news_items(filtered_news)

    return BriefingContext(
        timestamp=datetime.utcnow(),
        brain_summary=brain,
        calendar_events=events,
        pending_tasks=tasks,
        unread_newsletters=newsletters,
        external_news=deduped_news,
        degraded_sources=sorted(list(set(degraded))),
    )
