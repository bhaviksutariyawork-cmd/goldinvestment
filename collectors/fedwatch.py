"""Implied rate-cut probability.

CME's FedWatch tool is a JavaScript application with no free documented API, and
the "free mirrors" that scrape it are unreliable and go dark without notice. So
rather than depend on one, this collector derives the implied rate from **30-day
Fed Funds futures** (`ZQ=F`) — the same instrument FedWatch itself is built on.

The arithmetic: a Fed Funds future settles at `100 - average effective funds rate`
over its contract month. So `100 - price` is the market-implied average rate for
that month. Compare it to the current effective rate and you get the expected
cut in basis points, which converts to an approximate probability of a 25bp move.

This is explicitly labelled as a derived estimate, not a FedWatch quote. It tracks
FedWatch closely but is not identical to it — FedWatch uses a more careful
mid-month meeting-date weighting. The brief must not call this a CME figure.
"""

from __future__ import annotations

from collectors._yahoo import quote
from collectors.base import CollectorError, collector
from goldagent.models import CollectorResult, Reading

FUTURES_SYMBOL = "ZQ=F"  # 30-day Fed Funds future, front month
STANDARD_MOVE_BP = 25.0


@collector("fedwatch", category="macro")
def collect(current_rate: float | None = None) -> CollectorResult:
    q = quote(FUTURES_SYMBOL, period="1mo", interval="1d")

    if not 90.0 <= q.last <= 100.5:
        raise CollectorError(
            f"{FUTURES_SYMBOL} priced at {q.last}, outside the plausible 90–100 band for a "
            f"Fed Funds future — wrong symbol or a bad print"
        )

    implied_rate = 100.0 - q.last
    result = CollectorResult(name="fedwatch")

    extra: dict[str, object] = {
        "futures_symbol": FUTURES_SYMBOL,
        "futures_price": q.last,
        "method": "100 - front-month ZQ price",
        "caveat": "derived from Fed Funds futures, not a CME FedWatch quote",
    }

    result.readings.append(
        Reading(
            metric="implied_policy_rate",
            value=round(implied_rate, 4),
            unit="%",
            source=f"derived from 30-day Fed Funds futures {FUTURES_SYMBOL} ({q.via})",
            ts=q.ts,
            extra=extra,
        )
    )

    if current_rate is not None:
        expected_cut_bp = (current_rate - implied_rate) * 100.0
        # Fraction of a standard 25bp move already priced, clamped to 0–100.
        probability = max(0.0, min(100.0, expected_cut_bp / STANDARD_MOVE_BP * 100.0))
        result.readings.append(
            Reading(
                metric="implied_cut_probability_pct",
                value=round(probability, 1),
                unit="%",
                source=(
                    f"derived: front-month {FUTURES_SYMBOL} implies {implied_rate:.3f}% vs "
                    f"effective {current_rate:.3f}%, i.e. {expected_cut_bp:.1f}bp priced"
                ),
                ts=q.ts,
                extra={
                    "expected_cut_bp": round(expected_cut_bp, 1),
                    "effective_rate": current_rate,
                    "implied_rate": round(implied_rate, 4),
                    "standard_move_bp": STANDARD_MOVE_BP,
                    "caveat": (
                        "approximation: share of one 25bp move priced into the front-month "
                        "contract. CME FedWatch weights meeting dates within the month and "
                        "will differ by a few points."
                    ),
                },
            )
        )
        result.notes.append(
            f"Rate-cut odds are DERIVED from {FUTURES_SYMBOL}, not read from CME FedWatch. "
            f"Describe them as futures-implied, not as a FedWatch figure."
        )
    else:
        result.notes.append(
            "Effective funds rate unavailable (FRED leg failed), so only the implied "
            "policy rate is reported — no cut probability was computed."
        )

    return result
