"""Data rules 1 and 6, and the ratios that hang off them."""

from __future__ import annotations

from datetime import date

from app.core.metrics import (
    aggregate,
    group_by,
    hhi,
    median,
    percentile,
    row_revenue,
)
from app.core.windows import Window

from .conftest import AS_OF, row, series


def test_revenue_is_roas_times_spend_never_omni_purchase_values():
    """Data rule 1. `omni_purchase_values` carries a 100x decimal error."""
    r = row(AS_OF, spend=2_400.0, roas=1.75)
    r["omni_purchase_values"] = 420_000.0  # what the API would wrongly report
    assert row_revenue(r) == 4_200.0


def test_ratios_are_computed_from_sums_not_averaged():
    """Averaging daily ROAS weights a 100-rupee day like a 100,000-rupee one."""
    rows = [
        row(date(2026, 8, 1), spend=100.0, roas=5.0),
        row(date(2026, 8, 2), spend=9_900.0, roas=1.0),
    ]
    metrics = aggregate(rows)
    assert metrics.revenue == 500.0 + 9_900.0
    assert round(metrics.roas, 4) == round(10_400.0 / 10_000.0, 4)
    # The naive mean of the two daily ROAS values would be 3.0.
    assert metrics.roas < 1.1


def test_outbound_clicks_is_the_click_denominator():
    """Data rule 6, applied to both derived click metrics."""
    metrics = aggregate([row(AS_OF, spend=500.0, clicks=250, lpv=150, impressions=50_000)])
    assert metrics.cost_per_outbound_click == 2.0
    assert metrics.outbound_ctr == 250 / 50_000
    assert metrics.lpv_transfer == 0.6


def test_group_by_creative_merges_the_same_asset_across_ad_sets():
    """Data rule 3."""
    rows = [
        row(AS_OF, creative_id="cr1", ad_id="a1", adset_id="s1", spend=100.0),
        row(AS_OF, creative_id="cr1", ad_id="a2", adset_id="s2", spend=200.0),
        row(AS_OF, creative_id="cr1", ad_id="a3", adset_id="s3", spend=300.0),
    ]
    by_creative = group_by(rows, "creative_id")
    by_ad = group_by(rows, "ad_id")
    assert len(by_creative) == 1
    assert by_creative["cr1"].spend == 600.0
    assert len(by_ad) == 3


def test_window_clips_rows():
    rows = series(10)
    window = Window(date(2026, 8, 18), date(2026, 8, 20))
    assert aggregate(rows, window).days_live == 3


def test_frequency_falls_back_to_a_lower_bound_without_deduped_reach():
    metrics = aggregate(series(5, impressions=10_000, reach=8_000))
    assert metrics.frequency_is_lower_bound
    assert metrics.reach_basis == "daily_sum"
    lower_bound = metrics.frequency

    metrics.attach_reach(12_000, "window")
    assert not metrics.frequency_is_lower_bound
    # The deduped figure is strictly higher — that is the whole point.
    assert metrics.frequency > lower_bound


def test_percentile_and_hhi():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert median([5]) == 5
    assert percentile([], 0.75) is None
    assert round(hhi([0.5, 0.5]), 4) == 0.5
    assert round(hhi([0.25] * 4), 4) == 0.25
