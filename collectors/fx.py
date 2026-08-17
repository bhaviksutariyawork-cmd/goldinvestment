"""USD/INR (`USDINR=X`).

This is the leg that makes the reader's purchase price diverge from the headline
USD number. A falling USD gold price and a weakening rupee can leave his cost
basis flat or higher, and that divergence is the thing he needs told.
"""

from __future__ import annotations

from collectors._yahoo import quote
from collectors.base import collector
from goldagent.models import CollectorResult, Reading

SYMBOL = "USDINR=X"


@collector("fx", category="fx")
def collect() -> CollectorResult:
    q = quote(SYMBOL)
    result = CollectorResult(name="fx")
    result.readings.append(
        Reading(
            metric="usdinr",
            value=q.last,
            unit="INR per USD",
            source=f"Yahoo Finance {SYMBOL} ({q.via})",
            ts=q.ts,
            extra={"previous_close": q.previous_close, "change_pct": q.change_pct},
        )
    )
    return result
