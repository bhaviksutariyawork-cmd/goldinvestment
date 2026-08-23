"""The flag engine. Each test pins one trigger to the number in section 5."""

from __future__ import annotations

from app.core.flags import (
    AccountContext,
    AdsetContext,
    CreativeContext,
    Placement,
    evaluate_account,
    evaluate_adset,
    evaluate_creative,
    rank_flags,
)


def creative(**overrides) -> CreativeContext:
    defaults = {
        "account_id": "acct",
        "client_name": "Aurelia",
        "creative_id": "cr1",
        "name": "112-4",
        "status": "WIN",
        "purchases": 120,
        "spend": 40_000,
        "spend_7d": 8_000,
        "roas": 2.4,
        "roas_7d": 2.4,
        "cpa": 400,
        "frequency": 1.4,
        "lpv_transfer": 0.9,
        "outbound_ctr": 0.009,
        "outbound_ctr_7d": 0.009,
        "outbound_ctr_first7": 0.009,
        "cpm_7d": 300,
        "cpm_first7": 300,
        "reach_7d": 100_000,
        "reach_prior_7d": 80_000,
        "impressions_7d": 140_000,
        "target_roas": 2.0,
        "target_cpa": 380,
        "category": "Rings",
        "aov_band": "low",
        "angle_id": "gifting",
        "objective": "OUTCOME_SALES",
        "account_median_outbound_ctr": 0.008,
        "placements": [
            Placement("as1", "Ad set 1", spend=40_000, roas=2.4, delivery_share=0.6),
            Placement("as2", "Ad set 2", spend=10_000, roas=2.1, delivery_share=0.4),
        ],
    }
    return CreativeContext(**{**defaults, **overrides})


def keys(flags) -> set[str]:
    return {f.key for f in flags}


# --- RED --------------------------------------------------------------------


def test_audience_saturation_needs_all_three_conditions():
    saturated = creative(frequency=2.6, reach_7d=101_000, reach_prior_7d=100_000,
                         roas=2.4, roas_7d=1.5)
    assert "audience_saturation" in keys(evaluate_creative(saturated))
    # Reach still growing — the pool is not exhausted yet.
    assert "audience_saturation" not in keys(
        evaluate_creative(creative(frequency=2.6, reach_7d=140_000, reach_prior_7d=100_000,
                                   roas=2.4, roas_7d=1.5))
    )


def test_severe_frequency_fires_regardless_of_roas():
    flags = evaluate_creative(creative(frequency=8.24, roas=0.90, roas_7d=0.90))
    assert "severe_frequency" in keys(flags)


def test_transfer_leak_needs_meaningful_spend():
    assert "transfer_leak" in keys(evaluate_creative(creative(lpv_transfer=0.33, spend=40_000)))
    assert "transfer_leak" not in keys(
        evaluate_creative(creative(lpv_transfer=0.33, spend=2_000))
    )


def test_high_cac_requires_an_adequate_sample():
    over = creative(cpa=600, target_cpa=380, purchases=120)
    thin = creative(cpa=600, target_cpa=380, purchases=12)
    assert "high_cac" in keys(evaluate_creative(over))
    assert "high_cac" not in keys(evaluate_creative(thin))


def test_starved_winner_prices_the_forgone_revenue():
    placement = Placement(
        "as1", "Rings | Broad", spend=2_400, roas=3.10, delivery_share=0.20,
        is_best_roas=True, adset_spend=12_000, rival_spend=9_600, rival_roas=1.05,
    )
    flags = evaluate_creative(creative(purchases=40, placements=[placement]))
    starved = next(f for f in flags if f.key == "starved_winner")
    # (3.10 - 1.05) x 9,600 spent on the worse creative.
    assert round(starved.money_at_stake) == round((3.10 - 1.05) * 9_600)
    assert starved.severity == "red"


def test_starved_winner_needs_twenty_purchases():
    placement = Placement("as1", "s", roas=3.0, delivery_share=0.2, is_best_roas=True,
                          adset_spend=1000, rival_spend=800, rival_roas=1.0)
    assert "starved_winner" not in keys(evaluate_creative(creative(purchases=19,
                                                                   placements=[placement])))
    assert "starved_winner" in keys(evaluate_creative(creative(purchases=20,
                                                               placements=[placement])))


# --- AMBER ------------------------------------------------------------------


def test_frequency_warning_band():
    assert "frequency_warning" in keys(evaluate_creative(creative(frequency=2.2)))
    assert "frequency_warning" not in keys(evaluate_creative(creative(frequency=1.9)))
    assert "frequency_warning" not in keys(evaluate_creative(creative(frequency=2.6)))


def test_ctr_decay_and_cpm_inflation():
    decayed = creative(outbound_ctr_first7=0.012, outbound_ctr_7d=0.008)
    inflated = creative(cpm_first7=250, cpm_7d=320)
    assert "ctr_decay" in keys(evaluate_creative(decayed))
    assert "cpm_inflation" in keys(evaluate_creative(inflated))


def test_hook_works_sell_fails():
    flags = evaluate_creative(
        creative(outbound_ctr=0.021, account_median_outbound_ctr=0.008, roas=0.72, spend=41_000)
    )
    assert "hook_works_sell_fails" in keys(flags)


def test_budget_underspend_and_learning_threshold():
    starved_budget = AdsetContext(
        account_id="acct", client_name="Aurelia", adset_id="as1", adset_name="Earrings",
        daily_budget=2_400, spend_7d=9_000, purchases_7d=60,
    )
    flags = evaluate_adset(starved_budget)
    assert "budget_underspend" in keys(flags)
    assert "under_learning_threshold" not in keys(flags)

    thin = AdsetContext(
        account_id="acct", client_name="Aurelia", adset_id="as2", adset_name="Bangles",
        daily_budget=1_000, spend_7d=6_900, purchases_7d=12,
    )
    assert "under_learning_threshold" in keys(evaluate_adset(thin))


# --- BLUE -------------------------------------------------------------------


def test_scale_candidate_needs_headroom():
    assert "scale_candidate" in keys(
        evaluate_creative(creative(roas=3.2, target_roas=2.0, purchases=40, frequency=1.4))
    )
    # Over target but the audience is already saturating.
    assert "scale_candidate" not in keys(
        evaluate_creative(creative(roas=3.2, target_roas=2.0, purchases=40, frequency=2.4))
    )


def test_proven_creative_in_one_adset():
    single = [Placement("as1", "Rings | Broad", spend=20_000, roas=2.6, delivery_share=1.0)]
    assert "proven_one_adset" in keys(evaluate_creative(creative(placements=single)))
    assert "proven_one_adset" not in keys(evaluate_creative(creative()))


def test_coverage_and_concentration_come_from_the_account_context():
    context = AccountContext(
        account_id="acct",
        client_name="Aurelia",
        hours_since_sync=2.0,
        last_sync_status="ok",
        active_campaigns_by_category={"Rings": ["Prospecting", "Festive"]},
        category_spend={"Rings": 90_000},
        concentration=[
            {"aov_band": "low", "hhi": 0.31, "angles": 4, "spend": 200_000,
             "top_angle": "price-anchor", "top_share": 0.52}
        ],
        coverage_gaps=[{"category": "Rings", "angle_id": "heritage", "impressions": 1_200}],
    )
    flags = evaluate_account(context)
    assert {"self_competition", "concentration_risk", "coverage_gap"} <= keys(flags)
    assert "sync_failure" not in keys(flags)


# --- GREY -------------------------------------------------------------------


def test_untagged_zero_delivery_and_mixed_objective():
    assert "untagged_creative" in keys(evaluate_creative(creative(category=None)))
    assert "zero_delivery" in keys(
        evaluate_creative(creative(effective_status="ACTIVE", impressions_7d=0))
    )
    assert "mixed_objective" in keys(
        evaluate_creative(creative(objective="OUTCOME_TRAFFIC", objective_is_conversion=False))
    )


def test_sync_failure_on_stale_or_errored():
    stale = AccountContext(account_id="a", client_name="c", hours_since_sync=9.0)
    errored = AccountContext(
        account_id="a", client_name="c", hours_since_sync=1.0,
        last_sync_status="error", last_sync_error="OAuth token expired",
    )
    assert "sync_failure" in keys(evaluate_account(stale))
    failure = next(f for f in evaluate_account(errored) if f.key == "sync_failure")
    assert "OAuth token expired" in failure.trigger


# --- ranking ----------------------------------------------------------------


def test_ranking_is_severity_then_money_at_stake():
    flags = evaluate_creative(
        creative(frequency=8.0, spend_7d=47_000, lpv_transfer=0.33, spend=90_000)
    )
    ranked = rank_flags(flags)
    assert [f.severity for f in ranked] == sorted(
        (f.severity for f in ranked), key=lambda s: ["red", "amber", "blue", "grey"].index(s)
    )
    reds = [f.money_at_stake for f in ranked if f.severity == "red"]
    assert reds == sorted(reds, reverse=True)


def test_dedupe_key_is_stable_for_snoozing():
    flag = next(f for f in evaluate_creative(creative(frequency=8.0)) if f.key == "severe_frequency")
    assert flag.dedupe_key == "severe_frequency:creative:cr1"
