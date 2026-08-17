"""Central bank gold demand — World Gold Council releases.

Monthly cadence, reported with a lag of several weeks. This is the slowest series
in the pipeline and the most structurally important: official-sector buying has
been the persistent bid under the market, and it does not react to the same things
speculative flow reacts to.

The WGC does not offer a free numeric API. What is reachable is their research and
press output, so this collector surfaces those releases and lets the analyst read
the reported figures out of them. It reports headlines and explicitly declines to
put a tonnage number on the record it did not read from a release.
"""

from __future__ import annotations

from collectors.base import collector, domain_of, get, get_json
from goldagent.models import CollectorResult, Headline

WGC_FEEDS = (
    ("World Gold Council research", "https://www.gold.org/rss/research"),
    ("World Gold Council press", "https://www.gold.org/rss/press-releases"),
)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = (
    '("central bank gold" OR "gold reserves" OR "official sector") '
    "(tonnes OR purchases OR buying) sourcelang:english"
)

RELEVANCE_TERMS = ("central bank", "official sector", "reserves", "demand", "tonne", "purchase")


@collector("central_banks", category="central_bank")
def collect() -> CollectorResult:
    import feedparser

    result = CollectorResult(name="central_banks")
    problems: list[str] = []
    seen: set[str] = set()

    for name, url in WGC_FEEDS:
        try:
            parsed = feedparser.parse(get(url, timeout=25).content)
            entries = getattr(parsed, "entries", []) or []
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name} ({str(exc)[:90]})")
            continue

        if not entries:
            problems.append(f"{name} (feed empty)")
            continue

        for entry in entries[:15]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            result.headlines.append(
                Headline(
                    title=title,
                    url=link,
                    domain=domain_of(link),
                    source=name,
                    summary=(entry.get("summary") or "")[:400],
                )
            )

    # Secondary sweep so a WGC outage does not blind this leg entirely.
    try:
        payload = get_json(
            GDELT_URL,
            params={
                "query": GDELT_QUERY,
                "mode": "artlist",
                "format": "json",
                "timespan": "14d",
                "maxrecords": 20,
                "sort": "hybridrel",
            },
            timeout=30,
        )
        for article in (payload or {}).get("articles", []):
            url = (article.get("url") or "").strip()
            title = (article.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            if not any(term in title.lower() for term in RELEVANCE_TERMS):
                continue
            seen.add(url)
            result.headlines.append(
                Headline(
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    source=f"GDELT central-bank sweep — {article.get('domain') or domain_of(url)}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"GDELT central-bank sweep ({str(exc)[:90]})")

    if problems:
        result.notes.append("Central bank sources unavailable: " + "; ".join(problems))

    result.notes.append(
        "No numeric central-bank tonnage is collected — the WGC has no free numeric feed. "
        "Only report official-sector figures you can read directly from one of the linked "
        "releases, and cite that release. Do not state a tonnage from memory."
    )

    if not result.headlines:
        result.notes.append(
            "No central-bank releases or coverage found this run. Say the official-sector "
            "leg had nothing new rather than restating an old figure."
        )

    return result
