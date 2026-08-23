"""Coverage matrix, concentration, and the testing priority queue."""

from __future__ import annotations

from app.core.coverage import (
    CoverageRow,
    concentration,
    coverage_gaps,
    coverage_matrix,
    untagged_spend_share,
)
from app.core.coverage import (
    testing_priority as rank_categories_to_test,
)


def cov(category, angle, band="low", impressions=10_000, spend=10_000, revenue=20_000, **kw):
    return CoverageRow(
        creative_id=f"{category}-{angle}",
        category=category,
        angle_id=angle,
        aov_band=band,
        impressions=impressions,
        spend=spend,
        revenue=revenue,
        **kw,
    )


def test_matrix_materialises_every_intersection():
    """An absent cell is the most interesting gap and must still be drawn."""
    rows = [cov("Rings", "gifting"), cov("Earrings", "heritage")]
    matrix = coverage_matrix(rows)
    assert len(matrix["cells"]) == 4  # 2 categories x 2 angles
    empty = next(c for c in matrix["cells"] if c["category"] == "Rings"
                 and c["angle_id"] == "heritage")
    assert empty["impressions"] == 0
    assert empty["state"] == "untested"


def test_under_the_floor_is_untested_not_failed():
    rows = [cov("Rings", "gifting", impressions=4_999), cov("Rings", "heritage", impressions=5_000)]
    cells = {c["angle_id"]: c for c in coverage_matrix(rows)["cells"]}
    assert cells["gifting"]["state"] == "partial"
    assert cells["gifting"]["tested"] is False
    assert cells["heritage"]["tested"] is True


def test_untagged_rows_never_enter_the_grid():
    rows = [cov("Rings", "gifting"), CoverageRow("x", None, None, None, impressions=99_999)]
    assert coverage_matrix(rows)["categories"] == ["Rings"]


def test_gaps_are_ordered_by_category_spend():
    rows = [
        cov("Rings", "gifting", spend=100_000),
        cov("Rings", "heritage", spend=100_000, impressions=800),
        cov("Earrings", "gifting", spend=1_000),
        cov("Earrings", "heritage", spend=1_000, impressions=400),
    ]
    gaps = coverage_gaps(rows)
    assert gaps[0]["category"] == "Rings"


def test_concentration_flags_a_bunched_band():
    bunched = [
        cov("Rings", "price-anchor", spend=90_000),
        cov("Rings", "gifting", spend=10_000),
    ]
    spread = [cov("Rings", f"angle-{i}", spend=25_000) for i in range(4)]
    assert concentration(bunched)[0]["concentrated"] is True
    assert concentration(bunched)[0]["top_angle"] == "price-anchor"
    assert concentration(spread)[0]["concentrated"] is False
    assert round(concentration(spread)[0]["hhi"], 3) == 0.25


def test_priority_queue_favours_big_spend_untested_angles_and_a_falling_trend():
    rows = [
        # Big spender, most angles untested, ROAS sliding.
        cov("Rings", "gifting", spend=100_000, impressions=50_000,
            spend_recent=10_000, revenue_recent=10_000,
            spend_prior=10_000, revenue_prior=30_000),
        # Small spender, fully covered, ROAS improving.
        cov("Charms", "gifting", spend=1_000, impressions=50_000,
            spend_recent=1_000, revenue_recent=4_000,
            spend_prior=1_000, revenue_prior=1_000),
        cov("Charms", "heritage", spend=1_000, impressions=50_000),
        cov("Charms", "price-anchor", spend=1_000, impressions=50_000),
    ]
    queue = rank_categories_to_test(rows, ["gifting", "heritage", "price-anchor"])
    assert queue[0]["category"] == "Rings"
    assert queue[0]["angles_untested"] == 2
    assert queue[0]["trend_factor"] > 1.0   # falling ROAS raises urgency
    assert queue[-1]["trend_factor"] < 1.0  # rising ROAS lowers it


def test_untagged_share_visibility_threshold():
    assert untagged_spend_share(90.0, 10.0)["visible"] is True   # exactly 10%
    assert untagged_spend_share(91.0, 9.0)["visible"] is False
    assert untagged_spend_share(0.0, 0.0)["untagged_share"] == 0.0
