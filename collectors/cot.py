"""CFTC Commitments of Traders — gold futures, managed money net long.

Weekly. Data is as of Tuesday's close and published Friday afternoon US time, so
for most of the week this reading is several days stale by construction. The
observation date travels with it.

What it tells you: how crowded the trade is. Extreme net long means a lot of
positioning to unwind if the story changes, and moves get fragile in both
directions. This is a fragility signal, not a direction signal.

Source is the CFTC's own Socrata API — a documented JSON endpoint, so unlike the
scrapers in this package it should stay stable.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from collectors.base import CollectorError, collector, get_json, to_float
from goldagent.models import CollectorResult, Reading

# Disaggregated futures-only report.
ENDPOINT = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
GOLD_CONTRACT_CODE = "088691"  # COMEX gold


@collector("cot", category="cot")
def collect() -> CollectorResult:
    rows = get_json(
        ENDPOINT,
        params={
            "cftc_contract_market_code": GOLD_CONTRACT_CODE,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 8,
        },
        timeout=30,
    )

    if not isinstance(rows, list) or not rows:
        raise CollectorError("CFTC returned no rows for the COMEX gold contract")

    reports = []
    for row in rows:
        parsed = _parse_row(row)
        if parsed:
            reports.append(parsed)

    if not reports:
        raise CollectorError(
            "CFTC rows had no readable managed-money positions (field names may have changed)"
        )

    result = CollectorResult(name="cot")
    latest = reports[0]
    net = latest["net_long"]

    extra: dict[str, object] = {
        "report_date": latest["date"].date().isoformat(),
        "long": latest["long"],
        "short": latest["short"],
        "open_interest": latest.get("open_interest"),
    }

    if len(reports) > 1:
        prior = reports[1]
        extra["previous_net_long"] = prior["net_long"]
        extra["previous_report_date"] = prior["date"].date().isoformat()
        extra["week_change"] = net - prior["net_long"]

    # Rank within the window tells you whether "big" is actually big. Scaled so the
    # highest reading in the window is 100 and the lowest is 0 — dividing by the
    # sample count instead would cap the maximum at (n-1)/n, so a genuinely
    # extreme net long would read as 80th percentile on a five-report window.
    history = [r["net_long"] for r in reports]
    if len(history) >= 4:
        below = sum(1 for value in history if value < net)
        extra["percentile_in_window"] = round(100.0 * below / (len(history) - 1), 1)
        extra["window_reports"] = len(history)

    result.readings.append(
        Reading(
            metric="cot_managed_money_net_long",
            value=float(net),
            unit="contracts",
            source=(
                f"CFTC Commitments of Traders, disaggregated futures-only, "
                f"COMEX gold, report of {latest['date'].date()}"
            ),
            ts=latest["date"],
            extra=extra,
        )
    )

    age_days = (datetime.now(UTC).date() - latest["date"].date()).days
    result.notes.append(
        f"COT is weekly: positions are as of {latest['date'].date()} "
        f"({age_days} days ago), published the following Friday. Not a current reading."
    )

    return result


def _parse_row(row: dict) -> dict | None:
    try:
        long_positions = to_float(row.get("m_money_positions_long_all"), field="managed money long")
        short_positions = to_float(
            row.get("m_money_positions_short_all"), field="managed money short"
        )
    except CollectorError:
        return None

    raw_date = row.get("report_date_as_yyyy_mm_dd")
    if not raw_date:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)

    open_interest = None
    with contextlib.suppress(CollectorError):
        open_interest = to_float(row.get("open_interest_all"), field="open interest")

    return {
        "date": stamp,
        "long": int(long_positions),
        "short": int(short_positions),
        "net_long": int(long_positions - short_positions),
        "open_interest": int(open_interest) if open_interest is not None else None,
    }
