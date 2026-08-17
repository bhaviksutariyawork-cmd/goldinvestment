"""Silver (`SI=F`).

Carried for the gold/silver ratio, which is computed downstream in
`goldagent.collect` once both legs are in hand. A rising ratio means gold is
outperforming silver, which usually reads as a flight to monetary metal rather
than an industrial-demand story.
"""

from __future__ import annotations

from collectors._yahoo import quote
from collectors.base import collector
from goldagent.models import CollectorResult, Reading

SYMBOL = "SI=F"


@collector("silver", category="price")
def collect() -> CollectorResult:
    q = quote(SYMBOL)
    result = CollectorResult(name="silver")
    result.readings.append(
        Reading(
            metric="silver_usd_oz",
            value=q.last,
            unit="USD/oz",
            source=f"COMEX silver front-month via Yahoo Finance {SYMBOL} ({q.via})",
            ts=q.ts,
            extra={"previous_close": q.previous_close, "change_pct": q.change_pct},
        )
    )
    return result
