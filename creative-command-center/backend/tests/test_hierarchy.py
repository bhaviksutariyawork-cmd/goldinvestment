"""Hierarchy Explorer levels, the delivery bar, and the Within Ad Set tab."""

from __future__ import annotations

from app.core.hierarchy import (
    adset_delivery_bar,
    budget_pacing,
    build_level,
    learning_threshold,
    within_adset_groups,
)
from app.core.windows import resolve_range

from .conftest import AS_OF, series

WINDOW = resolve_range(AS_OF, "90d")


def _account_rows():
    return (
        series(30, creative_id="c1", ad_id="a1", adset_id="s1", campaign_id="k1",
               spend=100, purchases=3, roas=3.1)
        + series(30, creative_id="c2", ad_id="a2", adset_id="s1", campaign_id="k1",
                 spend=400, purchases=4, roas=1.05)
        + series(30, creative_id="c3", ad_id="a3", adset_id="s2", campaign_id="k2",
                 spend=500, purchases=5, roas=2.0)
    )


def test_every_level_returns_the_same_columns():
    rows = _account_rows()
    for level in ("campaign", "adset", "ad"):
        table = build_level(rows, level, WINDOW)
        assert table
        for entry in table:
            assert {"name", "status", "delivery_share", "metrics", "flags"} <= set(entry)
            assert {"spend", "roas", "cpa", "purchases", "frequency", "cpm",
                    "outbound_ctr", "lpv_transfer"} <= set(entry["metrics"])


def test_drill_down_filters_to_the_parent():
    rows = _account_rows()
    assert len(build_level(rows, "adset", WINDOW)) == 2
    assert len(build_level(rows, "adset", WINDOW, campaign_id="k1")) == 1
    assert len(build_level(rows, "ad", WINDOW, adset_id="s1")) == 2
    # "All Ads" at any level flattens everything below.
    assert len(build_level(rows, "ad", WINDOW)) == 3


def test_delivery_share_at_campaign_level_is_share_of_account():
    table = {r["id"]: r["delivery_share"] for r in build_level(_account_rows(), "campaign", WINDOW)}
    assert round(table["k1"], 2) == 0.50
    assert round(table["k2"], 2) == 0.50


def test_delivery_bar_carries_roas_for_colouring():
    bar = adset_delivery_bar(_account_rows(), "s1", WINDOW)
    assert [round(s["delivery_share"], 2) for s in bar] == [0.80, 0.20]
    assert bar[0]["roas"] is not None


def test_learning_threshold_counts_the_trailing_settled_week():
    rows = series(30, adset_id="s1", purchases=5)
    result = learning_threshold(rows, "s1", AS_OF)
    assert result["events_7d"] == 35
    assert result["under_threshold"] is True
    assert result["window"]["end"] < AS_OF.isoformat()


def test_budget_pacing_uses_the_budget_set_on_each_day():
    rows = series(30, adset_id="s1", spend=750)
    entity_rows = [
        {"account_id": "acct", "entity_type": "adset", "entity_id": "s1",
         "date": r["date"], "daily_budget": 1_000.0}
        for r in rows
    ]
    pacing = budget_pacing(rows, entity_rows, "s1", AS_OF)
    assert len(pacing["days"]) == 7
    assert pacing["pacing"] == 0.75
    assert all(day["pacing"] == 0.75 for day in pacing["days"])


def test_within_adset_only_compares_ads_that_shared_a_budget():
    groups = {g["adset_id"]: g for g in within_adset_groups(_account_rows(), WINDOW, {})}
    assert set(groups) == {"s1", "s2"}

    contested = groups["s1"]
    assert [c["creative_id"] for c in contested["creatives"]] == ["c1", "c2"]
    assert contested["creatives"][0]["rank_in_adset"] == 1
    # The better creative holds 20%, the worse one 80% — that is the misallocation.
    assert contested["misallocated"] is True
    assert contested["misallocation_gap_pct"] == 300.0

    assert groups["s2"]["misallocated"] is False


def test_misallocated_adsets_sort_to_the_top():
    groups = within_adset_groups(_account_rows(), WINDOW, {})
    assert groups[0]["misallocated"] is True
