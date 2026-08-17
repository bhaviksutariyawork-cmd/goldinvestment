"""SPDR Gold Shares (GLD) tonnes held.

ETF tonnage is the cleanest read on Western investment demand. The daily delta
matters more than the level: sustained inflows under a rising price mean the move
has real buying behind it, while a rising price on falling tonnage usually means
futures positioning rather than allocation.

Source is the archive CSV the trust publishes for its own reporting.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from collectors.base import CollectorError, collector, get, to_float
from goldagent.models import CollectorResult, Reading

ARCHIVE_CSV = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"

# Tonnage has ranged roughly 600–1400t over the fund's life. A parse landing
# outside this means we grabbed the wrong column.
PLAUSIBLE_TONNES = (200.0, 2000.0)


@collector("gld_holdings", category="etf_flows")
def collect() -> CollectorResult:
    response = get(ARCHIVE_CSV, timeout=30)
    rows = _parse(response.text)

    if not rows:
        raise CollectorError(
            "no usable rows in the GLD archive CSV (column layout has probably changed)"
        )

    result = CollectorResult(name="gld_holdings")
    (latest_date, latest_tonnes) = rows[0]

    extra: dict[str, object] = {"as_of": latest_date.date().isoformat()}

    if len(rows) > 1:
        prior_date, prior_tonnes = rows[1]
        delta = latest_tonnes - prior_tonnes
        extra["previous_tonnes"] = prior_tonnes
        extra["previous_date"] = prior_date.date().isoformat()
        extra["delta_tonnes"] = round(delta, 2)
        result.readings.append(
            Reading(
                metric="gld_tonnes_delta",
                value=round(delta, 2),
                unit="tonnes",
                source=f"SPDR Gold Shares archive, {prior_date.date()} to {latest_date.date()}",
                ts=latest_date,
                extra={"days_between": (latest_date - prior_date).days},
            )
        )

    # Five-session net gives the model a trend rather than one noisy print.
    if len(rows) >= 6:
        window_delta = latest_tonnes - rows[5][1]
        extra["delta_tonnes_5d"] = round(window_delta, 2)

    result.readings.append(
        Reading(
            metric="gld_tonnes",
            value=latest_tonnes,
            unit="tonnes",
            source=f"SPDR Gold Shares published holdings, {latest_date.date()}",
            ts=latest_date,
            extra=extra,
        )
    )

    age_days = (datetime.now(UTC).date() - latest_date.date()).days
    if age_days > 3:
        result.notes.append(
            f"GLD tonnage is as of {latest_date.date()} ({age_days} days old) — cite that date"
        )

    return result


def _parse(text: str) -> list[tuple[datetime, float]]:
    """Rows of (date, tonnes), newest first.

    The CSV carries a preamble and its headers move, so columns are located by
    name rather than by index.
    """
    reader = csv.reader(io.StringIO(text))
    header_index: dict[str, int] | None = None
    out: list[tuple[datetime, float]] = []

    for row in reader:
        if not row:
            continue

        if header_index is None:
            lowered = [cell.strip().lower() for cell in row]
            date_col = next((i for i, c in enumerate(lowered) if "date" in c), None)
            tonne_col = next(
                (i for i, c in enumerate(lowered) if "tonne" in c or "tonnes in the trust" in c),
                None,
            )
            if date_col is not None and tonne_col is not None:
                header_index = {"date": date_col, "tonnes": tonne_col}
            continue

        try:
            raw_date = row[header_index["date"]].strip()
            raw_tonnes = row[header_index["tonnes"]].strip()
        except IndexError:
            continue
        if not raw_date or not raw_tonnes:
            continue

        stamp = _parse_date(raw_date)
        if stamp is None:
            continue
        try:
            tonnes = to_float(raw_tonnes, field="GLD tonnes")
        except CollectorError:
            continue
        if not (PLAUSIBLE_TONNES[0] <= tonnes <= PLAUSIBLE_TONNES[1]):
            continue
        out.append((stamp, tonnes))

    if header_index is None:
        raise CollectorError("could not locate date and tonnage columns in the GLD CSV")

    out.sort(key=lambda pair: pair[0], reverse=True)
    return out


def _parse_date(raw: str) -> datetime | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
