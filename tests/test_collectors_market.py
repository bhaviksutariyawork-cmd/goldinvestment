"""Price, FX, macro and positioning collectors, against captured responses."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from collectors import cot, dollar_index, fedwatch, fred, fx, gld_holdings, price_spot, silver
from collectors._yahoo import Quote


def quote_stub(**by_symbol: Quote):
    """Return a fake `quote()` that serves the given symbols and fails others."""

    def fake(symbol: str, **kwargs):
        if symbol in by_symbol:
            return by_symbol[symbol]
        raise RuntimeError(f"no fixture for {symbol}")

    return fake


def q(symbol: str, last: float, previous: float | None = None) -> Quote:
    return Quote(
        symbol=symbol,
        last=last,
        previous_close=previous,
        ts=datetime(2026, 8, 17, 5, 0, tzinfo=UTC),
        via="test",
    )


# --------------------------------------------------------------------------- #
# Spot gold and the two-source cross-check
# --------------------------------------------------------------------------- #


class TestPriceSpot:
    def test_both_symbols_resolve(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.price_spot.quote",
            quote_stub(**{"XAUUSD=X": q("XAUUSD=X", 3400.0, 3380.0), "GC=F": q("GC=F", 3402.0)}),
        )
        result = price_spot.collect(divergence_pct=0.3)

        assert result.ok
        assert result.reading("gold_usd_oz").value == 3400.0
        assert result.reading("gold_future_usd_oz").value == 3402.0
        assert result.reading("gold_usd_oz").extra["change_pct"] == pytest.approx(0.5917, abs=1e-3)

    def test_divergence_within_tolerance_is_silent(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.price_spot.quote",
            quote_stub(**{"XAUUSD=X": q("XAUUSD=X", 3400.0), "GC=F": q("GC=F", 3403.0)}),
        )
        result = price_spot.collect(divergence_pct=0.3)
        gap = result.reading("gold_spot_future_divergence_pct").value
        assert gap == pytest.approx(0.0882, abs=1e-3)
        assert not any("DIVERGENCE" in note for note in result.notes)

    def test_divergence_beyond_tolerance_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.price_spot.quote",
            quote_stub(**{"XAUUSD=X": q("XAUUSD=X", 3400.0), "GC=F": q("GC=F", 3430.0)}),
        )
        result = price_spot.collect(divergence_pct=0.3)
        assert result.reading("gold_spot_future_divergence_pct").value == pytest.approx(
            0.882, abs=1e-3
        )
        assert any("DIVERGENCE" in note for note in result.notes)

    def test_one_dead_symbol_still_yields_a_price_with_a_caveat(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.price_spot.quote", quote_stub(**{"XAUUSD=X": q("XAUUSD=X", 3400.0)})
        )
        result = price_spot.collect()

        assert result.ok
        assert result.reading("gold_usd_oz").value == 3400.0
        assert result.reading("gold_future_usd_oz") is None
        # The reader must learn the cross-check did not happen.
        assert any("cross-check" in note for note in result.notes)

    def test_both_symbols_dead_fails_with_both_reasons(self, monkeypatch):
        monkeypatch.setattr("collectors.price_spot.quote", quote_stub())
        result = price_spot.collect()

        assert result.stale is True
        assert "XAUUSD=X" in result.error
        assert "GC=F" in result.error


class TestSimpleQuoteCollectors:
    @pytest.mark.parametrize(
        ("module", "patch_target", "symbol", "metric"),
        [
            (fx, "collectors.fx.quote", "USDINR=X", "usdinr"),
            (dollar_index, "collectors.dollar_index.quote", "DX-Y.NYB", "dxy"),
            (silver, "collectors.silver.quote", "SI=F", "silver_usd_oz"),
        ],
    )
    def test_reads_the_value_and_change(self, monkeypatch, module, patch_target, symbol, metric):
        monkeypatch.setattr(patch_target, quote_stub(**{symbol: q(symbol, 100.0, 99.0)}))
        result = module.collect()

        assert result.ok
        assert result.reading(metric).value == 100.0
        assert result.reading(metric).extra["change_pct"] == pytest.approx(1.0101, abs=1e-3)

    def test_a_dead_symbol_degrades(self, monkeypatch):
        monkeypatch.setattr("collectors.fx.quote", quote_stub())
        result = fx.collect()
        assert result.stale is True
        assert result.reading("usdinr") is None


# --------------------------------------------------------------------------- #
# FRED
# --------------------------------------------------------------------------- #


class TestFred:
    def test_no_api_key_degrades_with_a_pointer_to_get_one(self):
        result = fred.collect(api_key=None)
        assert result.stale is True
        assert "FRED_API_KEY" in result.error
        assert "fredaccount.stlouisfed.org" in result.error

    def test_parses_series_and_computes_the_basis_point_change(self, monkeypatch, fixture_text):
        payload = json.loads(fixture_text("fred_dfii10.json"))
        monkeypatch.setattr("collectors.fred.get_json", lambda *a, **k: payload)

        result = fred.collect(api_key="key")
        real = result.reading("real_yield_10y")

        assert real.value == 1.78
        assert real.extra["observation_date"] == "2026-08-15"
        # 1.78 - 1.90 = -0.12 pct = -12bp
        assert real.extra["change_bp"] == pytest.approx(-12.0)
        assert "FRED series DFII10" in real.source

    def test_dot_placeholders_are_skipped(self, monkeypatch, fixture_text):
        payload = json.loads(fixture_text("fred_with_gaps.json"))
        monkeypatch.setattr("collectors.fred.get_json", lambda *a, **k: payload)

        result = fred.collect(api_key="key")
        real = result.reading("real_yield_10y")
        # First two observations are ".", so the newest real print is the 14th.
        assert real.value == 1.85
        assert real.extra["observation_date"] == "2026-08-14"
        assert real.extra["previous_value"] == 1.91

    def test_fred_error_message_is_surfaced(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.fred.get_json",
            lambda *a, **k: {"error_message": "Bad Request. The value for variable api_key is not registered."},
        )
        result = fred.collect(api_key="bad")
        assert result.stale is True
        assert "not registered" in result.error

    def test_one_dead_series_does_not_lose_the_others(self, monkeypatch, fixture_text):
        payload = json.loads(fixture_text("fred_dfii10.json"))
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if kwargs.get("params", {}).get("series_id") == "DGS10":
                raise RuntimeError("upstream 500")
            return payload

        monkeypatch.setattr("collectors.fred.get_json", flaky)
        result = fred.collect(api_key="key")

        assert result.ok
        assert result.reading("real_yield_10y") is not None
        assert result.reading("nominal_yield_10y") is None
        assert any("DGS10" in note for note in result.notes)

    def test_stale_observation_is_flagged(self, monkeypatch):
        old = {"observations": [{"date": "2026-01-02", "value": "1.50"}]}
        monkeypatch.setattr("collectors.fred.get_json", lambda *a, **k: old)
        result = fred.collect(api_key="key")
        assert any("days old" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# GLD tonnage
# --------------------------------------------------------------------------- #


class TestGldHoldings:
    def _response(self, text: str):
        class R:
            def __init__(self, body):
                self.text = body

        return R(text)

    def test_parses_tonnage_and_daily_delta(self, monkeypatch, fixture_text):
        csv_text = fixture_text("gld_archive.csv")
        monkeypatch.setattr(
            "collectors.gld_holdings.get", lambda *a, **k: self._response(csv_text)
        )
        result = gld_holdings.collect()

        tonnes = result.reading("gld_tonnes")
        assert tonnes.value == 905.82
        assert tonnes.extra["as_of"] == "2026-08-15"
        # 905.82 - 904.93
        assert result.reading("gld_tonnes_delta").value == pytest.approx(0.89, abs=1e-6)
        assert tonnes.extra["delta_tonnes_5d"] == pytest.approx(905.82 - 898.86, abs=1e-6)

    def test_column_layout_change_fails_clearly(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.gld_holdings.get",
            lambda *a, **k: self._response("some,unrelated,columns\n1,2,3\n"),
        )
        result = gld_holdings.collect()
        assert result.stale is True
        assert "columns" in result.error

    def test_implausible_tonnage_is_rejected(self, monkeypatch):
        body = "Date,Tonnes in the Trust\n15-Aug-2026,99999999\n14-Aug-2026,88888888\n"
        monkeypatch.setattr("collectors.gld_holdings.get", lambda *a, **k: self._response(body))
        result = gld_holdings.collect()
        assert result.stale is True


# --------------------------------------------------------------------------- #
# CFTC COT
# --------------------------------------------------------------------------- #


class TestCot:
    def test_computes_net_long_and_week_change(self, monkeypatch, fixture_text):
        rows = json.loads(fixture_text("cftc_gold.json"))
        monkeypatch.setattr("collectors.cot.get_json", lambda *a, **k: rows)

        result = cot.collect()
        net = result.reading("cot_managed_money_net_long")

        assert net.value == 215430 - 48210
        assert net.extra["report_date"] == "2026-08-11"
        assert net.extra["week_change"] == (215430 - 48210) - (198220 - 52110)
        assert net.extra["open_interest"] == 512300
        # Latest is the highest in the window, so top percentile.
        assert net.extra["percentile_in_window"] == 100.0

    def test_always_states_the_reading_is_weekly_and_lagged(self, monkeypatch, fixture_text):
        rows = json.loads(fixture_text("cftc_gold.json"))
        monkeypatch.setattr("collectors.cot.get_json", lambda *a, **k: rows)
        result = cot.collect()
        assert any("weekly" in note and "Not a current reading" in note for note in result.notes)

    def test_empty_response_degrades(self, monkeypatch):
        monkeypatch.setattr("collectors.cot.get_json", lambda *a, **k: [])
        result = cot.collect()
        assert result.stale is True
        assert "no rows" in result.error

    def test_renamed_fields_degrade_rather_than_guess(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.cot.get_json",
            lambda *a, **k: [{"report_date_as_yyyy_mm_dd": "2026-08-11", "some_new_name": "1"}],
        )
        result = cot.collect()
        assert result.stale is True
        assert "field names" in result.error


# --------------------------------------------------------------------------- #
# Rate-cut odds, derived from Fed Funds futures
# --------------------------------------------------------------------------- #


class TestFedwatch:
    def test_derives_implied_rate_from_the_futures_price(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.fedwatch.quote", quote_stub(**{"ZQ=F": q("ZQ=F", 96.25)})
        )
        result = fedwatch.collect(current_rate=None)
        # 100 - 96.25 = 3.75%
        assert result.reading("implied_policy_rate").value == pytest.approx(3.75)
        assert result.reading("implied_cut_probability_pct") is None

    def test_converts_the_gap_to_a_share_of_a_25bp_move(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.fedwatch.quote", quote_stub(**{"ZQ=F": q("ZQ=F", 96.25)})
        )
        # Effective 3.875 vs implied 3.75 => 12.5bp priced => half of a 25bp move.
        result = fedwatch.collect(current_rate=3.875)
        odds = result.reading("implied_cut_probability_pct")
        assert odds.value == pytest.approx(50.0, abs=0.1)
        assert odds.extra["expected_cut_bp"] == pytest.approx(12.5, abs=0.1)

    def test_probability_is_clamped_to_0_100(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.fedwatch.quote", quote_stub(**{"ZQ=F": q("ZQ=F", 95.0)})
        )
        # Implied 5.0% vs effective 3.0% => a hike is priced, not a cut => 0.
        result = fedwatch.collect(current_rate=3.0)
        assert result.reading("implied_cut_probability_pct").value == 0.0

    def test_never_claims_to_be_a_cme_fedwatch_quote(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.fedwatch.quote", quote_stub(**{"ZQ=F": q("ZQ=F", 96.25)})
        )
        result = fedwatch.collect(current_rate=3.875)
        assert any("not a CME FedWatch quote" in note or "DERIVED" in note for note in result.notes)
        assert "derived" in result.reading("implied_policy_rate").source.lower()

    def test_implausible_futures_price_is_rejected(self, monkeypatch):
        # A wrong symbol would give a price nowhere near 100.
        monkeypatch.setattr(
            "collectors.fedwatch.quote", quote_stub(**{"ZQ=F": q("ZQ=F", 3400.0)})
        )
        result = fedwatch.collect(current_rate=3.5)
        assert result.stale is True
        assert "plausible" in result.error
