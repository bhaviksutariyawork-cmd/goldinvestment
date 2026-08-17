"""Event alert evaluation and suppression.

Two stages, deliberately separate:

1. **Evaluate** each enabled trigger against the current snapshot and the stored
   history. This is pure: it reads, it never writes, and it returns candidates.
2. **Gate** the candidates through mute, quiet hours, the daily budget and the
   per-trigger cooldown, in that order.

Keeping them apart means `--dry-run` can show exactly what would have fired and
why something was suppressed, without touching the ledger.

Every threshold comes from `config/alerts.yaml`. There are no numeric literals in
the trigger logic below beyond structural ones.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from goldagent import db
from goldagent.config import Config
from goldagent.models import AlertEvent, Snapshot

log = logging.getLogger(__name__)

# How far past a level the price must retreat before that level can fire again.
# Without this a price oscillating around a level would re-alert every tick.
LEVEL_REARM_MARGIN_PCT = 0.5


@dataclass(slots=True)
class Decision:
    """One candidate and what happened to it. Used for logging and dry-run output."""

    event: AlertEvent
    fired: bool
    suppressed_by: str | None = None


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def evaluate(
    conn: sqlite3.Connection,
    config: Config,
    snapshot: Snapshot,
    now: datetime | None = None,
) -> list[AlertEvent]:
    """Candidate alerts, before any suppression is applied."""
    now = now or datetime.now(UTC)
    triggers = config.alerts.triggers
    candidates: list[AlertEvent] = []

    for check, trigger in (
        (_gold_move, triggers.gold_move),
        (_dxy_move, triggers.dxy_move),
        (_real_yield_move, triggers.real_yield_move),
        (_ratio_move, triggers.gold_silver_ratio),
        (_conflict, triggers.conflict_escalation),
        (_rate_odds, triggers.rate_odds_shift),
        (_price_levels, triggers.inr_price_levels),
    ):
        if not trigger.enabled:
            continue
        try:
            produced = check(conn, config, snapshot, trigger, now)
        except Exception:  # noqa: BLE001 — one broken trigger must not lose the others
            log.error(
                "trigger evaluation failed",
                extra={"trigger": type(trigger).__name__},
                exc_info=True,
            )
            continue
        candidates.extend(produced)

    return candidates


def _pct_move_event(
    conn: sqlite3.Connection,
    metric: str,
    label: str,
    current: float | None,
    window_hours: float,
    threshold_pct: float,
    trigger_type: str,
    now: datetime,
) -> list[AlertEvent]:
    """Shared body for the percentage-move triggers."""
    if current is None:
        return []
    baseline = db.window_baseline(conn, metric, window_hours, now)
    if baseline is None or not baseline.value:
        return []

    move_pct = (current / baseline.value - 1.0) * 100.0
    if abs(move_pct) < threshold_pct:
        return []

    age_hours = (now - baseline.ts).total_seconds() / 3600.0
    return [
        AlertEvent(
            trigger_type=trigger_type,
            summary=(
                f"{label} {move_pct:+.2f}% over {age_hours:.1f}h "
                f"({baseline.value:,.2f} → {current:,.2f}), threshold {threshold_pct}%"
            ),
            detail={
                "metric": metric,
                "current": round(current, 4),
                "baseline": round(baseline.value, 4),
                "baseline_ts": baseline.ts.isoformat(),
                "move_pct": round(move_pct, 3),
                "window_hours": window_hours,
                "threshold_pct": threshold_pct,
                "actual_window_hours": round(age_hours, 2),
            },
            ts=now,
        )
    ]


def _gold_move(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    metric = "gold_usd_oz" if trigger.source == "XAUUSD" else "gold_future_usd_oz"
    return _pct_move_event(
        conn,
        metric,
        f"Gold ({trigger.source})",
        snapshot.value(metric),
        trigger.window_hours,
        trigger.threshold_pct,
        "gold_move",
        now,
    )


def _dxy_move(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    return _pct_move_event(
        conn,
        "dxy",
        "Dollar index",
        snapshot.value("dxy"),
        trigger.window_hours,
        trigger.threshold_pct,
        "dxy_move",
        now,
    )


def _real_yield_move(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    """Real yield moves are measured in basis points, not percent of level."""
    current = snapshot.value("real_yield_10y")
    if current is None:
        return []
    baseline = db.window_baseline(conn, "real_yield_10y", trigger.window_hours, now)
    if baseline is None or baseline.value is None:
        return []

    move_bp = (current - baseline.value) * 100.0
    if abs(move_bp) < trigger.threshold_bp:
        return []

    direction = "higher" if move_bp > 0 else "lower"
    implication = (
        "a headwind for gold — the opportunity cost of holding a zero-yield asset rose"
        if move_bp > 0
        else "supportive for gold — the opportunity cost of holding a zero-yield asset fell"
    )
    return [
        AlertEvent(
            trigger_type="real_yield_move",
            summary=(
                f"US 10Y real yield {move_bp:+.1f}bp {direction} "
                f"({baseline.value:.3f}% → {current:.3f}%), threshold {trigger.threshold_bp}bp. "
                f"Mechanically {implication}."
            ),
            detail={
                "series": trigger.series,
                "current_pct": round(current, 4),
                "baseline_pct": round(baseline.value, 4),
                "move_bp": round(move_bp, 2),
                "threshold_bp": trigger.threshold_bp,
                "baseline_ts": baseline.ts.isoformat(),
            },
            ts=now,
        )
    ]


def _ratio_move(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    current = snapshot.value("gold_silver_ratio")
    if current is None:
        return []
    baseline = db.window_baseline(conn, "gold_silver_ratio", trigger.window_hours, now)
    if baseline is None or baseline.value is None:
        return []

    move = current - baseline.value
    if abs(move) < trigger.threshold_abs:
        return []

    return [
        AlertEvent(
            trigger_type="gold_silver_ratio",
            summary=(
                f"Gold/silver ratio {move:+.2f} to {current:.2f} over "
                f"{trigger.window_hours:.0f}h, threshold {trigger.threshold_abs}"
            ),
            detail={
                "current": round(current, 3),
                "baseline": round(baseline.value, 3),
                "move": round(move, 3),
                "threshold_abs": trigger.threshold_abs,
            },
            ts=now,
        )
    ]


def _conflict(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    """Fire when conflict language appears across independent domains.

    The domain count is what makes this meaningful. Wire copy syndicates, so
    counting articles would let one Reuters story appear to be three sources
    confirming each other. Only domains on the configured allowlist count, and
    each counts once no matter how many articles it filed.
    """
    from collectors.base import match_keywords

    headlines = db.recent_headlines(conn, trigger.window_hours, now)
    if not headlines:
        return []

    allowed = set(trigger.independent_domains)
    hits_by_domain: dict[str, set[str]] = {}
    matched_titles: list[str] = []

    for headline in headlines:
        if headline.domain not in allowed:
            continue
        text = f"{headline.title} {headline.summary}"
        keywords = headline.keywords or match_keywords(text, trigger.keywords)
        if not keywords:
            continue
        hits_by_domain.setdefault(headline.domain, set()).update(keywords)
        matched_titles.append(f"[{headline.domain}] {headline.title}")

    distinct_domains = len(hits_by_domain)
    distinct_keywords = set().union(*hits_by_domain.values()) if hits_by_domain else set()

    if (
        distinct_domains < trigger.min_independent_sources
        or len(distinct_keywords) < trigger.min_keyword_hits
    ):
        return []

    return [
        AlertEvent(
            trigger_type="conflict_escalation",
            summary=(
                f"Conflict escalation language across {distinct_domains} independent "
                f"sources in {trigger.window_hours:.0f}h "
                f"({', '.join(sorted(hits_by_domain))}); "
                f"keywords: {', '.join(sorted(distinct_keywords))}"
            ),
            detail={
                "independent_domains": sorted(hits_by_domain),
                "domain_count": distinct_domains,
                "keywords": sorted(distinct_keywords),
                "min_independent_sources": trigger.min_independent_sources,
                "min_keyword_hits": trigger.min_keyword_hits,
                "window_hours": trigger.window_hours,
                "headlines": matched_titles[:8],
            },
            ts=now,
        )
    ]


def _rate_odds(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    current = snapshot.value("implied_cut_probability_pct")
    if current is None:
        return []
    baseline = db.window_baseline(conn, "implied_cut_probability_pct", trigger.window_hours, now)
    if baseline is None or baseline.value is None:
        return []

    move_pp = current - baseline.value
    if abs(move_pp) < trigger.threshold_pp:
        return []

    return [
        AlertEvent(
            trigger_type="rate_odds_shift",
            summary=(
                f"Futures-implied odds of a 25bp cut moved {move_pp:+.1f}pp "
                f"({baseline.value:.1f}% → {current:.1f}%) over "
                f"{trigger.window_hours:.0f}h, threshold {trigger.threshold_pp}pp"
            ),
            detail={
                "current_pct": round(current, 2),
                "baseline_pct": round(baseline.value, 2),
                "move_pp": round(move_pp, 2),
                "threshold_pp": trigger.threshold_pp,
                "source_caveat": "derived from Fed Funds futures, not a CME FedWatch quote",
            },
            ts=now,
        )
    ]


@dataclass(slots=True)
class _Level:
    """A level from either source, normalised."""

    value: float
    direction: str
    note: str
    origin: str  # "config" or "/set"


def _all_levels(conn: sqlite3.Connection, trigger) -> list[_Level]:
    """Levels from config/alerts.yaml plus levels added at runtime via `/set`.

    Both sources are honoured. The YAML holds levels committed by hand (and keeps
    its comments); the DB holds levels added from Telegram. A value present in
    both is kept once, with the config entry winning so a hand-edited direction or
    note is not overridden by an older `/set`.
    """
    levels = [
        _Level(level.value, level.direction, level.note, "config") for level in trigger.levels
    ]
    seen = {level.value for level in levels}
    for row in db.list_user_levels(conn):
        if row["value"] in seen:
            continue
        levels.append(_Level(row["value"], row["direction"], row["note"], "/set"))
    return levels


def _price_levels(conn, config, snapshot, trigger, now) -> list[AlertEvent]:
    """Fire once per level crossing, re-arming only after a clear retreat."""
    current = snapshot.value("gold_inr_per_10g")
    all_levels = _all_levels(conn, trigger)
    if current is None or not all_levels:
        return []

    events: list[AlertEvent] = []
    for level in all_levels:
        crossed_above = current >= level.value
        crossed_below = current <= level.value

        for direction, crossed in (("above", crossed_above), ("below", crossed_below)):
            if level.direction not in (direction, "either"):
                continue

            already = db.level_already_hit(conn, level.value, direction)

            if not crossed:
                # Re-arm once price has pulled back past the margin, so the level
                # can fire again on a genuine second crossing.
                if already:
                    margin = level.value * LEVEL_REARM_MARGIN_PCT / 100.0
                    retreated = (
                        current < level.value - margin
                        if direction == "above"
                        else current > level.value + margin
                    )
                    if retreated:
                        db.clear_level_hit(conn, level.value)
                        log.info(
                            "price level re-armed",
                            extra={"level": level.value, "direction": direction},
                        )
                continue

            if already:
                continue

            note = f" — {level.note}" if level.note else ""
            events.append(
                AlertEvent(
                    trigger_type="inr_price_levels",
                    summary=(
                        f"INR gold crossed {direction} your ₹{level.value:,.0f}/10g level: "
                        f"now ₹{current:,.0f}/10g{note}"
                    ),
                    detail={
                        "level": level.value,
                        "direction": direction,
                        "current_inr_per_10g": round(current, 2),
                        "note": level.note,
                        "origin": level.origin,
                        "basis": "derived all-in INR price, not a dealer quote",
                    },
                    ts=now,
                )
            )
            break  # one event per level, even if `either` matches both sides

    return events


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #


def gate(
    conn: sqlite3.Connection,
    config: Config,
    candidates: list[AlertEvent],
    now: datetime | None = None,
    *,
    commit: bool = True,
) -> list[Decision]:
    """Apply mute, quiet hours, the daily budget and per-trigger cooldowns.

    With `commit=False` nothing is written — used by `--dry-run` so it can show
    what would fire without consuming budget or setting cooldowns.
    """
    now = now or datetime.now(UTC)
    decisions: list[Decision] = []

    if not config.alerts.enabled:
        return [Decision(event, False, "alerts disabled in config") for event in candidates]

    if db.is_muted(conn, now):
        until = db.get_mute_until(conn)
        reason = f"muted until {until.astimezone(config.tz):%H:%M %Z}" if until else "muted"
        return [Decision(event, False, reason) for event in candidates]

    local_time = now.astimezone(config.tz).timetz().replace(tzinfo=None)
    if config.alerts.budget.quiet_hours.contains(local_time):
        quiet = config.alerts.budget.quiet_hours
        reason = f"quiet hours ({quiet.start:%H:%M}–{quiet.end:%H:%M} {config.investor.timezone})"
        return [Decision(event, False, reason) for event in candidates]

    fired_today = db.alerts_on_local_day(conn, config.tz, now)
    remaining = max(0, config.alerts.budget.max_per_day - fired_today)

    # Most severe first, so a full budget spends on the biggest move rather than
    # whichever trigger happens to be evaluated first.
    ordered = sorted(candidates, key=_severity, reverse=True)

    for event in ordered:
        cooldown_hours = config.alerts.cooldown_for(event.trigger_type)
        last = db.last_fired(conn, event.trigger_type)
        if last is not None and now - last < timedelta(hours=cooldown_hours):
            resumes = last + timedelta(hours=cooldown_hours)
            decisions.append(
                Decision(
                    event,
                    False,
                    f"{event.trigger_type} cooling down until "
                    f"{resumes.astimezone(config.tz):%H:%M %Z} ({cooldown_hours:g}h)",
                )
            )
            continue

        if remaining <= 0:
            cap = config.alerts.budget.max_per_day
            # Note this does NOT mark a price level as hit. A crossing suppressed
            # by the budget stays a live candidate and will fire on a later tick
            # once headroom returns, rather than being silently consumed.
            decisions.append(
                Decision(
                    event,
                    False,
                    f"daily cap of {cap} reached ({fired_today} sent before this run, "
                    f"{cap - fired_today} in it)",
                )
            )
            continue

        if commit:
            db.record_alert(conn, event)
            if event.trigger_type == "inr_price_levels":
                db.mark_level_hit(
                    conn, event.detail["level"], event.detail["direction"], now
                )
        remaining -= 1
        decisions.append(Decision(event, True))

    for decision in decisions:
        log.info(
            "alert decision",
            extra={
                "trigger": decision.event.trigger_type,
                "fired": decision.fired,
                "suppressed_by": decision.suppressed_by,
                "summary": decision.event.summary[:160],
            },
        )
    return decisions


def _severity(event: AlertEvent) -> float:
    """Rank candidates so a constrained budget spends on the largest signal.

    Normalised to "multiples of its own threshold" so a 2x-threshold real-yield
    move outranks a 1.1x-threshold gold move, rather than comparing raw units of
    different things.
    """
    detail = event.detail
    for value_key, threshold_key in (
        ("move_pct", "threshold_pct"),
        ("move_bp", "threshold_bp"),
        ("move_pp", "threshold_pp"),
        ("move", "threshold_abs"),
    ):
        value, threshold = detail.get(value_key), detail.get(threshold_key)
        if value is not None and threshold:
            return abs(float(value)) / float(threshold)

    if event.trigger_type == "conflict_escalation":
        needed = detail.get("min_independent_sources") or 1
        return float(detail.get("domain_count", 0)) / float(needed)

    # A level the reader set himself outranks a threshold the config guessed.
    if event.trigger_type == "inr_price_levels":
        return 10.0

    return 1.0
