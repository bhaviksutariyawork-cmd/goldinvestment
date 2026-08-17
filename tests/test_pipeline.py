"""Config, database, derived metrics, prompt assembly, commands and delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goldagent import collect, commands, db, synthesize
from goldagent import config as config_module
from goldagent.models import Brief, CollectorResult, Headline, Reading, Snapshot, Usage
from goldagent.telegram import Telegram, TelegramError, split_message
from tests.conftest import NOW

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


class TestConfig:
    def test_the_shipped_config_validates(self, config):
        assert config.version == 1
        assert config.investor.timezone == "Asia/Kolkata"

    def test_unknown_keys_are_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\ninvestor:\n  timezone: Asia/Kolkata\n  typo_here: 1\n")
        with pytest.raises(config_module.ConfigError):
            config_module.load(bad)

    def test_a_bad_timezone_is_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\ninvestor:\n  timezone: Mars/Olympus\n")
        with pytest.raises(config_module.ConfigError):
            config_module.load(bad)

    def test_missing_file_is_a_readable_error(self, tmp_path):
        with pytest.raises(config_module.ConfigError, match="not found"):
            config_module.load(tmp_path / "nope.yaml")

    def test_malformed_yaml_is_a_readable_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 1\n  bad indent: [\n")
        with pytest.raises(config_module.ConfigError, match="not valid YAML"):
            config_module.load(bad)

    def test_an_empty_list_key_is_tolerated(self, config):
        # `levels:` with only comments beneath parses as None, not [].
        assert config.alerts.triggers.inr_price_levels.levels == []

    def test_window_handles_midnight_wrap(self):
        from datetime import time

        window = config_module.Window(start=time(6, 0), end=time(2, 0))
        assert window.contains(time(23, 0)) is True
        assert window.contains(time(1, 0)) is True
        assert window.contains(time(3, 0)) is False
        assert window.contains(time(6, 0)) is True

    def test_window_without_wrap(self):
        from datetime import time

        window = config_module.Window(start=time(2, 0), end=time(6, 0))
        assert window.contains(time(3, 0)) is True
        assert window.contains(time(23, 0)) is False

    def test_duty_stack_compounds_rather_than_sums(self):
        from datetime import date

        duty = config_module.DutyStack(
            import_duty_pct=6.0, agri_infra_cess_pct=5.0, gst_pct=3.0, as_of=date(2026, 1, 1)
        )
        # 1.11 * 1.03 = 1.1433, not 1.14
        assert duty.landed_multiplier == pytest.approx(1.1433)
        assert duty.effective_pct == pytest.approx(14.33)
        assert duty.effective_pct > 14.0

    def test_invalid_day_name_is_rejected(self):
        from datetime import time

        with pytest.raises(ValueError, match="unknown day"):
            config_module.Briefing(ist_time=time(7, 30), days=["mon", "funday"])

    def test_runs_on_maps_monday_to_zero(self):
        from datetime import time

        briefing = config_module.Briefing(ist_time=time(7, 30), days=["mon", "fri"])
        assert briefing.runs_on(0) is True
        assert briefing.runs_on(4) is True
        assert briefing.runs_on(6) is False

    def test_season_event_needs_exactly_one_shape(self):
        from datetime import date

        with pytest.raises(ValueError, match="either"):
            config_module.SeasonEvent(name="x")
        with pytest.raises(ValueError, match="either"):
            config_module.SeasonEvent(
                name="x", date=date(2026, 1, 1), start=date(2026, 1, 1), end=date(2026, 2, 1)
            )
        with pytest.raises(ValueError, match="ends before"):
            config_module.SeasonEvent(name="x", start=date(2026, 2, 1), end=date(2026, 1, 1))


class TestSecrets:
    def test_missing_secret_explains_where_to_set_it(self):
        secrets = config_module.Secrets(env={})
        with pytest.raises(config_module.MissingSecret, match="README"):
            _ = secrets.telegram_bot_token

    def test_blank_is_treated_as_missing(self):
        secrets = config_module.Secrets(env={"TELEGRAM_CHAT_ID": "   "})
        assert secrets.get("TELEGRAM_CHAT_ID") is None

    def test_optional_secret_returns_none_without_raising(self):
        assert config_module.Secrets(env={}).fred_api_key is None

    def test_present_secret_is_returned(self):
        secrets = config_module.Secrets(env={"ANTHROPIC_API_KEY": "sk-test"})
        assert secrets.anthropic_api_key == "sk-test"


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


class TestDatabase:
    def test_null_readings_are_not_stored(self, conn):
        written = db.record_readings(conn, [Reading("m", None, "u", "s")])
        assert written == 0

    def test_window_baseline_prefers_a_point_before_the_window(self, conn, now):
        db.record_readings(conn, [Reading("m", 100.0, "u", "s", now - timedelta(hours=6))])
        db.record_readings(conn, [Reading("m", 110.0, "u", "s", now - timedelta(hours=2))])

        baseline = db.window_baseline(conn, "m", 4, now)
        assert baseline.value == 100.0

    def test_window_baseline_falls_back_to_the_oldest_inside(self, conn, now):
        db.record_readings(conn, [Reading("m", 105.0, "u", "s", now - timedelta(hours=3))])
        db.record_readings(conn, [Reading("m", 110.0, "u", "s", now - timedelta(hours=1))])

        # Nothing before the 4h mark, so the earliest inside is used. That
        # understates the move rather than inventing one.
        assert db.window_baseline(conn, "m", 4, now).value == 105.0

    def test_window_baseline_is_none_on_an_empty_db(self, conn, now):
        assert db.window_baseline(conn, "m", 4, now) is None

    def test_daily_series_keeps_the_last_reading_of_each_local_day(self, conn, config, now):
        day = now.astimezone(config.tz).date()
        for hour, value in ((2, 100.0), (14, 105.0), (22, 103.0)):
            stamp = datetime(day.year, day.month, day.day, hour, tzinfo=config.tz)
            db.record_readings(conn, [Reading("m", value, "u", "s", stamp.astimezone(UTC))])

        series = db.daily_series(conn, "m", 7, config.tz, now=now + timedelta(hours=6))
        assert len(series) == 1
        assert series[0][1] == 103.0

    def test_headline_novelty(self, conn, now):
        headline = Headline("Title", "https://x.test/a", "x.test", "src")
        assert len(db.upsert_headlines(conn, [headline], now)) == 1
        assert headline.is_new is True

        again = Headline("Title updated", "https://x.test/a", "x.test", "src")
        assert len(db.upsert_headlines(conn, [again], now)) == 0
        assert again.is_new is False

    def test_recent_headlines_respects_the_window(self, conn, now):
        old = Headline("Old", "https://x.test/old", "x.test", "s")
        db.upsert_headlines(conn, [old], now - timedelta(hours=10))
        fresh = Headline("Fresh", "https://x.test/new", "x.test", "s")
        db.upsert_headlines(conn, [fresh], now)

        titles = [h.title for h in db.recent_headlines(conn, 2, now)]
        assert titles == ["Fresh"]

    def test_last_brief_excludes_alerts_when_unfiltered(self, conn, now):
        db.record_brief(conn, Brief("morning", "morning text", ts=now - timedelta(hours=3)))
        db.record_brief(conn, Brief("alert", "alert text", ts=now - timedelta(hours=1)))

        assert db.last_brief(conn).mode == "morning"
        assert db.last_brief(conn, "alert").mode == "alert"

    def test_spend_accumulates_within_a_local_day(self, conn, config, now):
        db.add_spend(conn, config.tz, 0.10, now)
        total = db.add_spend(conn, config.tz, 0.05, now)
        assert total == pytest.approx(0.15)

    def test_mute_state_roundtrips(self, conn, now):
        assert db.is_muted(conn, now) is False
        db.set_mute_until(conn, now + timedelta(hours=1))
        assert db.is_muted(conn, now) is True
        assert db.is_muted(conn, now + timedelta(hours=2)) is False
        db.set_mute_until(conn, None)
        assert db.is_muted(conn, now) is False

    def test_telegram_offset_roundtrips_and_tolerates_junk(self, conn):
        assert db.get_telegram_offset(conn) == 0
        db.set_telegram_offset(conn, 42)
        assert db.get_telegram_offset(conn) == 42
        db.kv_set(conn, "telegram_offset", "not-a-number")
        assert db.get_telegram_offset(conn) == 0

    def test_user_levels_reject_duplicates_and_can_be_removed(self, conn, now):
        assert db.add_user_level(conn, 100.0, "above", "note", now) is True
        assert db.add_user_level(conn, 100.0, "below", "other", now) is False
        assert len(db.list_user_levels(conn)) == 1
        assert db.remove_user_level(conn, 100.0) is True
        assert db.remove_user_level(conn, 100.0) is False

    def test_removing_a_level_clears_its_hit_state(self, conn, now):
        db.add_user_level(conn, 100.0, "above", "", now)
        db.mark_level_hit(conn, 100.0, "above", now)
        db.remove_user_level(conn, 100.0)
        assert db.level_already_hit(conn, 100.0, "above") is False

    def test_prune_deletes_beyond_the_window(self, conn, now):
        db.record_readings(conn, [Reading("m", 1.0, "u", "s", now - timedelta(days=500))])
        db.record_readings(conn, [Reading("m", 2.0, "u", "s", now)])

        counts = db.prune(conn, days=400, now=now)
        assert counts["readings"] == 1
        assert len(db.history(conn, "m", 1000, now=now)) == 1


# --------------------------------------------------------------------------- #
# Derived metrics
# --------------------------------------------------------------------------- #


def build(**metrics) -> Snapshot:
    snap = Snapshot(collected_at=NOW)
    snap.results["src"] = CollectorResult(
        name="src",
        readings=[
            Reading(m, v, "u", "s", NOW) if not isinstance(v, tuple)
            else Reading(m, v[0], "u", "s", NOW, extra={"change_pct": v[1]})
            for m, v in metrics.items()
        ],
    )
    return snap


class TestDerivedMetrics:
    def test_inr_price_matches_hand_arithmetic(self, config):
        snap = build(gold_usd_oz=3400.0, usdinr=87.50)
        collect._derive(snap, config)

        duty = config.india.duty_stack_fallback
        pre = 3400.0 * 87.50 / collect.TROY_OUNCE_GRAMS * 10.0
        assert snap.value("gold_inr_per_10g") == pytest.approx(
            pre * duty.landed_multiplier, abs=0.01
        )

    def test_ex_gst_figure_excludes_only_gst(self, config):
        snap = build(gold_usd_oz=3400.0, usdinr=87.50)
        collect._derive(snap, config)

        duty = config.india.duty_stack_fallback
        all_in = snap.value("gold_inr_per_10g")
        ex_gst = snap.value("gold_inr_per_10g_ex_gst")
        assert all_in / ex_gst == pytest.approx(1 + duty.gst_pct / 100.0, abs=1e-6)

    def test_physical_premium_compares_on_the_ex_gst_basis(self, config):
        """IBJA quotes exclude GST, so the comparison must too."""
        snap = build(gold_usd_oz=3400.0, usdinr=87.50)
        collect._derive(snap, config)
        ex_gst = snap.value("gold_inr_per_10g_ex_gst")

        snap2 = build(gold_usd_oz=3400.0, usdinr=87.50, ibja_inr_per_10g=ex_gst)
        collect._derive(snap2, config)
        # An IBJA quote exactly equal to the ex-GST cost is a zero premium.
        assert snap2.value("india_physical_premium_pct") == pytest.approx(0.0, abs=1e-6)

    def test_a_wide_premium_flags_a_stale_duty_stack(self, config):
        snap = build(gold_usd_oz=3400.0, usdinr=87.50)
        collect._derive(snap, config)
        ex_gst = snap.value("gold_inr_per_10g_ex_gst")

        snap2 = build(gold_usd_oz=3400.0, usdinr=87.50, ibja_inr_per_10g=ex_gst * 1.09)
        collect._derive(snap2, config)
        notes = " ".join(snap2.results["derived"].notes)
        assert "duty stack is out of date" in notes

    def test_gold_silver_ratio(self, config):
        snap = build(gold_usd_oz=3400.0, silver_usd_oz=40.0)
        collect._derive(snap, config)
        assert snap.value("gold_silver_ratio") == pytest.approx(85.0)

    def test_direction_divergence_is_flagged(self, config):
        # USD gold down, rupee weaker by more — INR price up.
        snap = build(gold_usd_oz=(3400.0, -0.40), usdinr=(87.50, 0.65))
        collect._derive(snap, config)

        assert snap.value("usd_inr_direction_divergence") == 1.0
        notes = " ".join(snap.results["derived"].notes)
        assert "DIVERGENCE" in notes
        assert "not a footnote" in notes

    def test_same_direction_is_not_flagged(self, config):
        snap = build(gold_usd_oz=(3400.0, 0.80), usdinr=(87.50, 0.10))
        collect._derive(snap, config)
        assert snap.value("usd_inr_direction_divergence") == 0.0

    def test_inr_change_compounds_the_two_legs(self, config):
        snap = build(gold_usd_oz=(3400.0, 1.0), usdinr=(87.50, 1.0))
        collect._derive(snap, config)
        reading = next(
            r for r in snap.results["derived"].readings
            if r.metric == "usd_inr_direction_divergence"
        )
        # (1.01 * 1.01 - 1) = 2.01%, not 2.00%
        assert reading.extra["inr_gold_change_pct"] == pytest.approx(2.01, abs=0.001)

    def test_missing_fx_leaves_inr_unset_rather_than_guessing(self, config):
        snap = build(gold_usd_oz=3400.0)
        collect._derive(snap, config)
        assert snap.value("gold_inr_per_10g") is None

    def test_seasonality_flags_an_upcoming_event(self, config):
        from datetime import date

        note = collect._seasonality_note(config, today=date(2026, 10, 25))
        assert note is not None
        assert "Dhanteras" in note or "wedding" in note

    def test_seasonality_silent_when_nothing_is_near(self, config):
        from datetime import date

        assert collect._seasonality_note(config, today=date(2026, 6, 1)) is None


class TestDegradedGate:
    def test_notes_only_collectors_do_not_clear_the_gate(self, config):
        """The bug this guards: 8 'nothing found' notes must not look healthy."""
        snap = Snapshot()
        for index in range(12):
            snap.results[f"c{index}"] = CollectorResult(
                name=f"c{index}", notes=["nothing found"]
            )

        assert snap.ok_count == 12
        assert snap.data_count == 0
        assert collect.degraded(config, snap) is True

    def test_enough_data_carrying_collectors_clears_the_gate(self, config):
        snap = Snapshot()
        minimum = config.data_quality.min_collectors_ok
        for index in range(minimum):
            snap.results[f"c{index}"] = CollectorResult(
                name=f"c{index}", readings=[Reading(f"m{index}", 1.0, "u", "s")]
            )
        assert collect.degraded(config, snap) is False


class TestStalenessReport:
    def test_flags_a_reading_past_its_budget(self, config):
        snap = Snapshot()
        old = NOW - timedelta(hours=config.data_quality.staleness_hours.price + 5)
        snap.results["price_spot"] = CollectorResult(
            name="price_spot", readings=[Reading("gold_usd_oz", 3400.0, "USD/oz", "s", old)]
        )
        lines = collect.staleness_report(config, snap, NOW)
        assert any("price_spot" in line and "freshness budget" in line for line in lines)

    def test_fresh_readings_produce_no_lines(self, config):
        snap = Snapshot()
        snap.results["price_spot"] = CollectorResult(
            name="price_spot", readings=[Reading("gold_usd_oz", 3400.0, "USD/oz", "s", NOW)]
        )
        assert collect.staleness_report(config, snap, NOW) == []


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #


class TestPromptAssembly:
    def test_names_every_failed_collector(self, config, conn, now):
        snap = build(gold_usd_oz=3400.0)
        snap.results["fred"] = CollectorResult(
            name="fred", stale=True, error="FRED_API_KEY is not set"
        )
        message = synthesize.build_user_message(config, snap, conn, "morning", now=now)

        assert "DATA GAPS" in message
        assert "DO NOT FILL THEM FROM MEMORY" in message
        assert "FRED_API_KEY is not set" in message

    def test_includes_the_previous_brief_with_a_do_not_repeat_instruction(
        self, config, conn, now
    ):
        previous = Brief("morning", "Yesterday: gold flat.", ts=now - timedelta(days=1))
        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "morning", previous=previous, now=now
        )
        assert "DO NOT REPEAT THIS" in message
        assert "Yesterday: gold flat." in message

    def test_says_so_when_there_is_no_previous_brief(self, config, conn, now):
        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "morning", now=now
        )
        assert "None on record" in message

    def test_separates_new_headlines_from_seen_ones(self, config, conn, now):
        snap = build(gold_usd_oz=3400.0)
        new = Headline("Fresh story", "https://x.test/1", "x.test", "Reuters")
        old = Headline("Known story", "https://x.test/2", "x.test", "Reuters")
        old.first_seen = now - timedelta(days=1)
        snap.results["rss"] = CollectorResult(name="rss", headlines=[new, old])

        message = synthesize.build_user_message(config, snap, conn, "morning", now=now)
        assert "New since the last run (1)" in message
        assert "Fresh story" in message
        assert "Already seen in an earlier run (1, for context only)" in message
        assert "Known story" in message

    def test_no_headlines_says_unavailable_not_quiet(self, config, conn, now):
        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "morning", now=now
        )
        assert "Treat the news leg as unavailable" in message
        assert "do not conclude that news flow was quiet" in message

    def test_carries_the_word_cap_for_the_mode(self, config, conn, now):
        for mode in ("morning", "evening", "weekly"):
            message = synthesize.build_user_message(
                config, build(gold_usd_oz=3400.0), conn, mode, now=now
            )
            assert f"Hard cap {config.briefing(mode).max_words} words" in message

    def test_alert_mode_requests_the_three_line_format(self, config, conn, now):
        from goldagent.models import AlertEvent

        event = AlertEvent("gold_move", "gold +1.8%", {"move_pct": 1.8}, now)
        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "alert", alert=event, now=now
        )
        assert "three-line format" in message
        assert "gold +1.8%" in message

    def test_readings_carry_their_source(self, config, conn, now):
        snap = Snapshot(collected_at=NOW)
        snap.results["s"] = CollectorResult(
            name="s",
            readings=[Reading("gold_usd_oz", 3400.0, "USD/oz", "Yahoo Finance XAUUSD=X", NOW)],
        )
        message = synthesize.build_user_message(config, snap, conn, "morning", now=now)
        assert "source: Yahoo Finance XAUUSD=X" in message

    def test_history_absence_forbids_inferring_a_trend(self, config, conn, now):
        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "morning", now=now
        )
        assert "Do not infer a trend" in message

    def test_history_renders_when_present(self, config, conn, now):
        for days_ago in range(5):
            stamp = now - timedelta(days=days_ago)
            db.record_readings(conn, [Reading("gold_usd_oz", 3300.0 + days_ago, "USD/oz", "s", stamp)])

        message = synthesize.build_user_message(
            config, build(gold_usd_oz=3400.0), conn, "morning", now=now
        )
        assert "TRAILING" in message
        assert "over the window" in message


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


class TestCommands:
    def ctx(self, conn, config, now, client=None):
        return commands.Context(
            conn=conn, config=config, client=client, system_prompt="sys", now=now
        )

    def test_non_command_text_is_ignored(self, conn, config, now):
        assert commands.dispatch("just chatting", self.ctx(conn, config, now)) is None

    def test_unknown_command_offers_help(self, conn, config, now):
        reply = commands.dispatch("/nope", self.ctx(conn, config, now))
        assert "Unknown command" in reply.text
        assert "/now" in reply.text

    def test_botname_suffix_is_stripped(self, conn, config, now):
        reply = commands.dispatch("/help@goldbot", self.ctx(conn, config, now))
        assert "Gold agent commands" in reply.text

    def test_set_parses_indian_lakh_notation(self, conn, config, now):
        commands.dispatch("/set 1,15,000", self.ctx(conn, config, now))
        assert [level["value"] for level in db.list_user_levels(conn)] == [115000.0]

    def test_set_rejects_an_out_of_band_price(self, conn, config, now):
        reply = commands.dispatch("/set 118", self.ctx(conn, config, now))
        assert "outside the plausible band" in reply.text
        assert db.list_user_levels(conn) == []

    def test_set_extracts_direction_and_note(self, conn, config, now):
        commands.dispatch("/set 118000 above trim tranche one", self.ctx(conn, config, now))
        level = db.list_user_levels(conn)[0]
        assert level["direction"] == "above"
        assert "trim tranche one" in level["note"]

    def test_set_is_idempotent(self, conn, config, now):
        ctx = self.ctx(conn, config, now)
        commands.dispatch("/set 118000", ctx)
        reply = commands.dispatch("/set 118000", ctx)
        assert "already on your list" in reply.text
        assert len(db.list_user_levels(conn)) == 1

    def test_mute_is_capped_by_config(self, conn, config, now):
        cap = config.telegram.mute.max_duration_hours
        reply = commands.dispatch(f"/mute {cap + 100:g}h", self.ctx(conn, config, now))
        assert "capped" in reply.text

        until = db.get_mute_until(conn)
        assert (until - now).total_seconds() == pytest.approx(cap * 3600, abs=2)

    @pytest.mark.parametrize(
        ("text", "hours"), [("4h", 4.0), ("90m", 1.5), ("2", 2.0), ("1.5h", 1.5)]
    )
    def test_mute_duration_parsing(self, text, hours):
        assert commands._parse_duration(text) == hours

    @pytest.mark.parametrize("text", ["", "junk", "4 days"])
    def test_mute_rejects_unparseable_durations(self, text):
        assert commands._parse_duration(text) is None

    def test_unmute_clears(self, conn, config, now):
        db.set_mute_until(conn, now + timedelta(hours=4))
        commands.dispatch("/unmute", self.ctx(conn, config, now))
        assert db.is_muted(conn, now) is False

    def test_levels_needs_no_model_call(self, conn, config, now):
        db.record_readings(conn, [Reading("gold_inr_per_10g", 109000.0, "INR/10g", "s", now)])
        reply = commands.dispatch("/levels", self.ctx(conn, config, now, client=None))
        assert reply.called_model is False
        assert "Levels" in reply.text

    def test_levels_on_an_empty_db_says_so(self, conn, config, now):
        reply = commands.dispatch("/levels", self.ctx(conn, config, now))
        assert "No stored price history" in reply.text

    def test_levels_shows_both_config_and_set_levels(self, conn, config, now):
        from goldagent.config import PriceLevel

        config.alerts.triggers.inr_price_levels.levels = [
            PriceLevel(value=120000.0, direction="above", note="yaml level")
        ]
        db.add_user_level(conn, 115000.0, "below", "set level", now)
        db.record_readings(conn, [Reading("gold_inr_per_10g", 109000.0, "INR/10g", "s", now)])

        reply = commands.dispatch("/levels", self.ctx(conn, config, now))
        assert "120,000" in reply.text
        assert "115,000" in reply.text

    def test_round_levels_bracket_the_price(self):
        levels = commands._round_levels(3412.0, step=50)
        assert 3400.0 in levels
        assert min(levels) < 3412.0 < max(levels)

    def test_spend_cap_during_a_command_is_reported_not_raised(self, conn, config, now):
        db.add_spend(conn, config.tz, config.llm.spend.daily_usd_cap, now)

        class Client:
            class messages:
                @staticmethod
                def count_tokens(**kwargs):
                    from types import SimpleNamespace

                    return SimpleNamespace(input_tokens=1000)

                @staticmethod
                def create(**kwargs):
                    raise AssertionError("must not be called when the cap is reached")

        # /why goes through the model, so the guard should trip.
        import goldagent.collect as collect_module

        original = collect_module.quick
        collect_module.quick = lambda cfg: build(gold_usd_oz=3400.0)
        try:
            reply = commands.dispatch("/why", self.ctx(conn, config, now, client=Client()))
        finally:
            collect_module.quick = original

        assert "budget reached" in reply.text

    def test_a_handler_exception_becomes_a_reply(self, conn, config, now, monkeypatch):
        monkeypatch.setattr(
            commands, "_levels", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        reply = commands.dispatch("/levels", self.ctx(conn, config, now))
        assert "failed" in reply.text
        assert "boom" in reply.text


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json or {}))
        outcome = self.results.pop(0) if self.results else {"ok": True, "result": {"message_id": 1}}
        if isinstance(outcome, Exception):
            raise outcome

        class R:
            status_code = 200 if outcome.get("ok") else 400

            def json(self_inner):
                return outcome

        return R()


class TestTelegram:
    def test_send_posts_once_for_a_short_message(self):
        session = FakeSession([{"ok": True, "result": {"message_id": 7}}])
        bot = Telegram("token", "123", session=session)
        assert bot.send("hello") == [7]
        assert len(session.posts) == 1
        assert session.posts[0][1]["parse_mode"] == "Markdown"

    def test_markdown_rejection_retries_as_plain_text(self):
        session = FakeSession(
            [
                {"ok": False, "description": "Bad Request: can't parse entities"},
                {"ok": True, "result": {"message_id": 9}},
            ]
        )
        bot = Telegram("token", "123", session=session)
        assert bot.send("unbalanced *asterisk") == [9]

        assert len(session.posts) == 2
        # The retry must drop parse_mode, not the content.
        assert "parse_mode" in session.posts[0][1]
        assert "parse_mode" not in session.posts[1][1]
        assert session.posts[1][1]["text"] == "unbalanced *asterisk"

    def test_a_long_message_is_split_and_marked(self, monkeypatch):
        monkeypatch.setattr("goldagent.telegram.time.sleep", lambda _s: None)
        session = FakeSession([])
        bot = Telegram("token", "123", session=session)
        bot.send("x" * 9000)

        assert len(session.posts) == 3
        assert "part 1 of 3" in session.posts[0][1]["text"]

    def test_network_failure_raises_telegram_error(self):
        import requests

        session = FakeSession([requests.ConnectionError("down"), requests.ConnectionError("down")])
        bot = Telegram("token", "123", session=session)
        with pytest.raises(TelegramError, match="failed to reach Telegram"):
            bot.send("hello")

    def test_poll_parses_messages(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 100,
                            "message": {
                                "message_id": 5,
                                "text": "/now",
                                "chat": {"id": 123},
                                "from": {"id": 456},
                            },
                        }
                    ],
                }
            ]
        )
        bot = Telegram("token", "123", session=session)
        updates = bot.poll(offset=99)

        assert len(updates) == 1
        assert updates[0].text == "/now"
        assert updates[0].chat_id == "123"
        assert session.posts[0][1]["offset"] == 99

    def test_poll_still_counts_a_non_text_update_so_the_offset_advances(self):
        session = FakeSession(
            [{"ok": True, "result": [{"update_id": 200, "message": {"chat": {"id": 123}}}]}]
        )
        bot = Telegram("token", "123", session=session)
        updates = bot.poll()
        assert len(updates) == 1
        assert updates[0].update_id == 200
        assert updates[0].text == ""


class TestSplitMessage:
    def test_short_text_is_one_chunk(self):
        assert split_message("hello") == ["hello"]

    def test_empty_text_yields_one_empty_chunk(self):
        assert split_message("") == [""]

    def test_splits_on_paragraph_boundaries(self):
        text = "\n\n".join(["p" * 900 for _ in range(5)])
        chunks = split_message(text, limit=2000)
        assert len(chunks) > 1
        assert all(len(chunk) <= 2000 for chunk in chunks)

    def test_an_oversized_line_is_hard_split_losslessly(self):
        text = "z" * 5000
        chunks = split_message(text, limit=1000)
        assert "".join(chunks) == text
        assert all(len(chunk) <= 1000 for chunk in chunks)


class TestFailureNotice:
    def test_includes_mode_error_and_run_url(self):
        from goldagent.telegram import failure_notice

        text = failure_notice("morning", "ConnectionError: nope", run_url="https://gh/run/1")
        assert "morning" in text
        assert "ConnectionError" in text
        assert "https://gh/run/1" in text
        assert "State is unchanged" in text


# --------------------------------------------------------------------------- #
# Usage accounting
# --------------------------------------------------------------------------- #


class TestUsage:
    def test_price_helper_matches_configured_rates(self, config):
        cost = synthesize.price(config, 1_000_000, 1_000_000)
        assert cost == pytest.approx(
            config.llm.spend.input_usd_per_mtok + config.llm.spend.output_usd_per_mtok
        )

    def test_brief_defaults_to_zero_usage(self):
        brief = Brief("morning", "text")
        assert brief.usage == Usage()
        assert brief.usage.cost_usd == 0.0
