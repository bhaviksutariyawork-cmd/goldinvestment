"""RSS feeds: commodities desks, gold trade press, and world desks for conflict.

A dead feed is normal — outlets retire URLs without redirects. Each feed is
fetched independently and failures are collected into a note rather than losing
the whole set.

Only items matching a gold, macro or conflict term are kept. An unfiltered world
feed would swamp the brief.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import mktime

from collectors.base import collector, domain_of, get, match_keywords
from goldagent.models import CollectorResult, Headline

FEEDS = {
    "Reuters commodities": "https://www.reuters.com/markets/commodities/rss",
    "Kitco news": "https://www.kitco.com/rss/KitcoNews.xml",
    "Mining.com": "https://www.mining.com/feed/",
    "Bloomberg markets": "https://feeds.bloomberg.com/markets/news.rss",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "Reuters world": "https://www.reuters.com/world/rss",
    "Investing.com commodities": "https://www.investing.com/rss/news_11.rss",
}

# An item must hit one of these to be kept.
RELEVANCE_TERMS = (
    "gold", "bullion", "xau", "precious metal", "silver",
    "real yield", "treasury yield", "tips", "breakeven",
    "fed", "fomc", "powell", "rate cut", "rate hike", "inflation", "cpi", "pce",
    "dollar", "dxy", "central bank", "reserves",
    "sanction", "embargo", "war", "strike", "missile", "conflict", "ceasefire",
    "rbi", "import duty", "customs duty", "gst",
)

MAX_PER_FEED = 25


@collector("rss", category="news")
def collect(keywords: list[str] | None = None) -> CollectorResult:
    import feedparser

    result = CollectorResult(name="rss")
    keywords = keywords or []
    problems: list[str] = []
    seen: set[str] = set()

    for name, url in FEEDS.items():
        try:
            # Fetch with our own session so the shared UA and timeout apply —
            # feedparser's default fetcher has neither and several of these
            # outlets 403 an unfamiliar agent.
            raw = get(url, timeout=20).content
            parsed = feedparser.parse(raw)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name} ({str(exc)[:90]})")
            continue

        entries = getattr(parsed, "entries", []) or []
        if not entries:
            problems.append(f"{name} (feed empty or unparseable)")
            continue

        kept = 0
        for entry in entries[:MAX_PER_FEED]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link or link in seen:
                continue

            summary = (entry.get("summary") or "").strip()
            haystack = f"{title} {summary}".lower()
            if not any(term in haystack for term in RELEVANCE_TERMS):
                continue

            seen.add(link)
            kept += 1
            result.headlines.append(
                Headline(
                    title=title,
                    url=link,
                    domain=domain_of(link),
                    source=name,
                    summary=summary[:400],
                    published_at=_entry_time(entry),
                    keywords=match_keywords(f"{title} {summary}", keywords),
                )
            )

        if kept == 0:
            problems.append(f"{name} (no relevant items)")

    if problems:
        result.notes.append("RSS feeds with no usable items: " + "; ".join(problems))

    if not result.headlines:
        result.notes.append(
            "No RSS headlines matched this run — treat the news leg as unavailable "
            "rather than concluding the news was quiet."
        )

    return result


def _entry_time(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        stamp = entry.get(key)
        if stamp:
            try:
                return datetime.fromtimestamp(mktime(stamp), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                continue
    return None
