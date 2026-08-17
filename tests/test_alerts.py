"""Trigger evaluation and the suppression gates.

Threshold arithmetic and the order of the gates are the two things most likely to
go quietly wrong here, so both are pinned. Every test drives config values rather
than literals, so a threshold changed in alerts.yaml does not silently invalidate
the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goldagent import alerts, db
from goldagent.models import AlertEvent, CollectorResult, Headline, Reading, Snapshot
from tests.conftest import NOW


def seed(conn, metric: str, value: float, hours_ago: float, now=NOW):
    db.record_readings(
        conn, [Reading(metric, value, "u", "test", now - timedelta(hours=hours_ago))]
    )


def snap(**metrics: float) -> Snapshot:
    s = Snapshot(collected_at=NOW)
    s.results["test"] = CollectorResult(
        name="test", readings=[Reading(m, v, "u", "test", NOW) for m, v in metrics.items()]
    )
    return s


# --------------------------------------------------------------------------- #
# Percentage-move triggers
# --------------------------------------------------------------------------- #


class TestGoldMove:
    def test_fires_above_threshold(self, conn, config, now):
        threshold = config.alerts.triggers.gold_move.threshold_pct
        seed(conn, "gold_usd_oz", 3000.0, 5)
        # A move comfortably past the threshold.
        current = 3000.0 * (1 + (threshold + 0.5) / 100)

        events = alerts.evaluate(conn, config, snap(gold_usd_oz=current), now)
        gold = [e for e in events if e.trigger_type == "gold_move"]

        assert len(gold) == 1
        assert gold[0].detail["move_pct"] == pytest.approx(threshold + 0.5, abs=0.01)

    def test_silent_below_threshold(self, conn, config, now):
        threshold = config.alerts.triggers.gold_move.threshold_pct
        seed(conn, "gold_usd_oz", 3000.0, 5)
        current = 3000.0 * (1 + (threshold - 0.3) / 100)

        events = alerts.evaluate(conn, config, snap(gold_usd_oz=current), now)
        assert [e for e in events if e.trigger_type == "gold_move"] == []

    def test_fires_on_a_downward_move_too(self, conn, config, now):
        threshold = config.alerts.triggers.gold_move.threshold_pct
        seed(conn, "gold_usd_oz", 3000.0, 5)
        current = 3000.0 * (1 - (threshold + 0.5) / 100)

        events = [
            e
            for e in alerts.evaluate(conn, config, snap(gold_usd_oz=current), now)
            if e.trigger_type == "gold_move"
        ]
        assert len(events) == 1
        assert events[0].detail["move_pct"] < 0

    def test_no_history_cannot_manufacture_an_alert(self, conn, config, now):
        # Fresh database: nothing to compare against.
        events = alerts.evaluate(conn, config, snap(gold_usd_oz=3400.0), now)
        assert [e for e in events if e.trigger_type == "gold_move"] == []

    def test_missing_current_value_is_silent(self, conn, config, now):
        seed(conn, "gold_usd_oz", 3000.0, 5)
        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "gold_move"] == []

    def test_disabled_trigger_never_evaluates(self, conn, config, now):
        config.alerts.triggers.gold_move.enabled = False
        seed(conn, "gold_usd_oz", 3000.0, 5)
        events = alerts.evaluate(conn, config, snap(gold_usd_oz=4000.0), now)
        assert [e for e in events if e.trigger_type == "gold_move"] == []

    def test_source_selects_the_metric(self, conn, config, now):
        config.alerts.triggers.gold_move.source = "GC=F"
        seed(conn, "gold_future_usd_oz", 3000.0, 5)
        # Spot moving does nothing; the configured source is the future.
        events = alerts.evaluate(conn, config, snap(gold_usd_oz=4000.0), now)
        assert [e for e in events if e.trigger_type == "gold_move"] == []

        events = alerts.evaluate(conn, config, snap(gold_future_usd_oz=4000.0), now)
        assert len([e for e in events if e.trigger_type == "gold_move"]) == 1


class TestRealYieldMove:
    def test_threshold_is_in_basis_points_not_percent(self, conn, config, now):
        threshold_bp = config.alerts.triggers.real_yield_move.threshold_bp
        seed(conn, "real_yield_10y", 1.900, 30)
        # A move of threshold+2 bp expressed as a percentage level.
        current = 1.900 - (threshold_bp + 2) / 100.0

        events = [
            e
            for e in alerts.evaluate(conn, config, snap(real_yield_10y=current), now)
            if e.trigger_type == "real_yield_move"
        ]
        assert len(events) == 1
        assert events[0].detail["move_bp"] == pytest.approx(-(threshold_bp + 2), abs=0.01)

    def test_a_small_move_in_percent_terms_is_a_large_move_in_bp(self, conn, config, now):
        # 0.10 percentage points is 10bp — over an 8bp threshold, even though
        # 0.10 looks tiny next to a 1.2% price threshold.
        seed(conn, "real_yield_10y", 1.900, 30)
        events = [
            e
            for e in alerts.evaluate(conn, config, snap(real_yield_10y=1.800), now)
            if e.trigger_type == "real_yield_move"
        ]
        assert len(events) == 1

    def test_falling_yield_is_described_as_supportive(self, conn, config, now):
        seed(conn, "real_yield_10y", 1.900, 30)
        event = next(
            e
            for e in alerts.evaluate(conn, config, snap(real_yield_10y=1.700), now)
            if e.trigger_type == "real_yield_move"
        )
        assert "supportive for gold" in event.summary

    def test_rising_yield_is_described_as_a_headwind(self, conn, config, now):
        seed(conn, "real_yield_10y", 1.700, 30)
        event = next(
            e
            for e in alerts.evaluate(conn, config, snap(real_yield_10y=1.900), now)
            if e.trigger_type == "real_yield_move"
        )
        assert "headwind for gold" in event.summary


class TestConflictEscalation:
    def _post(self, conn, domains, keywords=("missile", "strike"), now=NOW):
        headlines = [
            Headline(
                title=f"{keywords[0].title()} {keywords[1]} reported",
                url=f"https://{domain}/story{index}",
                domain=domain,
                source=domain,
                keywords=list(keywords),
            )
            for index, domain in enumerate(domains)
        ]
        db.upsert_headlines(conn, headlines, now)

    def test_below_the_source_minimum_is_silent(self, conn, config, now):
        minimum = config.alerts.triggers.conflict_escalation.min_independent_sources
        allowed = config.alerts.triggers.conflict_escalation.independent_domains
        self._post(conn, allowed[: minimum - 1])

        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "conflict_escalation"] == []

    def test_at_the_source_minimum_fires(self, conn, config, now):
        minimum = config.alerts.triggers.conflict_escalation.min_independent_sources
        allowed = config.alerts.triggers.conflict_escalation.independent_domains
        self._post(conn, allowed[:minimum])

        events = [
            e
            for e in alerts.evaluate(conn, config, snap(), now)
            if e.trigger_type == "conflict_escalation"
        ]
        assert len(events) == 1
        assert events[0].detail["domain_count"] == minimum

    def test_wire_syndication_counts_once(self, conn, config, now):
        """Six URLs from one outlet are one source, not six."""
        minimum = config.alerts.triggers.conflict_escalation.min_independent_sources
        assert minimum > 1, "this test is meaningless with a minimum of 1"

        self._post(conn, ["reuters.com"] * 6)
        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "conflict_escalation"] == []

    def test_domains_outside_the_allowlist_do_not_count(self, conn, config, now):
        minimum = config.alerts.triggers.conflict_escalation.min_independent_sources
        self._post(conn, [f"contentfarm{i}.example" for i in range(minimum + 3)])

        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "conflict_escalation"] == []

    def test_one_keyword_across_many_domains_is_not_enough(self, conn, config, now):
        trigger = config.alerts.triggers.conflict_escalation
        if trigger.min_keyword_hits < 2:
            pytest.skip("config requires only one keyword")
        allowed = trigger.independent_domains
        self._post(conn, allowed[: trigger.min_independent_sources], keywords=("missile", "missile"))

        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "conflict_escalation"] == []

    def test_headlines_outside_the_window_are_ignored(self, conn, config, now):
        trigger = config.alerts.triggers.conflict_escalation
        allowed = trigger.independent_domains
        stale_time = now - timedelta(hours=trigger.window_hours + 2)
        self._post(conn, allowed[: trigger.min_independent_sources], now=stale_time)

        events = alerts.evaluate(conn, config, snap(), now)
        assert [e for e in events if e.trigger_type == "conflict_escalation"] == []


class TestPriceLevels:
    def test_fires_once_then_stays_quiet(self, conn, config, now):
        config.alerts.budget.max_per_day = 50
        db.add_user_level(conn, 100_000.0, "above", "", now)

        first = alerts.evaluate(conn, config, snap(gold_inr_per_10g=101_000.0), now)
        assert len([e for e in first if e.trigger_type == "inr_price_levels"]) == 1
        alerts.gate(conn, config, first, now)

        second = alerts.evaluate(conn, config, snap(gold_inr_per_10g=102_000.0), now)
        assert [e for e in second if e.trigger_type == "inr_price_levels"] == []

    def test_direction_below_ignores_an_upward_cross(self, conn, config, now):
        config.alerts.budget.max_per_day = 50
        db.add_user_level(conn, 100_000.0, "below", "", now)

        events = alerts.evaluate(conn, config, snap(gold_inr_per_10g=101_000.0), now)
        assert [e for e in events if e.trigger_type == "inr_price_levels"] == []

        events = alerts.evaluate(conn, config, snap(gold_inr_per_10g=99_000.0), now)
        assert len([e for e in events if e.trigger_type == "inr_price_levels"]) == 1

    def test_rearms_only_after_a_real_pullback(self, conn, config, now):
        config.alerts.budget.max_per_day = 50
        config.alerts.triggers.inr_price_levels.cooldown_hours = 0
        level = 100_000.0
        db.add_user_level(conn, level, "above", "", now)

        alerts.gate(conn, config, alerts.evaluate(conn, config, snap(gold_inr_per_10g=101_000.0), now), now)
        assert db.level_already_hit(conn, level, "above")

        margin = alerts.LEVEL_REARM_MARGIN_PCT
        # Just inside the margin: still considered hit.
        alerts.evaluate(conn, config, snap(gold_inr_per_10g=level * (1 - margin / 200)), now)
        assert db.level_already_hit(conn, level, "above")

        # Past the margin: re-armed.
        alerts.evaluate(conn, config, snap(gold_inr_per_10g=level * (1 - margin / 100 - 0.002)), now)
        assert not db.level_already_hit(conn, level, "above")

    def test_config_and_set_levels_both_apply(self, conn, config, now):
        from goldagent.config import PriceLevel

        config.alerts.triggers.inr_price_levels.levels = [
            PriceLevel(value=100_000.0, direction="above", note="from yaml")
        ]
        db.add_user_level(conn, 105_000.0, "above", "from /set", now)

        levels = alerts._all_levels(conn, config.alerts.triggers.inr_price_levels)
        origins = {level.value: level.origin for level in levels}
        assert origins == {100_000.0: "config", 105_000.0: "/set"}

    def test_a_duplicate_value_prefers_the_config_entry(self, conn, config, now):
        from goldagent.config import PriceLevel

        config.alerts.triggers.inr_price_levels.levels = [
            PriceLevel(value=100_000.0, direction="below", note="hand-edited")
        ]
        db.add_user_level(conn, 100_000.0, "above", "older /set", now)

        levels = alerts._all_levels(conn, config.alerts.triggers.inr_price_levels)
        assert len(levels) == 1
        assert levels[0].direction == "below"
        assert levels[0].origin == "config"


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


def two_candidates(now=NOW):
    return [
        AlertEvent("gold_move", "gold moved", {"move_pct": 2.0, "threshold_pct": 1.2}, now),
        AlertEvent("dxy_move", "dxy moved", {"move_pct": 1.0, "threshold_pct": 0.6}, now),
    ]


class TestGates:
    def test_mute_suppresses_everything(self, conn, config, now):
        db.set_mute_until(conn, now + timedelta(hours=2))
        decisions = alerts.gate(conn, config, two_candidates(), now)
        assert all(not d.fired for d in decisions)
        assert all("muted" in d.suppressed_by for d in decisions)

    def test_expired_mute_does_not_suppress(self, conn, config, now):
        db.set_mute_until(conn, now - timedelta(minutes=1))
        decisions = alerts.gate(conn, config, two_candidates(), now)
        assert any(d.fired for d in decisions)

    def test_quiet_hours_suppress_everything(self, conn, config):
        quiet = config.alerts.budget.quiet_hours
        # Build an instant inside the quiet window, in the investor's timezone.
        local_midpoint = datetime(2026, 8, 18, quiet.start.hour, 30, tzinfo=config.tz)
        decisions = alerts.gate(conn, config, two_candidates(), local_midpoint.astimezone(UTC))
        assert all(not d.fired for d in decisions)
        assert all("quiet hours" in d.suppressed_by for d in decisions)

    def test_alerts_disabled_suppresses_everything(self, conn, config, now):
        config.alerts.enabled = False
        decisions = alerts.gate(conn, config, two_candidates(), now)
        assert all("disabled" in d.suppressed_by for d in decisions)

    def test_daily_budget_caps_the_count(self, conn, config, now):
        config.alerts.budget.max_per_day = 1
        decisions = alerts.gate(conn, config, two_candidates(), now)
        assert sum(1 for d in decisions if d.fired) == 1
        assert any("daily cap" in (d.suppressed_by or "") for d in decisions)

    def test_budget_spends_on_the_more_severe_candidate_first(self, conn, config, now):
        config.alerts.budget.max_per_day = 1
        # gold is 1.67x its threshold, dxy is 1.67x too — make gold clearly larger.
        candidates = [
            AlertEvent("dxy_move", "dxy", {"move_pct": 0.7, "threshold_pct": 0.6}, now),
            AlertEvent("gold_move", "gold", {"move_pct": 6.0, "threshold_pct": 1.2}, now),
        ]
        fired = [d.event.trigger_type for d in alerts.gate(conn, config, candidates, now) if d.fired]
        assert fired == ["gold_move"]

    def test_a_user_set_level_outranks_a_threshold_alert(self, conn, config, now):
        config.alerts.budget.max_per_day = 1
        candidates = [
            AlertEvent("gold_move", "gold", {"move_pct": 6.0, "threshold_pct": 1.2}, now),
            AlertEvent("inr_price_levels", "level", {"level": 1.0, "direction": "above"}, now),
        ]
        fired = [d.event.trigger_type for d in alerts.gate(conn, config, candidates, now) if d.fired]
        assert fired == ["inr_price_levels"]

    def test_cooldown_blocks_a_repeat(self, conn, config, now):
        alerts.gate(conn, config, two_candidates(), now)
        cooldown = config.alerts.cooldown_for("gold_move")

        soon = now + timedelta(hours=cooldown / 2)
        decisions = alerts.gate(conn, config, two_candidates(soon), soon)
        assert all(not d.fired for d in decisions)
        assert any("cooling down" in (d.suppressed_by or "") for d in decisions)

    def test_cooldown_expires(self, conn, config, now):
        config.alerts.budget.max_per_day = 50
        alerts.gate(conn, config, two_candidates(), now)
        cooldown = config.alerts.cooldown_for("gold_move")

        later = now + timedelta(hours=cooldown + 0.1)
        decisions = alerts.gate(conn, config, [two_candidates(later)[0]], later)
        assert any(d.fired for d in decisions)

    def test_per_trigger_cooldown_overrides_the_global_one(self, config):
        # real_yield_move sets its own; dxy_move inherits the budget default.
        assert config.alerts.cooldown_for("real_yield_move") == (
            config.alerts.triggers.real_yield_move.cooldown_hours
        )
        assert config.alerts.cooldown_for("dxy_move") == config.alerts.budget.cooldown_hours

    def test_dry_run_commits_nothing(self, conn, config, now):
        decisions = alerts.gate(conn, config, two_candidates(), now, commit=False)
        assert any(d.fired for d in decisions)
        # No ledger entry, so no cooldown was consumed and the budget is intact.
        assert db.alerts_on_local_day(conn, config.tz, now) == 0
        assert db.last_fired(conn, "gold_move") is None

    def test_budget_resets_at_local_midnight(self, conn, config, now):
        config.alerts.budget.max_per_day = 1
        alerts.gate(conn, config, [two_candidates()[0]], now)
        assert db.alerts_on_local_day(conn, config.tz, now) == 1

        # 00:30 IST the next day.
        next_day = datetime(2026, 8, 18, 0, 30, tzinfo=config.tz).astimezone(UTC)
        assert db.alerts_on_local_day(conn, config.tz, next_day) == 0

    def test_a_broken_trigger_does_not_lose_the_others(self, conn, config, now, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("trigger bug")

        monkeypatch.setattr(alerts, "_gold_move", boom)
        seed(conn, "dxy", 99.0, 30)
        threshold = config.alerts.triggers.dxy_move.threshold_pct
        current = 99.0 * (1 + (threshold + 0.4) / 100)

        events = alerts.evaluate(conn, config, snap(dxy=current, gold_usd_oz=3400.0), now)
        assert any(e.trigger_type == "dxy_move" for e in events)


class TestSeverity:
    def test_normalises_across_different_units(self, now):
        gold = AlertEvent("gold_move", "", {"move_pct": 2.4, "threshold_pct": 1.2}, now)
        yields = AlertEvent("real_yield_move", "", {"move_bp": 24.0, "threshold_bp": 8.0}, now)
        # 2x threshold vs 3x threshold — the yield move ranks higher despite the
        # raw numbers being wildly different scales.
        assert alerts._severity(yields) > alerts._severity(gold)

    def test_conflict_scales_by_source_count(self, now):
        few = AlertEvent("conflict_escalation", "", {"domain_count": 3, "min_independent_sources": 3}, now)
        many = AlertEvent("conflict_escalation", "", {"domain_count": 9, "min_independent_sources": 3}, now)
        assert alerts._severity(many) > alerts._severity(few)

    def test_unknown_shape_gets_a_neutral_score(self, now):
        assert alerts._severity(AlertEvent("mystery", "", {}, now)) == 1.0
