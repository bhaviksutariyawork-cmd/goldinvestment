"""US dollar index (ICE, `DX-Y.NYB`).

Second-order driver but fast-acting. The useful signal is usually not the level
but whether gold is moving with the dollar or against it — divergence between the
two is information in itself.
"""

from __future__ import annotations

from collectors._yahoo import quote
from collectors.base import collector
from goldagent.models import CollectorResult, Reading

SYMBOL = "DX-Y.NYB"


@collector("dollar_index", category="fx")
def collect() -> CollectorResult:
    q = quote(SYMBOL)
    result = CollectorResult(name="dollar_index")
    result.readings.append(
        Reading(
            metric="dxy",
            value=q.last,
            unit="index",
            source=f"ICE US Dollar Index via Yahoo Finance {SYMBOL} ({q.via})",
            ts=q.ts,
            extra={"previous_close": q.previous_close, "change_pct": q.change_pct},
        )
    )
    return result
