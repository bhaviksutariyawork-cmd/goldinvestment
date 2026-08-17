"""India gold policy: import duty, GST, RBI reserves, customs notifications.

A duty change moves the reader's cost basis more than most macro news, and it
arrives through channels the commodity desks do not always cover the same day —
CBIC notifications, PIB releases, Budget documents.

This collector surfaces candidate items. It does not parse a duty rate out of
them: a wrongly-parsed duty percentage presented as current would be worse than
no reading at all, since the whole point of the India layer is cost-basis
accuracy. The configured `india.duty_stack_fallback` supplies the arithmetic
baseline, labelled as a fallback, and any policy headline here is flagged for the
analyst to read and act on.
"""

from __future__ import annotations

from collectors.base import collector, domain_of, get, get_json
from goldagent.models import CollectorResult, Headline

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

QUERIES = {
    "duty_gst": (
        '(India) (gold OR bullion) ("import duty" OR "customs duty" OR GST OR cess OR tariff) '
        "sourcelang:english"
    ),
    "rbi_reserves": '(RBI OR "Reserve Bank of India") (gold reserves OR gold holdings) sourcelang:english',
    "policy": '(India) (gold) ("sovereign gold bond" OR "monetisation" OR hallmarking OR smuggling) sourcelang:english',
}

FEEDS = {
    "PIB India": "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
    "Economic Times markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Business Standard markets": "https://www.business-standard.com/rss/markets-106.rss",
}

POLICY_TERMS = (
    "import duty", "customs duty", "duty", "gst", "cess", "tariff",
    "gold", "bullion", "rbi", "reserve bank", "sovereign gold bond", "sgb",
    "hallmark", "budget",
)

# A headline must hit a policy term AND a gold term to be kept.
GOLD_TERMS = ("gold", "bullion", "sgb", "sovereign gold bond")


@collector("india_policy", category="news")
def collect() -> CollectorResult:
    import feedparser

    result = CollectorResult(name="india_policy")
    problems: list[str] = []
    seen: set[str] = set()

    for topic, query in QUERIES.items():
        try:
            payload = get_json(
                GDELT_URL,
                params={
                    "query": query,
                    "mode": "artlist",
                    "format": "json",
                    "timespan": "7d",
                    "maxrecords": 25,
                    "sort": "hybridrel",
                },
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"GDELT {topic} ({str(exc)[:90]})")
            continue

        for article in (payload or {}).get("articles", []):
            url = (article.get("url") or "").strip()
            title = (article.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            if not _relevant(title):
                continue
            seen.add(url)
            result.headlines.append(
                Headline(
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    source=f"India policy/{topic} — {article.get('domain') or domain_of(url)}",
                    keywords=[t for t in POLICY_TERMS if t in title.lower()],
                )
            )

    for name, url in FEEDS.items():
        try:
            parsed = feedparser.parse(get(url, timeout=20).content)
            entries = getattr(parsed, "entries", []) or []
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name} ({str(exc)[:90]})")
            continue

        for entry in entries[:30]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link or link in seen or not _relevant(title):
                continue
            seen.add(link)
            result.headlines.append(
                Headline(
                    title=title,
                    url=link,
                    domain=domain_of(link),
                    source=name,
                    summary=(entry.get("summary") or "")[:400],
                    keywords=[t for t in POLICY_TERMS if t in title.lower()],
                )
            )

    if problems:
        result.notes.append("India policy sources unavailable: " + "; ".join(problems))

    duty_flags = [
        h.title
        for h in result.headlines
        if any(term in h.title.lower() for term in ("import duty", "customs duty", "gst", "cess", "tariff"))
    ]
    if duty_flags:
        result.notes.append(
            "POSSIBLE DUTY/GST CHANGE — verify against the linked source before stating a "
            "new rate, and flag it prominently if confirmed: " + "; ".join(duty_flags[:4])
        )

    if not result.headlines:
        result.notes.append("No India gold-policy items found in the last 7 days")

    return result


def _relevant(title: str) -> bool:
    lowered = title.lower()
    return any(g in lowered for g in GOLD_TERMS) and any(p in lowered for p in POLICY_TERMS)
