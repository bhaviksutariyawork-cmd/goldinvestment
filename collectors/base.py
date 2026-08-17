"""The collector contract.

A collector's one hard obligation: **never raise**. A dead API, a changed page
layout, a TLS error, a malformed float — all of it degrades to a `CollectorResult`
with `stale=True` and an error string. The pipeline reports that gap in the brief.
It does not fill it from memory, and it does not die.

The `@collector` decorator enforces this. Write the happy path inside the function
and let the decorator catch everything.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, Literal

import requests

from goldagent.models import CollectorResult

log = logging.getLogger(__name__)

# Which staleness budget in config.data_quality.staleness_hours applies.
Category = Literal["price", "fx", "macro", "etf_flows", "cot", "central_bank", "news", "india"]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 goldagent/1.0"
)
DEFAULT_TIMEOUT = 20


class CollectorError(RuntimeError):
    """Raised inside a collector for an expected, describable failure.

    Use this for "the page loaded but the number wasn't there" — the message goes
    straight into the brief, so write it for a reader, not a stack trace.
    """


def collector(
    name: str,
    category: Category = "price",
) -> Callable[[Callable[..., CollectorResult]], Callable[..., CollectorResult]]:
    """Wrap a collect function so it always returns a well-formed result."""

    def decorator(fn: Callable[..., CollectorResult]) -> Callable[..., CollectorResult]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> CollectorResult:
            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                if not isinstance(result, CollectorResult):
                    raise CollectorError(
                        f"collector returned {type(result).__name__}, expected CollectorResult"
                    )
                result.name = name
                result.duration_ms = int((time.monotonic() - start) * 1000)
                # A result with no readings and no headlines and no notes did not
                # actually collect anything, whatever it claims.
                if not (result.readings or result.headlines or result.notes) and not result.stale:
                    result.stale = True
                    result.error = "collector produced no data"
                log.info(
                    "collector finished",
                    extra={
                        "collector": name,
                        "category": category,
                        "ok": result.ok,
                        "readings": len(result.readings),
                        "headlines": len(result.headlines),
                        "duration_ms": result.duration_ms,
                        "error": result.error,
                    },
                )
                return result
            except Exception as exc:  # noqa: BLE001 — the whole point is to catch everything
                duration_ms = int((time.monotonic() - start) * 1000)
                message = _describe(exc)
                log.warning(
                    "collector failed",
                    extra={
                        "collector": name,
                        "category": category,
                        "duration_ms": duration_ms,
                        "error": message,
                        "exc_type": type(exc).__name__,
                    },
                    exc_info=not isinstance(exc, CollectorError | requests.RequestException),
                )
                return CollectorResult(
                    name=name,
                    stale=True,
                    error=message,
                    duration_ms=duration_ms,
                )

        wrapper.collector_name = name  # type: ignore[attr-defined]
        wrapper.collector_category = category  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _describe(exc: Exception) -> str:
    """Turn an exception into something worth printing in a briefing."""
    if isinstance(exc, requests.Timeout):
        return "request timed out"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect"
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", "?")
        return f"HTTP {status}"
    if isinstance(exc, CollectorError):
        return str(exc)
    text = str(exc).strip() or exc.__class__.__name__
    return f"{type(exc).__name__}: {text[:200]}"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

_session: requests.Session | None = None


def session() -> requests.Session:
    """Shared session with a browser UA — several sources 403 the default one."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        _session = s
    return _session


def get(url: str, *, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    """GET with a timeout that is never optional, raising on 4xx/5xx."""
    response = session().get(url, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response


def get_json(url: str, *, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> Any:
    response = get(url, timeout=timeout, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise CollectorError(f"response was not JSON ({response.text[:80]!r})") from exc


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def to_float(raw: Any, *, field: str = "value") -> float:
    """Parse a number out of scraped text, tolerating commas, currency and units.

    Raises CollectorError rather than ValueError so the message reaches the brief.
    """
    if raw is None:
        raise CollectorError(f"{field} was absent")
    if isinstance(raw, int | float):
        value = float(raw)
        if value != value:  # NaN
            raise CollectorError(f"{field} was NaN")
        return value

    text = str(raw).strip()
    if not text:
        raise CollectorError(f"{field} was empty")
    cleaned = (
        text.replace(",", "")
        .replace("₹", "")
        .replace("$", "")
        .replace("%", "")
        .replace("\xa0", "")
        .strip()
    )
    # Strip a trailing unit token, e.g. "3401.20 USD" or "812.4 tonnes".
    parts = cleaned.split()
    if len(parts) > 1:
        cleaned = parts[0]
    try:
        return float(cleaned)
    except ValueError as exc:
        raise CollectorError(f"could not read {field} from {text[:60]!r}") from exc


def pct_change(current: float, previous: float) -> float | None:
    """Percent change, or None when the base is unusable."""
    if previous is None or current is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def domain_of(url: str) -> str:
    """Registrable-ish domain for source-independence counting.

    Strips a leading `www.` and any subdomain beyond the last two labels, so
    `feeds.reuters.com` and `www.reuters.com` both count as `reuters.com`. Good
    enough for de-duplicating wire syndication; not a public-suffix parser, so
    `bbc.co.uk` keeps three labels via the explicit two-part TLD list.
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return ""
    labels = host.split(".")
    two_part_tlds = {"co.uk", "com.au", "co.in", "co.jp", "com.br", "co.za", "org.uk"}
    if len(labels) >= 3 and ".".join(labels[-2:]) in two_part_tlds:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    """Which keywords appear in the text, case-insensitively.

    Multi-word keywords are matched as substrings; single words are matched on
    token boundaries so "strike" does not fire on "striking" or "airstrike"
    (the latter is its own keyword).
    """
    import re

    lowered = text.lower()
    hits = []
    for keyword in keywords:
        kw = keyword.lower().strip()
        if not kw:
            continue
        if " " in kw:
            if kw in lowered:
                hits.append(kw)
        elif re.search(rf"\b{re.escape(kw)}\b", lowered):
            hits.append(kw)
    return hits
