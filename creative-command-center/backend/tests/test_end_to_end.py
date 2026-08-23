"""The whole engine over the demo account.

Not a unit test — a check that the pieces agree with each other on a dataset
that contains one of everything.
"""

from __future__ import annotations

from app.core.build import build_views
from app.core.coverage import concentration, coverage_matrix
from app.core.windows import resolve_range, settled_end
from app.service import Bundle, build_flags, coverage_rows, untagged

from .conftest import AS_OF


def _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account, preset="30d"):
    window = resolve_range(AS_OF, preset)
    bundle = Bundle(
        account=account,
        account_id="acct",
        rows=sample_rows,
        entity_rows=[],
        meta=sample_meta,
        targets=sample_targets,
        targets_by_band={t["aov_band"]: t for t in sample_targets},
        as_of=AS_OF,
        window=window,
    )
    bundle.views = build_views(
        sample_rows,
        window=window,
        as_of=AS_OF,
        meta_by_creative=sample_meta,
        targets=sample_targets,
        account=account,
        reach_index=sample_reach,
    )
    return bundle


def test_the_demo_account_exercises_the_whole_cascade(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    statuses = {v.creative_id: v.status.status for v in bundle.views}

    assert statuses["cr_scale_01"] == "STARVED"     # best ROAS on 20% of the budget
    assert statuses["cr_leak_03"] == "LEAKING"      # 3.2 ROAS, 30% transfer
    assert statuses["cr_fatigue_04"] == "FATIGUED"  # frequency 4.0, ROAS halved
    assert statuses["cr_cut_06"] == "CUT"
    assert statuses["cr_win_12"] == "WIN"
    assert statuses["cr_hold_13"] == "HOLD"
    assert statuses["cr_thin_10"] == "INSUFFICIENT"


def test_leaderboard_ranks_only_creatives_past_the_gate(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    ranked = [v for v in bundle.views if v.rank is not None]
    unranked = [v for v in bundle.views if v.rank is None]

    assert all(v.window.purchases >= 30 for v in ranked)
    assert all(v.window.purchases < 30 or v.status.status == "INSUFFICIENT" for v in unranked)
    assert [v.rank for v in sorted(ranked, key=lambda v: v.rank)] == list(
        range(1, len(ranked) + 1)
    )
    best = min(ranked, key=lambda v: v.rank)
    assert best.window.roas == max(v.window.roas for v in ranked)


def test_the_multi_adset_creative_appears_once_with_three_placements(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    multi = [v for v in bundle.views if v.creative_id == "cr_multi_05"]
    assert len(multi) == 1
    assert len(multi[0].placements) == 3


def test_flags_cover_every_severity(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    flags = build_flags(bundle)
    by_key = {f.key for f in flags}

    assert {"severe_frequency", "transfer_leak", "starved_winner"} <= by_key
    assert "untagged_creative" in by_key
    assert {"red", "amber", "blue", "grey"} <= {f.severity for f in flags}
    # Ranked by money at stake inside each severity group.
    reds = [f.money_at_stake for f in flags if f.severity == "red"]
    assert reds == sorted(reds, reverse=True)


def test_snoozing_removes_a_flag_from_the_screen(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    flags = build_flags(bundle)
    target = flags[0].dedupe_key
    assert target not in {f.dedupe_key for f in build_flags(bundle, {target})}


def test_untagged_share_is_over_the_threshold_in_the_demo_account(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    """The dashboard should open with real tagging work visible."""
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    figure = untagged(bundle)
    assert figure["visible"] is True
    assert figure["untagged_share"] > 0.10


def test_coverage_grid_and_concentration_are_populated(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account, "90d")
    rows = coverage_rows(bundle)
    matrix = coverage_matrix(rows)

    assert len(matrix["categories"]) >= 4
    assert matrix["untested_cells"], "the demo account should have angles left to test"
    low_band = next(b for b in concentration(rows) if b["aov_band"] == "low")
    assert low_band["hhi"] > 0


def test_nothing_in_the_window_reaches_past_the_settled_edge(
    sample_rows, sample_meta, sample_targets, sample_reach, account
):
    bundle = _bundle(sample_rows, sample_meta, sample_targets, sample_reach, account)
    assert bundle.window.end == settled_end(AS_OF)
    assert bundle.settling["start"] > bundle.window.end.isoformat()
