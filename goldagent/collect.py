"""Run every collector, derive the cross-currency figures, persist the result.

Collectors run concurrently. None of them can raise — that is enforced by the
decorator in `collectors.base` — so this module's error handling is about the
executor itself: a collector that hangs past its deadline is abandoned and
reported as unavailable rather than allowed to stall the run.

The derived metrics computed here are the ones that need more than one collector:

* **gold/silver ratio** — needs both metals.
* **INR per 10g** — needs USD spot, USD/INR, and the duty stack. This is the
  number the reader actually pays against, and it is computed arithmetically so it
  is available whenever the price and FX legs work, independent of whether the
  IBJA and MCX scrapers are up.
* **physical premium** — the gap between the quoted IBJA price and the derived
  landed cost, which is real local-market information when both are present.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, date, datetime, timedelta

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
from goldagent.config import Config
from goldagent.models import CollectorResult, Reading, Snapshot

log = logging.getLogger(__name__)

TROY_OUNCE_GRAMS = 31.1034768

# Per-collector wall-clock budget. A collector past this is abandoned; the thread
# may linger but the run continues and the process exits at the end.
COLLECTOR_DEADLINE_SECONDS = 75
MAX_WORKERS = 8


def run(config: Config, secrets_fred_key: str | None = None) -> Snapshot:
    """Collect everything and return a snapshot with derived metrics attached."""
    snapshot = Snapshot()

    conflict = config.alerts.triggers.conflict_escalation
    news_keywords = list(conflict.keywords)

    # (name, callable, kwargs) — kwargs come from config so collectors stay
    # ignorant of the config object.
    jobs: list[tuple[str, object, dict]] = [
        ("price_spot", price_spot.collect, {"divergence_pct": config.data_quality.spot_divergence_pct}),
        ("price_india", price_india.collect, {"purity": config.investor.purity_reference}),
        ("fx", fx.collect, {}),
        ("dollar_index", dollar_index.collect, {}),
        ("silver", silver.collect, {}),
        ("fred", fred.collect, {"api_key": secrets_fred_key}),
        ("us_calendar", us_calendar.collect, {}),
        ("gld_holdings", gld_holdings.collect, {}),
        ("cot", cot.collect, {}),
        ("central_banks", central_banks.collect, {}),
        ("gdelt", gdelt.collect, {"keywords": news_keywords}),
        ("rss", rss.collect, {"keywords": news_keywords}),
        ("india_policy", india_policy.collect, {}),
        ("india_etf", india_etf.collect, {}),
        ("sgb", sgb.collect, {"check_enabled": config.india.sovereign_gold_bond.check_enabled}),
    ]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="collect") as pool:
        futures: dict[str, Future] = {
            name: pool.submit(fn, **kwargs)  # type: ignore[misc]
            for name, fn, kwargs in jobs
        }
        for name, future in futures.items():
            snapshot.results[name] = _await(name, future)

    # FedWatch depends on the effective funds rate from FRED, so it runs after the
    # rest rather than inside the pool. Called directly: its decorator already
    # guarantees no exception escapes, and its one HTTP call carries its own timeout.
    effective_rate = snapshot.value("fed_funds_rate")
    snapshot.results["fedwatch"] = fedwatch.collect(current_rate=effective_rate)

    _derive(snapshot, config)

    log.info(
        "collection complete",
        extra={
            "collectors": len(snapshot.results),
            "ok": snapshot.ok_count,
            "failed": ",".join(snapshot.failed) or None,
            "readings": len(snapshot.all_readings()),
            "headlines": len(snapshot.all_headlines()),
        },
    )
    return snapshot


def _await(name: str, future: Future) -> CollectorResult:
    """Resolve a collector future, converting a hang into a reported gap."""
    try:
        return future.result(timeout=COLLECTOR_DEADLINE_SECONDS)
    except FutureTimeout:
        future.cancel()
        log.warning("collector exceeded deadline", extra={"collector": name})
        return CollectorResult(
            name=name,
            stale=True,
            error=f"exceeded the {COLLECTOR_DEADLINE_SECONDS}s deadline and was abandoned",
        )
    except Exception as exc:  # noqa: BLE001 — belt and braces; the decorator should prevent this
        log.error("collector raised past its decorator", extra={"collector": name}, exc_info=True)
        return CollectorResult(name=name, stale=True, error=f"unexpected: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Derived metrics
# --------------------------------------------------------------------------- #


def _derive(snapshot: Snapshot, config: Config) -> None:
    """Attach cross-collector metrics as a synthetic `derived` result."""
    derived = CollectorResult(name="derived")

    gold = snapshot.value("gold_usd_oz") or snapshot.value("gold_future_usd_oz")
    silver_px = snapshot.value("silver_usd_oz")
    usdinr = snapshot.value("usdinr")

    if gold and silver_px:
        derived.readings.append(
            Reading(
                metric="gold_silver_ratio",
                value=round(gold / silver_px, 3),
                unit="ratio",
                source="derived from XAUUSD=X and SI=F",
                extra={"gold_usd_oz": gold, "silver_usd_oz": silver_px},
            )
        )

    if gold and usdinr:
        duty = config.india.duty_stack_fallback
        pure_inr_per_10g = gold * usdinr / TROY_OUNCE_GRAMS * 10.0

        # Two figures, because they answer different questions and are not
        # interchangeable:
        #
        #   ex_gst  — import value plus basic duty and AIDC cess. This is the basis
        #             IBJA publishes on, so it is the only correct comparison for
        #             the physical premium below.
        #   all_in  — ex_gst plus GST. This is what the reader actually pays at the
        #             counter, so it is the headline INR number in the brief.
        #
        # Comparing a quoted IBJA rate against the all-in figure would show a
        # spurious discount roughly the size of the GST rate.
        ex_gst_multiplier = 1.0 + (duty.import_duty_pct + duty.agri_infra_cess_pct) / 100.0
        ex_gst = pure_inr_per_10g * ex_gst_multiplier
        all_in = pure_inr_per_10g * duty.landed_multiplier

        derived.readings.append(
            Reading(
                metric="gold_inr_per_10g",
                value=round(all_in, 2),
                unit="INR/10g",
                source=(
                    f"derived: USD spot {gold:.2f}/oz x USD/INR {usdinr:.4f} "
                    f"converted to 10g, plus the configured duty stack "
                    f"({duty.effective_pct:.2f}% compounded, as of {duty.as_of})"
                ),
                extra={
                    "pre_duty_inr_per_10g": round(pure_inr_per_10g, 2),
                    "ex_gst_inr_per_10g": round(ex_gst, 2),
                    "duty_effective_pct": round(duty.effective_pct, 3),
                    "import_duty_pct": duty.import_duty_pct,
                    "agri_infra_cess_pct": duty.agri_infra_cess_pct,
                    "gst_pct": duty.gst_pct,
                    "duty_as_of": duty.as_of.isoformat(),
                    "basis": "derived all-in (duty + cess + GST), not a market quote",
                },
            )
        )
        derived.readings.append(
            Reading(
                metric="gold_inr_per_10g_ex_gst",
                value=round(ex_gst, 2),
                unit="INR/10g",
                source=(
                    f"derived: import value plus {duty.import_duty_pct}% duty and "
                    f"{duty.agri_infra_cess_pct}% AIDC cess, before GST — the IBJA basis"
                ),
                extra={"gst_excluded_pct": duty.gst_pct},
            )
        )
        derived.notes.append(
            f"INR/10g is DERIVED arithmetic (USD spot x USD/INR x duty stack, "
            f"{duty.effective_pct:.2f}% all-in as of {duty.as_of}), not a dealer quote. "
            f"Duty figures are the configured fallback — if the policy collector flagged "
            f"a change, this price is stale until config is updated."
        )

        # Physical premium, compared on the ex-GST basis IBJA actually quotes.
        ibja = snapshot.value("ibja_inr_per_10g")
        if ibja:
            premium_pct = (ibja / ex_gst - 1.0) * 100.0
            derived.readings.append(
                Reading(
                    metric="india_physical_premium_pct",
                    value=round(premium_pct, 3),
                    unit="%",
                    source=(
                        "derived: IBJA quoted rate vs computed landed cost, both on the "
                        "ex-GST basis IBJA publishes"
                    ),
                    extra={
                        "ibja_inr_per_10g": ibja,
                        "derived_ex_gst_inr_per_10g": round(ex_gst, 2),
                        "comparison_basis": "ex-GST",
                        "caveat": (
                            "IBJA quotes exclude GST and reflect a purity-specific domestic "
                            "rate, so a residual gap of a percent or two is normal and is not "
                            "necessarily a tradeable premium"
                        ),
                    },
                )
            )
            if abs(premium_pct) > 4.0:
                derived.notes.append(
                    f"IBJA quote is {premium_pct:+.1f}% against the derived ex-GST landed "
                    f"cost. A gap this wide usually means the configured duty stack is out "
                    f"of date rather than a real local premium — treat both INR figures as "
                    f"suspect and say so."
                )

    # Direction agreement between the USD leg and the reader's INR leg — the
    # divergence the analyst prompt says to lead with when it happens.
    usd_change = _change_pct(snapshot, "gold_usd_oz")
    inr_change = _fx_adjusted_change(snapshot)
    if usd_change is not None and inr_change is not None:
        diverging = (usd_change > 0) != (inr_change > 0)
        derived.readings.append(
            Reading(
                metric="usd_inr_direction_divergence",
                value=1.0 if diverging else 0.0,
                unit="boolean",
                source="derived from USD spot change and USD/INR change",
                extra={
                    "usd_gold_change_pct": round(usd_change, 3),
                    "inr_gold_change_pct": round(inr_change, 3),
                },
            )
        )
        if diverging:
            derived.notes.append(
                f"DIVERGENCE: USD gold {usd_change:+.2f}% but INR gold {inr_change:+.2f}% "
                f"on the session. This is the headline, not a footnote."
            )

    seasonality = _seasonality_note(config)
    if seasonality:
        derived.notes.append(seasonality)

    derived.duration_ms = 0
    snapshot.results["derived"] = derived


def _change_pct(snapshot: Snapshot, metric: str) -> float | None:
    """Session change from a collector's `previous_close` extra, if present."""
    for result in snapshot.results.values():
        reading = result.reading(metric)
        if reading and reading.extra.get("change_pct") is not None:
            return float(reading.extra["change_pct"])
    return None


def _fx_adjusted_change(snapshot: Snapshot) -> float | None:
    """Session change in INR terms: compounding the USD move and the FX move.

    Duty is a constant multiplier and drops out of a percentage change, so it is
    correctly absent here.
    """
    usd = _change_pct(snapshot, "gold_usd_oz")
    fx_change = _change_pct(snapshot, "usdinr")
    if usd is None or fx_change is None:
        return None
    return ((1 + usd / 100.0) * (1 + fx_change / 100.0) - 1.0) * 100.0


def _seasonality_note(config: Config, today: date | None = None) -> str | None:
    """Flag an approaching festival or wedding-season window."""
    season = config.india.seasonality
    if not season.enabled or not season.events:
        return None

    today = today or datetime.now(config.tz).date()
    horizon = today + timedelta(days=season.lead_days)
    flags: list[str] = []

    for event in season.events:
        if event.date is not None:
            if today <= event.date <= horizon:
                flags.append(f"{event.name} in {(event.date - today).days}d ({event.date})")
        elif event.start is not None and event.end is not None:
            if event.start <= today <= event.end:
                flags.append(f"{event.name} is currently active (to {event.end})")
            elif today <= event.start <= horizon:
                flags.append(f"{event.name} starts in {(event.start - today).days}d ({event.start})")

    if not flags:
        return None
    return "India seasonal demand: " + "; ".join(flags)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def persist(conn, snapshot: Snapshot) -> dict[str, int]:
    """Write readings and headlines. Returns counts, including how many are new."""
    from goldagent import db

    written = db.record_readings(conn, snapshot.all_readings())
    fresh = db.upsert_headlines(conn, snapshot.all_headlines(), now=snapshot.collected_at)
    log.info(
        "state written",
        extra={
            "readings_written": written,
            "headlines_seen": len(snapshot.all_headlines()),
            "headlines_new": len(fresh),
        },
    )
    return {
        "readings": written,
        "headlines": len(snapshot.all_headlines()),
        "headlines_new": len(fresh),
    }


def degraded(config: Config, snapshot: Snapshot) -> bool:
    """True when too few collectors worked to justify a confident brief."""
    return snapshot.ok_count < config.data_quality.min_collectors_ok


def staleness_report(config: Config, snapshot: Snapshot, now: datetime | None = None) -> list[str]:
    """Readings older than their category's budget, as human-readable lines."""
    now = now or datetime.now(UTC)
    budgets = config.data_quality.staleness_hours
    category_of = {
        "price_spot": budgets.price,
        "silver": budgets.price,
        "fx": budgets.fx,
        "dollar_index": budgets.fx,
        "fred": budgets.macro,
        "fedwatch": budgets.macro,
        "us_calendar": budgets.macro,
        "gld_holdings": budgets.etf_flows,
        "cot": budgets.cot,
        "central_banks": budgets.central_bank,
        "gdelt": budgets.news,
        "rss": budgets.news,
        "india_policy": budgets.news,
        "price_india": budgets.price,
        "india_etf": budgets.price,
    }

    out: list[str] = []
    for name, result in sorted(snapshot.results.items()):
        budget_hours = category_of.get(name)
        if budget_hours is None or not result.readings:
            continue
        newest = max((r.ts for r in result.readings if r.value is not None), default=None)
        if newest is None:
            continue
        age_hours = (now - newest).total_seconds() / 3600.0
        if age_hours > budget_hours:
            out.append(
                f"{name}: newest reading is {age_hours:.1f}h old, past its "
                f"{budget_hours:.0f}h freshness budget"
            )
    return out
