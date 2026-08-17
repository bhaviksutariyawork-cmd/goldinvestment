"""Sovereign Gold Bond status.

**This collector assumes nothing about whether tranches are being issued.**

SGB issuance was a periodic RBI programme, not a standing facility, and issuance
has been paused for extended stretches. A briefing that says "the next tranche
opens shortly" because the code assumed a schedule would be actively misleading
about an instrument the reader might buy.

So: check RBI's press releases and the wider record, report what is actually
found, and when nothing is found say exactly that — no tranche calendar, no
inferred cadence, no "typically issued in..." language.
"""

from __future__ import annotations

from collectors.base import collector, domain_of, get, get_json
from goldagent.models import CollectorResult, Headline

RBI_PRESS_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
RBI_NOTIFICATIONS_URL = "https://www.rbi.org.in/Scripts/NotificationUser.aspx"

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = '"sovereign gold bond" sourcelang:english'

TRANCHE_TERMS = ("sovereign gold bond", "sgb")
ISSUANCE_TERMS = ("tranche", "subscription", "issue price", "series", "open for subscription")
DISCONTINUE_TERMS = ("discontinued", "no further", "halted", "paused", "not issue", "premature redemption")


@collector("sgb", category="india")
def collect(check_enabled: bool = True) -> CollectorResult:
    result = CollectorResult(name="sgb")

    if not check_enabled:
        result.notes.append("SGB check disabled in config; no position reported")
        return result

    problems: list[str] = []
    found: list[Headline] = []

    try:
        payload = get_json(
            GDELT_URL,
            params={
                "query": GDELT_QUERY,
                "mode": "artlist",
                "format": "json",
                "timespan": "60d",
                "maxrecords": 30,
                "sort": "datedesc",
            },
            timeout=30,
        )
        for article in (payload or {}).get("articles", []):
            title = (article.get("title") or "").strip()
            url = (article.get("url") or "").strip()
            if not title or not url:
                continue
            if not any(term in title.lower() for term in TRANCHE_TERMS):
                continue
            found.append(
                Headline(
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    source=f"SGB sweep — {article.get('domain') or domain_of(url)}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"news sweep ({str(exc)[:100]})")

    try:
        rbi_hits = _rbi_mentions()
        found.extend(rbi_hits)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"RBI site ({str(exc)[:100]})")

    result.headlines.extend(found[:15])

    # Characterise what was found without asserting a schedule.
    issuance_signals = [h.title for h in found if any(t in h.title.lower() for t in ISSUANCE_TERMS)]
    pause_signals = [h.title for h in found if any(t in h.title.lower() for t in DISCONTINUE_TERMS)]

    if issuance_signals:
        result.notes.append(
            "SGB: items mentioning issuance mechanics found in the last 60 days. Read the "
            "linked sources before describing any tranche as open — do not infer a schedule: "
            + "; ".join(issuance_signals[:3])
        )
    if pause_signals:
        result.notes.append(
            "SGB: items suggesting issuance is paused or discontinued: "
            + "; ".join(pause_signals[:3])
        )
    if not found:
        result.notes.append(
            "SGB: no mentions found in the last 60 days from RBI or news. Report that no "
            "current SGB activity was found. Do NOT state that a tranche is upcoming, and "
            "do not describe an issuance cadence."
        )
    elif not issuance_signals and not pause_signals:
        result.notes.append(
            "SGB: mentions found but none indicate an open tranche or a formal pause. "
            "Report only what the linked sources say."
        )

    if problems:
        result.notes.append("SGB sources partially unavailable: " + "; ".join(problems))

    return result


def _rbi_mentions() -> list[Headline]:
    """Scan RBI press releases and notifications for SGB items."""
    from bs4 import BeautifulSoup

    out: list[Headline] = []
    for url in (RBI_PRESS_URL, RBI_NOTIFICATIONS_URL):
        response = get(url, timeout=25)
        soup = BeautifulSoup(response.text, "lxml")
        for anchor in soup.find_all("a"):
            title = anchor.get_text(" ", strip=True)
            if not title or not any(term in title.lower() for term in TRANCHE_TERMS):
                continue
            href = anchor.get("href") or ""
            if href.startswith("/"):
                href = "https://www.rbi.org.in" + href
            out.append(
                Headline(
                    title=title,
                    url=href or url,
                    domain="rbi.org.in",
                    source="RBI (rbi.org.in)",
                )
            )
    return out
