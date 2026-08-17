"""Data structures shared across the pipeline.

The important type here is `CollectorResult`. Every collector returns one, and it
is always well-formed even when the fetch failed — see `collectors/base.py`. A
failed collector produces a result with `stale=True`, an `error` string, and no
readings, which the brief reports explicitly rather than papering over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Reading:
    """A single numeric observation of one metric."""

    metric: str
    value: float | None
    unit: str
    source: str
    ts: datetime = field(default_factory=_now)
    # Anything the collector wants to carry that isn't the headline number:
    # previous close, percent change, tonnes delta, contract month, etc.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass(slots=True)
class Headline:
    """One news item. `domain` is what conflict-escalation counts as a source."""

    title: str
    url: str
    domain: str
    source: str
    published_at: datetime | None = None
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    first_seen: datetime | None = None

    @property
    def is_new(self) -> bool:
        """True when this headline was not present at the previous run."""
        return self.first_seen is None


@dataclass(slots=True)
class CollectorResult:
    """The uniform return type of every collector. Never raised, always complete."""

    name: str
    stale: bool = False
    error: str | None = None
    readings: list[Reading] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    # Free-text observations handed to the model verbatim — e.g. "IBJA layout
    # changed, rate parsed from fallback selector" or the current SGB position.
    notes: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=_now)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.stale and self.error is None

    def reading(self, metric: str) -> Reading | None:
        for r in self.readings:
            if r.metric == metric:
                return r
        return None


@dataclass(slots=True)
class Snapshot:
    """Everything one run collected, keyed by collector name."""

    results: dict[str, CollectorResult] = field(default_factory=dict)
    collected_at: datetime = field(default_factory=_now)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results.values() if r.ok)

    @property
    def failed(self) -> list[str]:
        return sorted(name for name, r in self.results.items() if not r.ok)

    def all_readings(self) -> list[Reading]:
        out: list[Reading] = []
        for r in self.results.values():
            out.extend(r.readings)
        return out

    def all_headlines(self) -> list[Headline]:
        out: list[Headline] = []
        for r in self.results.values():
            out.extend(r.headlines)
        return out

    def value(self, metric: str) -> float | None:
        """Latest non-null value for a metric across all collectors."""
        for r in self.results.values():
            reading = r.reading(metric)
            if reading is not None and reading.value is not None:
                return reading.value
        return None

    def notes(self) -> list[str]:
        out: list[str] = []
        for name, r in sorted(self.results.items()):
            for note in r.notes:
                out.append(f"{name}: {note}")
            if not r.ok:
                out.append(f"{name}: DATA UNAVAILABLE — {r.error or 'stale'}")
        return out


@dataclass(slots=True)
class AlertEvent:
    """A fired (or candidate) event alert."""

    trigger_type: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=_now)


@dataclass(slots=True)
class Usage:
    """Token accounting for one Claude call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


@dataclass(slots=True)
class Brief:
    """A rendered briefing, whether or not it was sent."""

    mode: str
    text: str
    usage: Usage = field(default_factory=Usage)
    ts: datetime = field(default_factory=_now)
    degraded: bool = False
