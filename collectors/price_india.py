"""India physical gold reference: IBJA daily rates and MCX front-month.

Both of these are scraped, and scraping is the most fragile thing in this repo.
Neither site publishes a documented API, both change layout without notice, and
IBJA rate-limits aggressively. Expect this collector to need maintenance.

That is why the brief's INR number does not depend on it. `goldagent.collect`
derives INR per 10g from USD spot, USD/INR and the duty stack — arithmetic that
works whenever the price and FX legs work. IBJA and MCX are the *physical
cross-check* against that derivation: when they resolve, a gap between the derived
and quoted price is real information about local premium or discount. When they
fail, the brief says the physical reference was unavailable and leans on the
derived figure. It never silently substitutes one for the other.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from collectors.base import CollectorError, collector, get, to_float
from goldagent.models import CollectorResult, Reading

IBJA_URL = "https://ibjarates.com/"
MCX_QUOTE_URL = "https://www.mcxindia.com/backpage.aspx/GetQuote"
MCX_MARKETWATCH_URL = "https://www.mcxindia.com/backpage.aspx/GetMarketWatch"

# IBJA quotes per 10 grams. A plausible band guards against parsing a volume,
# a date fragment or a percentage as if it were a price.
PLAUSIBLE_INR_PER_10G = (40_000.0, 400_000.0)


@collector("price_india", category="india")
def collect(purity: int = 995) -> CollectorResult:
    result = CollectorResult(name="price_india")
    problems: list[str] = []

    try:
        rate, matched_purity = _ibja_rate(purity)
        result.readings.append(
            Reading(
                metric="ibja_inr_per_10g",
                value=rate,
                unit="INR/10g",
                source=f"IBJA published rate, {matched_purity} fine",
                extra={"purity": matched_purity, "requested_purity": purity},
            )
        )
        if matched_purity != purity:
            result.notes.append(
                f"IBJA {purity} rate not found; used {matched_purity} fine instead"
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"IBJA scrape failed ({str(exc)[:140]})")

    try:
        price, contract = _mcx_gold()
        result.readings.append(
            Reading(
                metric="mcx_gold_inr_per_10g",
                value=price,
                unit="INR/10g",
                source=f"MCX Gold front-month ({contract})",
                extra={"contract": contract},
            )
        )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"MCX scrape failed ({str(exc)[:140]})")

    for problem in problems:
        result.notes.append(problem)

    if not result.readings:
        result.notes.append(
            "No physical India reference this run — the INR figure in this brief is "
            "derived from USD spot, USD/INR and the configured duty stack. Say so."
        )
        # Deliberately not stale: the absence itself is the reportable fact, and
        # the derived INR price downstream does not depend on this collector.
        result.notes.append("SCRAPE STATUS: both IBJA and MCX unavailable")

    return result


def _ibja_rate(purity: int) -> tuple[float, int]:
    """Pull a per-10g rate out of the IBJA rates page.

    Strategy: find the table row whose text mentions the purity, then take the
    first number in that row inside a plausible price band. Falls back through
    other common purities so a missing 995 row still yields something usable.
    """
    response = get(IBJA_URL, timeout=25)
    soup = BeautifulSoup(response.text, "lxml")

    for candidate in _purity_order(purity):
        rate = _rate_for_purity(soup, candidate)
        if rate is not None:
            return rate, candidate

    raise CollectorError(
        "no plausible per-10g rate found on the IBJA page for any known purity "
        "(layout has probably changed)"
    )


def _purity_order(preferred: int) -> list[int]:
    order = [preferred]
    order += [p for p in (995, 999, 916, 750) if p != preferred]
    return order


def _rate_for_purity(soup: BeautifulSoup, purity: int) -> float | None:
    needle = str(purity)
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        if needle not in text:
            continue
        for token in re.findall(r"\d[\d,]*\.?\d*", text):
            # Skip the purity marker itself.
            if token.replace(",", "") == needle:
                continue
            try:
                value = to_float(token, field=f"IBJA {purity} rate")
            except CollectorError:
                continue
            if PLAUSIBLE_INR_PER_10G[0] <= value <= PLAUSIBLE_INR_PER_10G[1]:
                return value
    return None


def _mcx_gold() -> tuple[float, str]:
    """Front-month MCX gold.

    MCX serves its market watch from an ASP.NET endpoint that expects a JSON POST
    and a browser-ish referer. It is undocumented and blocks often.
    """
    response = get(
        MCX_MARKETWATCH_URL,
        timeout=25,
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Referer": "https://www.mcxindia.com/market-data/market-watch",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise CollectorError("MCX did not return JSON (endpoint or block page changed)") from exc

    rows = payload.get("d") or payload.get("data") or []
    if isinstance(rows, str):
        import json

        try:
            rows = json.loads(rows)
        except json.JSONDecodeError as exc:
            raise CollectorError("MCX returned an unparseable payload") from exc

    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("Symbol") or row.get("symbol") or "")
        if symbol.upper() != "GOLD":
            continue
        raw_price = (
            row.get("LTP")
            or row.get("LastTradedPrice")
            or row.get("ltp")
            or row.get("ClosePrice")
        )
        expiry = str(row.get("ExpiryDate") or row.get("expiryDate") or "front-month")
        try:
            price = to_float(raw_price, field="MCX GOLD price")
        except CollectorError:
            continue
        if PLAUSIBLE_INR_PER_10G[0] <= price <= PLAUSIBLE_INR_PER_10G[1]:
            candidates.append((expiry, price))

    if not candidates:
        raise CollectorError("no GOLD contract with a plausible price in the MCX market watch")

    # Nearest expiry first where the date is parseable; otherwise take the first.
    candidates.sort(key=lambda pair: pair[0])
    expiry, price = candidates[0]
    return price, expiry
