"""Gold spot in USD, from two independent Yahoo symbols.

`GC=F` is the COMEX front-month future; `XAUUSD=X` is the spot cross. They should
track closely. A gap wider than `data_quality.spot_divergence_pct` usually means
one feed is stale or the future is carrying an unusual basis — either way the
reader should know the two sources disagree rather than be handed one number.

If only one symbol resolves, that is reported as the price with a note saying the
cross-check was unavailable. The brief must not present a single unverified feed
as a confirmed reading.
"""

from __future__ import annotations

from collectors._yahoo import quote
from collectors.base import CollectorError, collector
from goldagent.models import CollectorResult, Reading

SPOT_SYMBOL = "XAUUSD=X"
FUTURE_SYMBOL = "GC=F"


@collector("price_spot", category="price")
def collect(divergence_pct: float = 0.3) -> CollectorResult:
    result = CollectorResult(name="price_spot")

    quotes = {}
    errors = {}
    for symbol in (SPOT_SYMBOL, FUTURE_SYMBOL):
        try:
            quotes[symbol] = quote(symbol)
        except Exception as exc:  # noqa: BLE001 — one dead symbol must not kill the other
            errors[symbol] = str(exc)[:160]

    if not quotes:
        raise CollectorError(
            "both gold symbols failed: "
            + "; ".join(f"{sym} ({msg})" for sym, msg in errors.items())
        )

    for symbol, q in quotes.items():
        metric = "gold_usd_oz" if symbol == SPOT_SYMBOL else "gold_future_usd_oz"
        result.readings.append(
            Reading(
                metric=metric,
                value=q.last,
                unit="USD/oz",
                source=f"Yahoo Finance {symbol} (via {q.via})",
                ts=q.ts,
                extra={
                    "symbol": symbol,
                    "previous_close": q.previous_close,
                    "change_pct": q.change_pct,
                },
            )
        )

    for symbol, message in errors.items():
        result.notes.append(f"{symbol} unavailable ({message}); no cross-check on that leg")

    # Cross-check the two legs.
    spot = quotes.get(SPOT_SYMBOL)
    future = quotes.get(FUTURE_SYMBOL)
    if spot and future:
        gap_pct = abs(future.last / spot.last - 1.0) * 100.0
        result.readings.append(
            Reading(
                metric="gold_spot_future_divergence_pct",
                value=gap_pct,
                unit="%",
                source="derived from XAUUSD=X and GC=F",
                extra={"threshold_pct": divergence_pct},
            )
        )
        if gap_pct > divergence_pct:
            result.notes.append(
                f"DIVERGENCE: GC=F ({future.last:.2f}) and XAUUSD=X ({spot.last:.2f}) "
                f"differ by {gap_pct:.2f}%, above the {divergence_pct}% tolerance. "
                f"Treat the USD price as uncertain and say so."
            )
    else:
        present = SPOT_SYMBOL if spot else FUTURE_SYMBOL
        result.notes.append(
            f"only {present} resolved this run — the two-source cross-check did not run"
        )

    return result
