"""GDELT 2.0 document API — gold, conflict, sanctions and central-bank coverage.

Free, no key, and indexes global news within about fifteen minutes. Used for two
things: surfacing stories the RSS set misses, and feeding the conflict-escalation
trigger, which needs coverage across independent domains rather than one outlet's
wire copy.

Queries are deliberately narrow. GDELT will happily return thousands of loosely
related articles, and a brief built on that noise is worse than one built on five
good headlines.
"""

from __future__ import annotations

from datetime import UTC, datetime

from collectors.base import collector, domain_of, get_json, match_keywords
from goldagent.models import CollectorResult, Headline

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

QUERIES = {
    "gold_market": '(gold price OR bullion OR "gold demand") sourcelang:english',
    "central_banks": '("central bank" gold reserves) OR ("gold purchases" central bank) sourcelang:english',
    "sanctions": "(sanctions OR embargo) (gold OR reserves OR bullion) sourcelang:english",
    "conflict": (
        '(airstrike OR missile OR invasion OR "state of emergency" OR mobilisation) '
        "sourcelang:english"
    ),
}

MAX_RECORDS = 40


@collector("gdelt", category="news")
def collect(keywords: list[str] | None = None, timespan: str = "24h") -> CollectorResult:
    result = CollectorResult(name="gdelt")
    keywords = keywords or []
    seen_urls: set[str] = set()
    problems: list[str] = []

    for topic, query in QUERIES.items():
        try:
            articles = _search(query, timespan)
        except Exception as exc:  # noqa: BLE001 — one bad query must not kill the rest
            problems.append(f"{topic} ({str(exc)[:100]})")
            continue

        for article in articles:
            url = (article.get("url") or "").strip()
            title = (article.get("title") or "").strip()
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)

            result.headlines.append(
                Headline(
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    source=f"GDELT/{topic} — {article.get('domain') or domain_of(url)}",
                    published_at=_parse_seendate(article.get("seendate")),
                    keywords=match_keywords(title, keywords),
                )
            )

    if problems:
        result.notes.append("GDELT queries failed: " + "; ".join(problems))

    if not result.headlines and not problems:
        result.notes.append(f"GDELT returned no matching articles in the last {timespan}")

    return result


def _search(query: str, timespan: str) -> list[dict]:
    payload = get_json(
        API_URL,
        params={
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": timespan,
            "maxrecords": MAX_RECORDS,
            "sort": "hybridrel",
        },
        timeout=30,
    )
    if not isinstance(payload, dict):
        return []
    articles = payload.get("articles")
    return articles if isinstance(articles, list) else []


def _parse_seendate(raw: str | None) -> datetime | None:
    """GDELT stamps look like `20260817T063000Z`."""
    if not raw:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
