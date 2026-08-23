"""Date windows, and the settling lag that every comparison has to respect.

Data rule 4: the trailing 3 days are attribution-incomplete. Meta keeps
revising them for days after the fact, so a ROAS built on them drifts under
your feet. Everything comparative is measured against `settled_end` — the last
date we are willing to treat as final — never against today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .constants import SETTLING_DAYS, TRAILING_WINDOW_DAYS


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def iso(value: date) -> str:
    return value.isoformat()


@dataclass(frozen=True)
class Window:
    """An inclusive [start, end] date range."""

    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {"start": iso(self.start), "end": iso(self.end), "days": self.days}


def settled_end(as_of: date, settling_days: int = SETTLING_DAYS) -> date:
    """Last date whose attribution we trust.

    `as_of` is normally the newest date we hold a snapshot for.
    """
    return as_of - timedelta(days=settling_days)


def settling_window(as_of: date, settling_days: int = SETTLING_DAYS) -> Window:
    """The days a chart must mark "settling" rather than plot as fact."""
    return Window(as_of - timedelta(days=settling_days - 1), as_of)


def trailing(end: date, days: int = TRAILING_WINDOW_DAYS) -> Window:
    return Window(end - timedelta(days=days - 1), end)


def trailing_settled(as_of: date, days: int = TRAILING_WINDOW_DAYS,
                     settling_days: int = SETTLING_DAYS) -> Window:
    """The trailing window every "7d" trigger in the brief refers to.

    Ends at the settled edge, not at `as_of` — otherwise a 7d ROAS would be
    three-sevenths made of numbers Meta has not finished counting.
    """
    return trailing(settled_end(as_of, settling_days), days)


def lifetime_settled(first_date: date, as_of: date,
                     settling_days: int = SETTLING_DAYS) -> Window:
    return Window(first_date, settled_end(as_of, settling_days))


def is_settling(day: date, as_of: date, settling_days: int = SETTLING_DAYS) -> bool:
    return day > settled_end(as_of, settling_days)


def resolve_range(as_of: date, preset: str | None,
                  start: str | None = None, end: str | None = None) -> Window:
    """Turn a UI date-range selection into a settled window.

    Presets always stop at the settled edge. An explicit custom range is
    honoured as given but still clipped to the settled edge, so no filter
    choice can smuggle unsettled days into a ROAS comparison.
    """
    edge = settled_end(as_of)
    if preset == "custom" and start and end:
        return Window(parse_date(start), min(parse_date(end), edge))
    days = {"7d": 7, "14d": 14, "30d": 30, "60d": 60, "90d": 90}.get(preset or "30d", 30)
    return trailing(edge, days)
