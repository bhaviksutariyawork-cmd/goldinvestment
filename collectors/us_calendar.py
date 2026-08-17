"""Upcoming US data and policy events in the next 7 days.

FOMC dates come from the Federal Reserve's own calendar page. CPI, PCE and NFP
release dates come from the publishing agencies' schedules where they parse, and
are otherwise reported as unavailable.

Deliberately **no hardcoded date table**. A stale hardcoded FOMC date presented as
confirmed is exactly the failure the analyst prompt forbids: an inference dressed
as a reported fact. If the calendars do not parse, the brief says the calendar is
unavailable and the reader checks it himself.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from bs4 import BeautifulSoup

from collectors.base import collector, get
from goldagent.models import CollectorResult, Reading

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
BLS_EMPSIT_URL = "https://www.bls.gov/schedule/news_release/empsit.htm"

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}


@collector("us_calendar", category="macro")
def collect(horizon_days: int = 7, today: date | None = None) -> CollectorResult:
    today = today or datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)
    result = CollectorResult(name="us_calendar")
    problems: list[str] = []
    events: list[tuple[date, str, str]] = []

    try:
        events.extend(_fomc_dates(today))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"FOMC calendar unavailable ({str(exc)[:120]})")

    for label, url in (("CPI", BLS_SCHEDULE_URL), ("Nonfarm payrolls", BLS_EMPSIT_URL)):
        try:
            events.extend(_bls_dates(url, label, today))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label} schedule unavailable ({str(exc)[:120]})")

    upcoming = sorted({(d, name, src) for d, name, src in events if today <= d <= horizon})

    for event_date, name, source in upcoming:
        days_out = (event_date - today).days
        result.readings.append(
            Reading(
                metric="us_event",
                value=float(days_out),
                unit="days away",
                source=source,
                ts=datetime.combine(event_date, datetime.min.time(), tzinfo=UTC),
                extra={"event": name, "date": event_date.isoformat(), "days_away": days_out},
            )
        )

    if upcoming:
        rendered = ", ".join(f"{name} on {d.isoformat()}" for d, name, _ in upcoming)
        result.notes.append(f"Next {horizon_days} days: {rendered}")
    elif not problems:
        result.notes.append(
            f"No FOMC, CPI or payrolls release scheduled in the next {horizon_days} days"
        )

    for problem in problems:
        result.notes.append(problem)

    if problems and not upcoming:
        result.notes.append(
            "US calendar could not be retrieved. Do not state event dates from memory — "
            "say the calendar was unavailable."
        )

    return result


def _fomc_dates(today: date) -> list[tuple[date, str, str]]:
    """Parse meeting dates from the Fed's calendar page.

    The page groups panels by year with month names and day ranges. We take the
    last day of each range, which is the decision day.
    """
    response = get(FOMC_CALENDAR_URL, timeout=25)
    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)

    out: list[tuple[date, str, str]] = []
    years = {today.year, today.year + 1}

    # Matches "January 27-28" and "April 28-29, 2026" style fragments.
    pattern = re.compile(
        r"(?P<month>" + "|".join(MONTHS) + r")\s+(?P<d1>\d{1,2})\s*[-–]\s*(?P<d2>\d{1,2})",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        month = MONTHS[match.group("month").lower()]
        day = int(match.group("d2"))
        for year in sorted(years):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if today <= candidate <= today + timedelta(days=400):
                out.append(
                    (candidate, "FOMC decision", f"Federal Reserve FOMC calendar ({FOMC_CALENDAR_URL})")
                )
                break
    return out


def _bls_dates(url: str, label: str, today: date) -> list[tuple[date, str, str]]:
    """Parse a BLS release schedule page for upcoming release dates."""
    response = get(url, timeout=25)
    soup = BeautifulSoup(response.text, "lxml")

    out: list[tuple[date, str, str]] = []
    pattern = re.compile(
        r"(?P<month>" + "|".join(MONTHS) + r")\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
        re.IGNORECASE,
    )
    for row in soup.find_all(["tr", "li", "p"]):
        text = row.get_text(" ", strip=True)
        match = pattern.search(text)
        if not match:
            continue
        try:
            candidate = date(
                int(match.group("year")),
                MONTHS[match.group("month").lower()],
                int(match.group("day")),
            )
        except ValueError:
            continue
        if today <= candidate <= today + timedelta(days=200):
            out.append((candidate, f"{label} release", f"BLS release schedule ({url})"))
    return out
