"""The Claude spend guard.

The property that matters: the cap is enforced *before* a billable request is
made. A guard that only measures spend afterwards is not a cap, so these tests
assert no call reaches the client when a cap would be breached.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from goldagent import db, synthesize
from goldagent.models import Snapshot


class FakeUsage:
    def __init__(self, input_tokens=1000, output_tokens=300, cache_read=0, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class FakeClient:
    """Records whether messages.create was reached, and with what."""

    def __init__(self, *, text="Gold flat.", counted=1000, usage=None, stop_reason="end_turn"):
        self.calls: list[dict] = []
        self.count_calls = 0
        self._text = text
        self._counted = counted
        self._usage = usage or FakeUsage()
        self._stop_reason = stop_reason

        outer = self

        class Messages:
            def count_tokens(self, **kwargs):
                outer.count_calls += 1
                return SimpleNamespace(input_tokens=outer._counted)

            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=outer._text)],
                    usage=outer._usage,
                    stop_reason=outer._stop_reason,
                    stop_details=None,
                    model="claude-sonnet-4-6",
                )

        self.messages = Messages()


class TestEstimate:
    def test_prices_input_and_worst_case_output(self, config):
        client = FakeClient(counted=10_000)
        est = synthesize.estimate(client, config, "sys", "user", max_output_tokens=2000)

        spend = config.llm.spend
        expected = (
            10_000 / 1_000_000 * spend.input_usd_per_mtok
            + 2000 / 1_000_000 * spend.output_usd_per_mtok
        )
        assert est.input_tokens == 10_000
        assert est.projected_usd == pytest.approx(expected)

    def test_assumes_the_full_output_budget(self, config):
        """The projection must be pessimistic, not an average."""
        client = FakeClient(counted=1000)
        small = synthesize.estimate(client, config, "s", "u", max_output_tokens=100)
        large = synthesize.estimate(client, config, "s", "u", max_output_tokens=8000)
        assert large.projected_usd > small.projected_usd

    def test_falls_back_to_a_character_estimate_when_counting_fails(self, config, monkeypatch):
        class Broken(FakeClient):
            def __init__(self):
                super().__init__()
                outer = self

                class Messages:
                    def count_tokens(self, **kwargs):
                        raise RuntimeError("count_tokens 500")

                    def create(self, **kwargs):
                        outer.calls.append(kwargs)
                        return SimpleNamespace(
                            content=[SimpleNamespace(type="text", text="x")],
                            usage=FakeUsage(),
                            stop_reason="end_turn",
                            stop_details=None,
                            model="m",
                        )

                self.messages = Messages()

        est = synthesize.estimate(Broken(), config, "s" * 3500, "u" * 3500, max_output_tokens=100)
        # ~3.5 chars per token over 7000 chars.
        assert est.input_tokens == 2000
        assert est.projected_usd > 0


class TestCheckBudget:
    def test_passes_when_under_both_caps(self, conn, config, now):
        est = synthesize.Estimate(input_tokens=1000, max_output_tokens=500, projected_usd=0.01)
        synthesize.check_budget(conn, config, est, now)  # must not raise

    def test_blocks_when_over_the_per_run_cap(self, conn, config, now):
        over = config.llm.spend.per_run_usd_cap + 0.01
        est = synthesize.Estimate(input_tokens=1, max_output_tokens=1, projected_usd=over)

        with pytest.raises(synthesize.SpendCapExceeded) as caught:
            synthesize.check_budget(conn, config, est, now)
        assert "per-run cap" in str(caught.value)

    def test_blocks_when_the_day_total_would_breach(self, conn, config, now):
        daily = config.llm.spend.daily_usd_cap
        db.add_spend(conn, config.tz, daily - 0.01, now)

        est = synthesize.Estimate(input_tokens=1, max_output_tokens=1, projected_usd=0.05)
        with pytest.raises(synthesize.SpendCapExceeded) as caught:
            synthesize.check_budget(conn, config, est, now)

        message = str(caught.value)
        assert "daily cap" in message
        assert "No call was made" in message

    def test_the_daily_cap_resets_at_local_midnight(self, conn, config, now):
        from datetime import datetime

        db.add_spend(conn, config.tz, config.llm.spend.daily_usd_cap, now)
        est = synthesize.Estimate(input_tokens=1, max_output_tokens=1, projected_usd=0.05)

        with pytest.raises(synthesize.SpendCapExceeded):
            synthesize.check_budget(conn, config, est, now)

        next_day = datetime(2026, 8, 18, 0, 30, tzinfo=config.tz)
        synthesize.check_budget(conn, config, est, next_day)  # must not raise

    def test_exception_carries_the_numbers(self, conn, config, now):
        db.add_spend(conn, config.tz, 1.0, now)
        est = synthesize.Estimate(1, 1, projected_usd=99.0)
        with pytest.raises(synthesize.SpendCapExceeded) as caught:
            synthesize.check_budget(conn, config, est, now)
        assert caught.value.projected_usd == 99.0
        assert caught.value.spent_today_usd == pytest.approx(1.0)


class TestSynthesizeEnforcesTheGuard:
    def test_no_api_call_is_made_when_the_cap_would_break(self, conn, config, now):
        db.add_spend(conn, config.tz, config.llm.spend.daily_usd_cap, now)
        client = FakeClient()

        with pytest.raises(synthesize.SpendCapExceeded):
            synthesize.synthesize(
                client, conn, config, "sys", "user", "morning", now=now
            )

        # The whole point: the billable endpoint was never reached.
        assert client.calls == []

    def test_records_usage_and_advances_the_ledger(self, conn, config, now):
        client = FakeClient(usage=FakeUsage(input_tokens=8000, output_tokens=400))
        brief = synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)

        spend = config.llm.spend
        expected = (
            8000 / 1_000_000 * spend.input_usd_per_mtok
            + 400 / 1_000_000 * spend.output_usd_per_mtok
        )
        assert brief.usage.input_tokens == 8000
        assert brief.usage.output_tokens == 400
        assert brief.usage.cost_usd == pytest.approx(expected, abs=1e-9)
        assert db.spend_today(conn, config.tz, now) == pytest.approx(expected, abs=1e-9)

    def test_cached_tokens_are_priced_at_cache_rates(self, conn, config, now):
        client = FakeClient(
            usage=FakeUsage(input_tokens=100, output_tokens=100, cache_read=10_000)
        )
        brief = synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)

        spend = config.llm.spend
        # 10k cache-read tokens must cost a tenth of 10k fresh input tokens.
        full_price = 10_000 / 1_000_000 * spend.input_usd_per_mtok
        cache_component = brief.usage.cost_usd - (
            100 / 1_000_000 * spend.input_usd_per_mtok
            + 100 / 1_000_000 * spend.output_usd_per_mtok
        )
        assert cache_component == pytest.approx(full_price * 0.1, abs=1e-9)

    def test_sends_adaptive_thinking_and_effort_from_config(self, conn, config, now):
        client = FakeClient()
        synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)
        request = client.calls[0]

        assert request["model"] == config.llm.model
        assert request["thinking"] == {"type": "adaptive"}
        assert request["output_config"]["effort"] == config.llm.effort

    def test_thinking_disabled_is_honoured(self, conn, config, now):
        config.llm.thinking = "disabled"
        client = FakeClient()
        synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)
        assert client.calls[0]["thinking"] == {"type": "disabled"}

    def test_system_prompt_carries_a_cache_breakpoint(self, conn, config, now):
        client = FakeClient()
        synthesize.synthesize(client, conn, config, "the system prompt", "user", "morning", now=now)
        system = client.calls[0]["system"]

        assert system[0]["text"] == "the system prompt"
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_a_refusal_is_raised_not_delivered(self, conn, config, now):
        client = FakeClient(stop_reason="refusal")
        with pytest.raises(RuntimeError, match="declined"):
            synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)

    def test_empty_output_raises_with_a_hint(self, conn, config, now):
        client = FakeClient(text="", stop_reason="max_tokens")
        with pytest.raises(RuntimeError, match="max_output_tokens"):
            synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)

    def test_counting_happens_before_creating(self, conn, config, now):
        client = FakeClient()
        synthesize.synthesize(client, conn, config, "sys", "user", "morning", now=now)
        assert client.count_calls == 1
        assert len(client.calls) == 1


class TestDegradedNotice:
    def test_is_not_model_generated(self, config):
        """No client is involved, so a degraded run costs nothing."""
        snap = Snapshot()
        text = synthesize.degraded_notice(config, snap)
        assert "Degraded run" in text
        assert "no briefing" in text.lower()

    def test_names_failures_and_empties_separately(self, config):
        from goldagent.models import CollectorResult, Reading

        snap = Snapshot()
        snap.results["dead"] = CollectorResult(name="dead", stale=True, error="could not connect")
        snap.results["empty"] = CollectorResult(name="empty", notes=["nothing found"])
        snap.results["good"] = CollectorResult(
            name="good", readings=[Reading("gold_usd_oz", 3400.0, "USD/oz", "s")]
        )

        text = synthesize.degraded_notice(config, snap)
        assert "Failed: dead" in text
        assert "Returned nothing: empty" in text
        assert "With data: good" in text
        assert "$3,400.00/oz" in text
