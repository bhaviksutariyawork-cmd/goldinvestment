"""Yahoo Finance quotes.

yfinance is the primary path, as specified. It breaks periodically — rate limits,
upstream schema changes, pandas incompatibilities — so there is a direct call to
Yahoo's chart JSON endpoint as a fallback. Whichever path produced the number is
recorded on the quote, and the brief cites it.

Both paths are private to this module so collectors stay declarative and the tests
have exactly one seam to mock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from collectors.base import CollectorError, get_json, to_float

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(slots=True)
class Quote:
    symbol: str
    last: float
    previous_close: float | None
    ts: datetime
    via: str  # "yfinance" or "yahoo-chart"

    @property
    def change_pct(self) -> float | None:
        if not self.previous_close:
            return None
        return (self.last / self.previous_close - 1.0) * 100.0


def quote(symbol: str, *, period: str = "5d", interval: str = "1h") -> Quote:
    """Latest price for a Yahoo symbol, with previous close where available."""
    try:
        return _via_yfinance(symbol, period=period, interval=interval)
    except Exception as exc:  # noqa: BLE001 — fall through to the HTTP path
        log.info(
            "yfinance path failed, falling back to chart API",
            extra={"symbol": symbol, "error": str(exc)[:200]},
        )
    return _via_chart_api(symbol, period=period, interval=interval)


def _via_yfinance(symbol: str, *, period: str, interval: str) -> Quote:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if frame is None or frame.empty:
        raise CollectorError(f"yfinance returned no rows for {symbol}")

    closes = frame["Close"].dropna()
    if closes.empty:
        raise CollectorError(f"yfinance returned no closing prices for {symbol}")

    last = to_float(closes.iloc[-1], field=f"{symbol} close")
    previous = to_float(closes.iloc[-2], field=f"{symbol} prior close") if len(closes) > 1 else None

    stamp = closes.index[-1]
    ts = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    return Quote(symbol=symbol, last=last, previous_close=previous, ts=ts, via="yfinance")


def _via_chart_api(symbol: str, *, period: str, interval: str) -> Quote:
    payload = get_json(
        CHART_URL.format(symbol=symbol),
        params={"range": period, "interval": interval, "includePrePost": "false"},
    )
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise CollectorError(f"Yahoo chart API error for {symbol}: {chart['error']}")

    results = chart.get("result") or []
    if not results:
        raise CollectorError(f"Yahoo chart API returned no result for {symbol}")

    result = results[0]
    meta = result.get("meta") or {}
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quotes.get("close") or []) if c is not None]

    if closes:
        last = to_float(closes[-1], field=f"{symbol} close")
        previous = to_float(closes[-2], field=f"{symbol} prior close") if len(closes) > 1 else None
    elif meta.get("regularMarketPrice") is not None:
        last = to_float(meta["regularMarketPrice"], field=f"{symbol} market price")
        previous = None
    else:
        raise CollectorError(f"no usable price in Yahoo chart response for {symbol}")

    # Prefer the meta previous close — it is the official session close rather
    # than whatever the second-to-last bar happened to be.
    if meta.get("chartPreviousClose") is not None:
        previous = to_float(meta["chartPreviousClose"], field=f"{symbol} previous close")

    stamps = result.get("timestamp") or []
    ts = datetime.fromtimestamp(stamps[-1], tz=UTC) if stamps else datetime.now(UTC)

    return Quote(symbol=symbol, last=last, previous_close=previous, ts=ts, via="yahoo-chart")
