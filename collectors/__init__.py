"""Data collectors.

Every module here exposes a `collect(...)` callable decorated with
`@collector(...)` from `collectors.base`. The decorator guarantees the callable
never raises: any exception becomes a `CollectorResult` with `stale=True` and the
error text attached, which the briefing reports explicitly.

`REGISTRY` is the ordered list the pipeline runs. Adding a collector means adding
its module here.
"""

from __future__ import annotations

from collectors import (
    central_banks,
    cot,
    dollar_index,
    fedwatch,
    fred,
    fx,
    gdelt,
    gld_holdings,
    india_etf,
    india_policy,
    price_india,
    price_spot,
    rss,
    sgb,
    silver,
    us_calendar,
)

# Order matters only for log readability; collectors run concurrently.
REGISTRY = (
    price_spot.collect,
    price_india.collect,
    fx.collect,
    dollar_index.collect,
    silver.collect,
    fred.collect,
    fedwatch.collect,
    us_calendar.collect,
    gld_holdings.collect,
    cot.collect,
    central_banks.collect,
    gdelt.collect,
    rss.collect,
    india_policy.collect,
    india_etf.collect,
    sgb.collect,
)

__all__ = ["REGISTRY"]
