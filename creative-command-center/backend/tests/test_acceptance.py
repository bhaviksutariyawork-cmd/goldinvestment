"""Section 9, one test per acceptance criterion, in the order written."""

from __future__ import annotations

from datetime import date, timedelta

from app.core.build import build_views
from app.core.hierarchy import build_level
from app.core.metrics import aggregate
from app.core.status import StatusInput, classify
from app.core.windows import resolve_range, settled_end
from app.service import untagged

from .conftest import AS_OF, series


def _views(rows, meta=None, targets=None, as_of=AS_OF, preset="90d", reach=None):
    window = resolve_range(as_of, preset)
    return build_views(
        rows,
        window=window,
        as_of=as_of,
        meta_by_creative=meta or {},
        targets=targets or [{"aov_band": "low", "target_roas": 1.8, "target_cpa": 380}],
        account={"_id": "acct", "client_name": "Aurelia Jewels"},
        reach_index=reach,
    )


# 1 ---------------------------------------------------------------------------


def test_roas_166_on_one_purchase_is_insufficient_never_win():
    """The number looks like a win. One purchase cannot carry that verdict."""
    verdict = classify(
        StatusInput(
            purchases=1,
            impressions_in_window=12_000,
            roas_lifetime=1.66,
            roas_trailing=1.66,
            target_roas=1.0,          # a target it clears twice over
            lpv_transfer=0.9,
            frequency=1.1,
            cost_per_outbound_click=9.0,
            account_cpoc_p75=14.0,
        )
    )
    assert verdict.status == "INSUFFICIENT"
    assert verdict.status != "WIN"
    # And it still gets a read — on the hook, not on ROAS.
    assert verdict.upper_funnel_verdict == "VIABLE_HOOK"
    assert "1 purchases" in verdict.reason


def test_roas_166_on_one_purchase_end_to_end():
    rows = series(20, creative_id="cr_thin", ad_id="ad1", adset_id="as1",
                  spend=500, purchases=0.05, roas=1.66)
    view = _views(rows)[0]
    assert round(view.window.purchases) == 1
    assert view.status.status == "INSUFFICIENT"
    assert view.rank is None  # no rank: candidate, not loser


# 2 ---------------------------------------------------------------------------


def test_best_roas_in_adset_at_twenty_percent_delivery_raises_starved():
    """Meta allocating to reach, not return."""
    winner = series(40, creative_id="cr_win", ad_id="ad_w", adset_id="as1",
                    spend=100, purchases=2, roas=3.10)
    hog = series(40, creative_id="cr_hog", ad_id="ad_h", adset_id="as1",
                 spend=400, purchases=3, roas=1.05)
    views = {v.creative_id: v for v in _views(winner + hog)}

    starved = views["cr_win"]
    assert starved.status.status == "STARVED"
    assert round(starved.placements[0].delivery_share, 2) == 0.20
    assert starved.placements[0].is_best_roas
    assert "20%" in starved.status.reason
    # The one funding the wrong creative is not itself starved.
    assert views["cr_hog"].status.status != "STARVED"


# 3 ---------------------------------------------------------------------------


def test_roas_330_with_33_percent_transfer_is_leaking_despite_high_roas():
    """The creative is fine. The destination is not, and pausing it would be
    the wrong action."""
    rows = series(40, creative_id="cr_leak", spend=600, purchases=4, roas=3.30,
                  clicks=300, lpv=99)
    view = _views(rows)[0]
    assert round(view.window.lpv_transfer, 2) == 0.33
    assert round(view.window.roas, 2) == 3.30
    assert view.status.status == "LEAKING"
    assert view.status.action == "fix the landing page"


# 4 ---------------------------------------------------------------------------


def test_rank_movement_does_not_move_on_attribution_lag_alone():
    """A stable creative must not appear to climb or fall just because Meta is
    still revising the last three days."""
    creatives = [
        ("cr_a", 3.0, 6),
        ("cr_b", 2.0, 5),
        ("cr_c", 1.2, 4),
    ]
    settled: list[dict] = []
    for creative_id, roas, purchases in creatives:
        settled += series(60, creative_id=creative_id, ad_id=f"ad_{creative_id}",
                          adset_id=f"as_{creative_id}", spend=500,
                          purchases=purchases, roas=roas)

    # The same rows, but the trailing three days arrive badly under-counted —
    # exactly what an incomplete attribution window looks like.
    edge = settled_end(AS_OF)
    lagged = []
    for r in settled:
        day = date.fromisoformat(r["date"])
        if day > edge:
            r = {**r, "purchase_roas": r["purchase_roas"] * 0.4,
                 "omni_purchase": r["omni_purchase"] * 0.4}
        lagged.append(r)

    def movements(rows):
        return {
            v.creative_id: (v.rank, v.prior_rank, v.rank_movement) for v in _views(rows)
        }

    assert movements(settled) == movements(lagged)
    assert all(move == 0 for _, _, move in movements(lagged).values())


def test_settled_window_excludes_the_trailing_three_days():
    window = resolve_range(AS_OF, "7d")
    assert window.end == AS_OF - timedelta(days=3)
    assert window.days == 7


# 5 ---------------------------------------------------------------------------


def test_one_creative_three_adsets_is_one_leaderboard_row_and_three_hierarchy_rows():
    rows = []
    for index, adset in enumerate(("as1", "as2", "as3")):
        rows += series(30, creative_id="cr_multi", ad_id=f"ad{index}", adset_id=adset,
                       campaign_id="cmp1", spend=300, purchases=3, roas=2.4)

    views = _views(rows)
    assert len(views) == 1                      # Leaderboard: aggregated by creative_id
    assert len(views[0].placements) == 3
    assert sorted(views[0].ad_ids) == ["ad0", "ad1", "ad2"]

    window = resolve_range(AS_OF, "90d")
    ad_rows = build_level(rows, "ad", window)   # Hierarchy: keyed on ad_id
    assert len(ad_rows) == 3
    assert {r["adset_id"] for r in ad_rows} == {"as1", "as2", "as3"}


# 6 ---------------------------------------------------------------------------


def test_untagged_share_of_spend_is_visible_until_under_ten_percent():
    tagged = series(30, creative_id="cr_tagged", ad_id="ad_t", adset_id="as1", spend=1_000)
    untagged_rows = series(30, creative_id="cr_untagged", ad_id="ad_u", adset_id="as1",
                           spend=200)
    meta = {"cr_tagged": {"category": "Rings", "aov_band": "low", "angle_id": "gifting"}}

    class _Bundle:
        views = _views(tagged + untagged_rows, meta=meta)

    figure = untagged(_Bundle())
    assert round(figure["untagged_share"], 3) == 0.167
    assert figure["visible"] is True

    # Tag the rest and the figure drops out of sight.
    meta["cr_untagged"] = {"category": "Rings", "aov_band": "low", "angle_id": "heritage"}
    _Bundle.views = _views(tagged + untagged_rows, meta=meta)
    assert untagged(_Bundle())["visible"] is False


# --- the data rules the acceptance list assumes -----------------------------


def test_revenue_never_reads_omni_purchase_values():
    rows = series(5, spend=1_000, roas=2.0)
    for r in rows:
        r["omni_purchase_values"] = 1_000_000.0
    assert aggregate(rows).revenue == 10_000.0


def test_delivery_share_is_measured_against_the_parent_adset():
    rows = (
        series(10, creative_id="c1", ad_id="a1", adset_id="s1", spend=200)
        + series(10, creative_id="c2", ad_id="a2", adset_id="s1", spend=800)
        + series(10, creative_id="c3", ad_id="a3", adset_id="s2", spend=1_000)
    )
    window = resolve_range(AS_OF, "90d")
    shares = {r["id"]: r["delivery_share"] for r in build_level(rows, "ad", window)}
    assert round(shares["a1"], 2) == 0.20
    assert round(shares["a2"], 2) == 0.80
    # An ad alone in its ad set takes all of it — not 1/3 of the account.
    assert round(shares["a3"], 2) == 1.00
