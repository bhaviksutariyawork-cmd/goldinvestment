"""Indian gold ETF NAVs, from the AMFI daily NAV file.

AMFI publishes every scheme's NAV as a pipe-delimited text file. It is large
(several MB) and streamed line by line here rather than loaded whole.

NAV is struck once daily on the previous close, so an intraday premium or discount
to live spot cannot be computed from it — a NAV/spot gap during market hours is
mostly a timing artefact, not a tradeable premium. What this does give you is the
cross-scheme spread: when one fund's NAV moves materially differently from its
peers on the same day, that difference is real.
"""

from __future__ import annotations

import statistics

from collectors.base import CollectorError, collector, session, to_float
from goldagent.models import CollectorResult, Reading

NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

# Match gold ETFs while excluding fund-of-funds and multi-asset schemes, whose
# NAVs answer a different question.
INCLUDE_TERMS = ("gold",)
EXCLUDE_TERMS = (
    "fund of fund", "fof", "multi asset", "multi-asset", "silver",
    "equity", "savings", "hybrid", "balanced",
)

PLAUSIBLE_NAV = (5.0, 2000.0)
MAX_LINES = 60_000


@collector("india_etf", category="india")
def collect() -> CollectorResult:
    schemes = _fetch_gold_navs()

    if not schemes:
        raise CollectorError(
            "no gold ETF schemes found in the AMFI NAV file (format or naming may have changed)"
        )

    navs = [nav for _, nav, _ in schemes]
    median_nav = statistics.median(navs)

    result = CollectorResult(name="india_etf")
    result.readings.append(
        Reading(
            metric="india_gold_etf_median_nav",
            value=round(median_nav, 4),
            unit="INR/unit",
            source=f"AMFI daily NAV file, {len(schemes)} gold ETF schemes",
            extra={
                "scheme_count": len(schemes),
                "min_nav": round(min(navs), 4),
                "max_nav": round(max(navs), 4),
                # Units per scheme differ, so the level spread is not comparable
                # across funds. Reported for context only.
                "sample": [
                    {"scheme": name[:70], "nav": nav, "date": nav_date}
                    for name, nav, nav_date in schemes[:6]
                ],
            },
        )
    )

    dates = {nav_date for _, _, nav_date in schemes if nav_date}
    if dates:
        result.notes.append(
            "Gold ETF NAVs are as of "
            + ", ".join(sorted(dates)[:3])
            + " (struck on the previous close, so any gap to live spot is a timing "
            "artefact rather than a premium)."
        )

    return result


def _fetch_gold_navs() -> list[tuple[str, float, str]]:
    """Stream the AMFI file, keeping gold ETF rows only.

    Format: `Scheme Code;ISIN Div Payout;ISIN Reinvestment;Scheme Name;NAV;Date`
    with section headers interleaved.
    """
    out: list[tuple[str, float, str]] = []
    response = session().get(NAV_URL, timeout=45, stream=True)
    response.raise_for_status()

    try:
        for index, raw_line in enumerate(response.iter_lines(decode_unicode=True)):
            if index > MAX_LINES:
                break
            if not raw_line or ";" not in raw_line:
                continue
            fields = [field.strip() for field in raw_line.split(";")]
            if len(fields) < 6:
                continue

            scheme_name = fields[3]
            lowered = scheme_name.lower()
            if not any(term in lowered for term in INCLUDE_TERMS):
                continue
            if any(term in lowered for term in EXCLUDE_TERMS):
                continue
            # ETFs specifically, not open-ended gold funds.
            if "etf" not in lowered and "exchange traded" not in lowered:
                continue

            try:
                nav = to_float(fields[4], field=f"NAV for {scheme_name[:40]}")
            except CollectorError:
                continue
            if not (PLAUSIBLE_NAV[0] <= nav <= PLAUSIBLE_NAV[1]):
                continue

            out.append((scheme_name, nav, fields[5]))
    finally:
        response.close()

    return out
