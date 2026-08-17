"""Macro drivers from FRED (St. Louis Fed).

`DFII10` — the 10-year TIPS yield — is the single most important series here.
Gold's zero yield only costs you something when real yields are positive and
rising; when they fall, the opportunity cost of holding metal falls with them.

FRED publishes on a one-business-day lag and prints `.` for non-trading days, so
the most recent observation is often two days old. That is normal, not a failure —
the observation date travels with the reading so the brief can cite it honestly.

Needs `FRED_API_KEY` (free). Without one this collector returns stale rather than
failing the run, and the brief says the macro leg is missing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from collectors.base import CollectorError, collector, get_json, to_float
from goldagent.models import CollectorResult, Reading

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# series id -> (metric name, unit, human label)
SERIES = {
    "DFII10": ("real_yield_10y", "%", "US 10Y TIPS (real) yield"),
    "DGS10": ("nominal_yield_10y", "%", "US 10Y Treasury yield"),
    "T10YIE": ("breakeven_inflation_10y", "%", "10Y breakeven inflation"),
    "FEDFUNDS": ("fed_funds_rate", "%", "Effective federal funds rate"),
}


@collector("fred", category="macro")
def collect(api_key: str | None = None) -> CollectorResult:
    result = CollectorResult(name="fred")

    if not api_key:
        raise CollectorError(
            "FRED_API_KEY is not set — real yields, breakevens and fed funds are unavailable. "
            "Get a free key at https://fredaccount.stlouisfed.org/apikeys"
        )

    failures = []
    for series_id, (metric, unit, label) in SERIES.items():
        try:
            observations = _observations(series_id, api_key)
        except Exception as exc:  # noqa: BLE001 — one dead series must not kill the rest
            failures.append(f"{series_id} ({str(exc)[:100]})")
            continue

        if not observations:
            failures.append(f"{series_id} (no observations returned)")
            continue

        latest_date, latest_value = observations[0]
        previous = observations[1] if len(observations) > 1 else None

        extra: dict[str, object] = {
            "series_id": series_id,
            "observation_date": latest_date.date().isoformat(),
            "label": label,
        }
        if previous:
            extra["previous_value"] = previous[1]
            extra["previous_observation_date"] = previous[0].date().isoformat()
            extra["change_bp"] = round((latest_value - previous[1]) * 100, 2)

        result.readings.append(
            Reading(
                metric=metric,
                value=latest_value,
                unit=unit,
                source=f"FRED series {series_id} ({label}), observation {latest_date.date()}",
                ts=latest_date,
                extra=extra,
            )
        )

        age_days = (datetime.now(UTC).date() - latest_date.date()).days
        if age_days > 4:
            result.notes.append(
                f"{series_id} latest observation is {latest_date.date()} "
                f"({age_days} days old) — cite that date, not today's"
            )

    if not result.readings:
        raise CollectorError("no FRED series resolved: " + "; ".join(failures))

    if failures:
        result.notes.append("FRED series unavailable: " + "; ".join(failures))

    return result


def _observations(series_id: str, api_key: str) -> list[tuple[datetime, float]]:
    """Most recent real observations, newest first. Skips FRED's `.` placeholders."""
    payload = get_json(
        BASE_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            # Enough rows to survive a long weekend plus holidays and still find
            # two real prints for the change calculation.
            "limit": 12,
        },
    )

    if isinstance(payload, dict) and payload.get("error_message"):
        raise CollectorError(f"FRED said: {payload['error_message']}")

    out: list[tuple[datetime, float]] = []
    for observation in (payload or {}).get("observations", []):
        raw = observation.get("value")
        if raw in (None, "", "."):
            continue
        try:
            value = to_float(raw, field=f"{series_id} value")
        except CollectorError:
            continue
        try:
            stamp = datetime.fromisoformat(observation["date"]).replace(tzinfo=UTC)
        except (KeyError, ValueError):
            continue
        out.append((stamp, value))
        if len(out) == 2:
            break
    return out
