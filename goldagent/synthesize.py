"""Claude synthesis and the cost guard.

The prompt handed to the model is assembled here: today's readings with their
sources, the trailing daily history so direction is visible, the previous briefing
so it can avoid repeating itself, headlines flagged new since the last run, and an
explicit list of what failed to fetch.

That last part matters. The analyst prompt forbids filling a gap from memory, and
it can only obey that if the gaps are named. Every collector failure and every
stale reading is stated in the prompt rather than quietly omitted.

Cost control is a **pre-call** guard. Token usage is estimated with the token
counting endpoint, priced at the configured rates, and checked against both the
per-run and daily caps before any billable request is made. A guard that only
measures spend after the fact is not a cap.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from goldagent import db
from goldagent.config import Config
from goldagent.models import AlertEvent, Brief, Snapshot, Usage

log = logging.getLogger(__name__)

Mode = Literal["morning", "evening", "weekly", "alert", "deep", "why"]

# Metrics rendered in the "today" block, in the order the analyst prompt ranks
# them: real yields first, then the dollar, then price, positioning, flows.
PRESENTATION_ORDER = (
    ("real_yield_10y", "US 10Y real yield (TIPS)"),
    ("nominal_yield_10y", "US 10Y nominal yield"),
    ("breakeven_inflation_10y", "10Y breakeven inflation"),
    ("fed_funds_rate", "Effective fed funds rate"),
    ("implied_policy_rate", "Futures-implied policy rate"),
    ("implied_cut_probability_pct", "Implied share of a 25bp cut priced"),
    ("dxy", "US dollar index"),
    ("gold_usd_oz", "Gold spot"),
    ("gold_future_usd_oz", "Gold front-month future"),
    ("gold_spot_future_divergence_pct", "Spot/future divergence"),
    ("gold_inr_per_10g", "Gold INR per 10g (all-in, derived)"),
    ("gold_inr_per_10g_ex_gst", "Gold INR per 10g (ex-GST, derived)"),
    ("ibja_inr_per_10g", "IBJA published rate"),
    ("mcx_gold_inr_per_10g", "MCX gold front-month"),
    ("india_physical_premium_pct", "India physical premium vs derived (ex-GST basis)"),
    ("usdinr", "USD/INR"),
    ("silver_usd_oz", "Silver spot"),
    ("gold_silver_ratio", "Gold/silver ratio"),
    ("cot_managed_money_net_long", "COT managed money net long"),
    ("gld_tonnes", "GLD tonnes held"),
    ("gld_tonnes_delta", "GLD tonnes change"),
    ("india_gold_etf_median_nav", "India gold ETF median NAV"),
)

# Metrics whose trailing daily history is worth the context it costs.
HISTORY_METRICS = (
    "gold_usd_oz",
    "gold_inr_per_10g",
    "usdinr",
    "dxy",
    "real_yield_10y",
    "gold_silver_ratio",
    "gld_tonnes",
    "cot_managed_money_net_long",
)

MAX_HEADLINES = 25


class SpendCapExceeded(RuntimeError):
    """Raised when a call would breach the configured spend cap."""

    def __init__(self, message: str, projected_usd: float, spent_today_usd: float) -> None:
        super().__init__(message)
        self.projected_usd = projected_usd
        self.spent_today_usd = spent_today_usd


@dataclass(slots=True)
class Estimate:
    input_tokens: int
    max_output_tokens: int
    projected_usd: float


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


def build_user_message(
    config: Config,
    snapshot: Snapshot,
    conn: sqlite3.Connection | None,
    mode: Mode,
    *,
    previous: Brief | None = None,
    alert: AlertEvent | None = None,
    topic: str | None = None,
    now: datetime | None = None,
) -> str:
    """Assemble the data block the model reasons over."""
    now = now or datetime.now(UTC)
    local = now.astimezone(config.tz)
    parts: list[str] = []

    parts.append(_header(config, mode, local, alert=alert, topic=topic))
    parts.append(_today_block(snapshot))

    if conn is not None:
        history = _history_block(config, conn, now)
        if history:
            parts.append(history)

    parts.append(_headlines_block(snapshot))

    gaps = _gaps_block(config, snapshot, now)
    if gaps:
        parts.append(gaps)

    notes = snapshot.notes()
    if notes:
        parts.append("## COLLECTOR NOTES\n\n" + "\n".join(f"- {note}" for note in notes))

    if previous and previous.text.strip():
        age = _describe_age(now - previous.ts)
        parts.append(
            f"## PREVIOUS BRIEFING ({previous.mode}, {age} ago) — DO NOT REPEAT THIS\n\n"
            f"If a read below has been invalidated by anything above, open with that "
            f"correction. If nothing material changed, say so briefly instead of "
            f"restating it.\n\n"
            f"```\n{previous.text.strip()}\n```"
        )
    else:
        parts.append(
            "## PREVIOUS BRIEFING\n\nNone on record — this is the first briefing, so there "
            "is nothing to avoid repeating and no prior read to correct."
        )

    parts.append(_instruction_block(config, mode, alert=alert, topic=topic))

    return "\n\n".join(part for part in parts if part)


def _header(
    config: Config,
    mode: Mode,
    local: datetime,
    *,
    alert: AlertEvent | None,
    topic: str | None,
) -> str:
    label = {
        "morning": "07:30 IST overnight briefing (US session closed)",
        "evening": "18:30 IST pre-US-open note (only what changed since this morning)",
        "weekly": "Sunday weekly review",
        "alert": "EVENT ALERT",
        "deep": f"Deep dive on: {topic or 'unspecified'}",
        "why": "Explanation of the last 24 hours' move",
    }.get(mode, mode)

    lines = [
        f"# {label}",
        f"Generated {local:%Y-%m-%d %H:%M} {config.investor.timezone} "
        f"({local.astimezone(UTC):%Y-%m-%d %H:%M} UTC).",
    ]
    if alert is not None:
        lines.append(f"Trigger: {alert.trigger_type} — {alert.summary}")
        if alert.detail:
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(alert.detail.items()))
            lines.append(f"Trigger detail: {rendered}")
    return "\n".join(lines)


def _today_block(snapshot: Snapshot) -> str:
    """Current readings with their source and session change, one per line."""
    lines = ["## TODAY'S READINGS", ""]
    rendered: set[str] = set()

    by_metric = {}
    for result in snapshot.results.values():
        for reading in result.readings:
            if reading.value is not None and reading.metric not in by_metric:
                by_metric[reading.metric] = reading

    for metric, label in PRESENTATION_ORDER:
        reading = by_metric.get(metric)
        if reading is None:
            continue
        rendered.add(metric)
        lines.append(_reading_line(label, reading))

    # Anything not in the presentation order still gets shown, except the calendar
    # rows which are rendered separately below.
    leftovers = [
        (m, r) for m, r in sorted(by_metric.items()) if m not in rendered and m != "us_event"
    ]
    if leftovers:
        lines.append("")
        for metric, reading in leftovers:
            lines.append(_reading_line(metric, reading))

    events = [r for r in snapshot.all_readings() if r.metric == "us_event"]
    if events:
        lines.append("")
        lines.append("### US data and policy calendar, next 7 days")
        for reading in sorted(events, key=lambda r: r.extra.get("days_away", 99)):
            name = reading.extra.get("event", "event")
            when = reading.extra.get("date", "?")
            days = reading.extra.get("days_away", "?")
            lines.append(f"- {name}: {when} (in {days}d) — source: {reading.source}")

    return "\n".join(lines)


def _reading_line(label: str, reading) -> str:
    value = reading.value
    formatted = f"{value:,.2f}" if abs(value) < 1e7 else f"{value:,.0f}"
    line = f"- {label}: {formatted} {reading.unit}"

    change = reading.extra.get("change_pct")
    if change is not None:
        line += f" (session {change:+.2f}%)"
    change_bp = reading.extra.get("change_bp")
    if change_bp is not None:
        line += f" ({change_bp:+.1f}bp vs prior print)"
    delta = reading.extra.get("delta_tonnes")
    if delta is not None:
        line += f" (delta {delta:+.2f}t)"
    week = reading.extra.get("week_change")
    if week is not None:
        line += f" (week {week:+,.0f})"

    line += f" — source: {reading.source}"

    observed = reading.extra.get("observation_date") or reading.extra.get("report_date")
    if observed:
        line += f" [observation date {observed}]"
    caveat = reading.extra.get("caveat")
    if caveat:
        line += f" [caveat: {caveat}]"
    return line


def _history_block(config: Config, conn: sqlite3.Connection, now: datetime) -> str:
    days = max(b.lookback_days for b in (config.briefings.morning, config.briefings.evening))
    lines = [f"## TRAILING {days}-DAY HISTORY (one value per IST day, oldest first)", ""]
    any_series = False

    for metric in HISTORY_METRICS:
        series = db.daily_series(conn, metric, days, config.tz, now=now)
        if len(series) < 2:
            continue
        any_series = True
        rendered = " | ".join(f"{day:%m-%d} {value:,.2f}" for day, value in series)
        first, last = series[0][1], series[-1][1]
        direction = ""
        if first:
            direction = f"  → {(last / first - 1) * 100:+.2f}% over the window"
        lines.append(f"- {metric}: {rendered}{direction}")

    if not any_series:
        return (
            "## TRAILING HISTORY\n\nNot enough history stored yet to show direction. "
            "Do not infer a trend — describe levels only, and say history is not yet available."
        )
    return "\n".join(lines)


def _headlines_block(snapshot: Snapshot) -> str:
    headlines = snapshot.all_headlines()
    if not headlines:
        return (
            "## HEADLINES\n\nNo headlines were retrieved this run. Treat the news leg as "
            "unavailable — do not conclude that news flow was quiet."
        )

    new = [h for h in headlines if h.is_new]
    known = [h for h in headlines if not h.is_new]

    lines = ["## HEADLINES", ""]
    if new:
        lines.append(f"### New since the last run ({len(new)})")
        for h in new[:MAX_HEADLINES]:
            lines.append(f"- [{h.source}] {h.title} ({h.url})")
    else:
        lines.append("### New since the last run: none")

    if known:
        lines.append("")
        lines.append(f"### Already seen in an earlier run ({len(known)}, for context only)")
        for h in known[:10]:
            lines.append(f"- [{h.source}] {h.title}")

    return "\n".join(lines)


def _gaps_block(config: Config, snapshot: Snapshot, now: datetime) -> str:
    from goldagent.collect import staleness_report

    failures = [
        f"- {name}: {result.error or 'unavailable'}"
        for name, result in sorted(snapshot.results.items())
        if not result.ok
    ]
    stale = [f"- {line}" for line in staleness_report(config, snapshot, now)]

    if not failures and not stale:
        return ""

    lines = ["## DATA GAPS — STATE THESE, DO NOT FILL THEM FROM MEMORY", ""]
    if failures:
        lines.append("Collectors that failed this run:")
        lines.extend(failures)
    if stale:
        if failures:
            lines.append("")
        lines.append("Readings past their freshness budget:")
        lines.extend(stale)
    return "\n".join(lines)


def _instruction_block(
    config: Config, mode: Mode, *, alert: AlertEvent | None, topic: str | None
) -> str:
    if mode == "alert":
        return (
            "## YOUR TASK\n\n"
            "Write an event alert in the three-line format from your instructions: what "
            "happened with the number, the most likely mechanism, and whether this is a "
            "fade or a trend with your confidence in that call. No preamble, no sign-off. "
            "Three lines maximum."
        )

    if mode == "why":
        return (
            "## YOUR TASK\n\n"
            "Explain the last 24 hours' move in gold. Lead with the driver that actually "
            "moved it, ranked by the priorities in your instructions. Under 200 words. "
            "No 'watch' line — this is a backward-looking explanation."
        )

    if mode == "deep":
        return (
            "## YOUR TASK\n\n"
            f"Longer analysis on one driver: {topic or 'the dominant driver right now'}. "
            "Up to 600 words. The usual rules still hold: cite sources, separate data from "
            "interpretation, no recommendations, no price forecasts."
        )

    briefing = config.briefing(mode)
    extra = ""
    if mode == "evening":
        extra = (
            " Cover only what changed since the morning briefing. If nothing material "
            "changed, a two-line note saying so is the correct output."
        )
    elif mode == "weekly":
        included = ", ".join(briefing.include) or "positioning, flows and the week ahead"
        extra = f" Cover: {included}."

    return (
        "## YOUR TASK\n\n"
        f"Write the {mode} briefing.{extra} Hard cap {briefing.max_words} words — shorter is "
        f"better, and on a quiet day a short note saying it was quiet is the right answer. "
        f"Report INR per 10g alongside USD per oz. Markdown, but keep formatting light: this "
        f"is read on a phone."
    )


def _describe_age(delta) -> str:
    hours = delta.total_seconds() / 3600.0
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


# --------------------------------------------------------------------------- #
# Cost guard
# --------------------------------------------------------------------------- #


def estimate(
    client,
    config: Config,
    system: str,
    user_message: str,
    max_output_tokens: int | None = None,
) -> Estimate:
    """Price the call before making it.

    Output tokens are unknown ahead of time, so the projection assumes the full
    `max_tokens` budget. That deliberately overestimates: the guard should stop a
    call that *could* breach the cap, not merely one that turns out to.
    """
    spend = config.llm.spend
    max_output = max_output_tokens or config.llm.max_output_tokens

    try:
        counted = client.messages.count_tokens(
            model=config.llm.model,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        input_tokens = int(counted.input_tokens)
    except Exception as exc:  # noqa: BLE001 — fall back to a coarse estimate
        # ~3.5 characters per token is a conservative English approximation; it
        # overestimates slightly, which is the right direction for a spend guard.
        input_tokens = int((len(system) + len(user_message)) / 3.5)
        log.warning(
            "token counting failed, using character-based estimate",
            extra={"error": str(exc)[:200], "estimated_input_tokens": input_tokens},
        )

    projected = (
        input_tokens / 1_000_000 * spend.input_usd_per_mtok
        + max_output / 1_000_000 * spend.output_usd_per_mtok
    )
    return Estimate(
        input_tokens=input_tokens, max_output_tokens=max_output, projected_usd=projected
    )


def check_budget(
    conn: sqlite3.Connection,
    config: Config,
    est: Estimate,
    now: datetime | None = None,
) -> None:
    """Raise SpendCapExceeded if this call would breach either cap."""
    spend = config.llm.spend
    already = db.spend_today(conn, config.tz, now)

    if est.projected_usd > spend.per_run_usd_cap:
        raise SpendCapExceeded(
            f"this call projects at ${est.projected_usd:.4f}, above the per-run cap of "
            f"${spend.per_run_usd_cap:.2f} ({est.input_tokens:,} input tokens + up to "
            f"{est.max_output_tokens:,} output). Lower llm.max_output_tokens or raise the cap.",
            projected_usd=est.projected_usd,
            spent_today_usd=already,
        )

    if already + est.projected_usd > spend.daily_usd_cap:
        raise SpendCapExceeded(
            f"${already:.4f} already spent today and this call projects at "
            f"${est.projected_usd:.4f}, which would exceed the daily cap of "
            f"${spend.daily_usd_cap:.2f}. No call was made.",
            projected_usd=est.projected_usd,
            spent_today_usd=already,
        )


def price(config: Config, input_tokens: int, output_tokens: int) -> float:
    spend = config.llm.spend
    return (
        input_tokens / 1_000_000 * spend.input_usd_per_mtok
        + output_tokens / 1_000_000 * spend.output_usd_per_mtok
    )


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #


def synthesize(
    client,
    conn: sqlite3.Connection,
    config: Config,
    system: str,
    user_message: str,
    mode: Mode,
    *,
    max_output_tokens: int | None = None,
    now: datetime | None = None,
    degraded: bool = False,
) -> Brief:
    """Call Claude and return the brief, recording token usage and cost.

    Raises SpendCapExceeded before making any billable request if a cap would be
    breached. Any other API failure propagates — the caller turns it into a
    Telegram failure notice.
    """
    now = now or datetime.now(UTC)
    est = estimate(client, config, system, user_message, max_output_tokens)
    check_budget(conn, config, est, now)

    log.info(
        "calling Claude",
        extra={
            "mode": mode,
            "model": config.llm.model,
            "effort": config.llm.effort,
            "input_tokens_est": est.input_tokens,
            "projected_usd": round(est.projected_usd, 5),
            "spent_today_usd": round(db.spend_today(conn, config.tz, now), 5),
        },
    )

    request: dict = {
        "model": config.llm.model,
        "max_tokens": est.max_output_tokens,
        # The system prompt is byte-stable across runs, so it caches; the volatile
        # data block sits after it in the user turn and does not invalidate it.
        "system": [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_message}],
        "output_config": {"effort": config.llm.effort},
    }
    if config.llm.thinking == "adaptive":
        request["thinking"] = {"type": "adaptive"}
    else:
        request["thinking"] = {"type": "disabled"}

    response = client.messages.create(**request)

    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(
            f"Claude declined this request (stop_reason=refusal, category={category}). "
            f"No briefing was produced."
        )

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()

    usage = _usage_from(response, config)
    if not text:
        raise RuntimeError(
            f"Claude returned no text (stop_reason={getattr(response, 'stop_reason', '?')}). "
            f"If this is max_tokens, raise llm.max_output_tokens."
        )

    total_today = db.add_spend(conn, config.tz, usage.cost_usd, now)
    log.info(
        "synthesis complete",
        extra={
            "mode": mode,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cost_usd": round(usage.cost_usd, 5),
            "spend_today_usd": round(total_today, 5),
            "words": len(text.split()),
            "stop_reason": getattr(response, "stop_reason", None),
        },
    )

    return Brief(mode=mode, text=text, usage=usage, ts=now, degraded=degraded)


def _usage_from(response, config: Config) -> Usage:
    raw = getattr(response, "usage", None)
    input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
    cache_read = int(getattr(raw, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(raw, "cache_creation_input_tokens", 0) or 0)

    # Cache reads bill at roughly a tenth of input, writes at 1.25x. Folding those
    # in keeps the ledger honest rather than counting cached tokens at full price.
    spend = config.llm.spend
    cost = (
        input_tokens / 1_000_000 * spend.input_usd_per_mtok
        + cache_read / 1_000_000 * spend.input_usd_per_mtok * 0.1
        + cache_write / 1_000_000 * spend.input_usd_per_mtok * 1.25
        + output_tokens / 1_000_000 * spend.output_usd_per_mtok
    )

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=round(cost, 6),
        model=getattr(response, "model", config.llm.model),
    )


def degraded_notice(config: Config, snapshot: Snapshot) -> str:
    """A plain warning used instead of a brief when too little data came back.

    Deliberately not model-generated: with most collectors down there is nothing
    to analyse, and spending a call to say so would be wasteful and would invite
    the model to speculate past its data.
    """
    failed = snapshot.failed
    working = sorted(name for name, r in snapshot.results.items() if r.has_data)
    gold = snapshot.value("gold_usd_oz")
    inr = snapshot.value("gold_inr_per_10g")

    lines = [
        "*Degraded run — no briefing*",
        "",
        f"Only {snapshot.data_count} of {len(snapshot.results)} collectors returned data "
        f"(minimum for a briefing is {config.data_quality.min_collectors_ok}). "
        f"Not enough to form a view, so no analysis was generated.",
    ]
    if gold:
        line = f"Gold: ${gold:,.2f}/oz"
        if inr:
            line += f" · ₹{inr:,.0f}/10g (derived)"
        lines += ["", line]
    lines += [
        "",
        f"Failed: {', '.join(failed) or 'none'}",
        f"Returned nothing: {', '.join(snapshot.empty) or 'none'}",
        f"With data: {', '.join(working) or 'none'}",
    ]
    return "\n".join(lines)
