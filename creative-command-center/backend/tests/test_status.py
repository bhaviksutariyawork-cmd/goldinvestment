"""The status cascade — order matters more than any individual rule."""

from __future__ import annotations

from app.core.status import StatusInput, classify, is_ranked


def base(**overrides) -> StatusInput:
    defaults = {
        "purchases": 120,
        "impressions_in_window": 50_000,
        "lpv_transfer": 0.9,
        "frequency": 1.4,
        "roas_trailing": 2.2,
        "roas_lifetime": 2.2,
        "target_roas": 2.0,
        "cost_per_outbound_click": 12.0,
    }
    return StatusInput(**{**defaults, **overrides})


def test_paused_wins_over_everything():
    assert classify(base(effective_status="PAUSED")).status == "PAUSED"
    assert classify(base(manual_paused=True)).status == "PAUSED"


def test_non_conversion_objective_is_excluded_not_judged():
    result = classify(base(objective_is_conversion=False))
    assert result.status == "EXCLUDED"
    assert "ROAS is not the yardstick" in result.reason


def test_zero_delivery_is_excluded():
    assert classify(base(impressions_in_window=0)).status == "EXCLUDED"


def test_insufficient_sits_above_every_roas_verdict():
    """A thin sample cannot produce CUT, HOLD or WIN, whatever the ROAS says."""
    for roas in (0.1, 1.0, 9.0):
        assert classify(base(purchases=29, roas_lifetime=roas)).status == "INSUFFICIENT"


def test_insufficient_still_gets_an_upper_funnel_verdict():
    """It means judgeable on hook, not "come back later"."""
    strong = classify(base(purchases=4, cost_per_outbound_click=5.0,
                           account_cpoc_p50=8.0, account_cpoc_p75=14.0))
    viable = classify(base(purchases=4, cost_per_outbound_click=10.0,
                           account_cpoc_p50=8.0, account_cpoc_p75=14.0))
    weak = classify(base(purchases=4, cost_per_outbound_click=22.0,
                         account_cpoc_p50=8.0, account_cpoc_p75=14.0))
    assert strong.upper_funnel_verdict == "STRONG_HOOK"
    assert viable.upper_funnel_verdict == "VIABLE_HOOK"
    assert weak.upper_funnel_verdict == "WEAK_HOOK"


def test_leaking_beats_fatigue_starvation_and_the_roas_verdicts():
    result = classify(base(lpv_transfer=0.33, frequency=5.0, roas_lifetime=4.0,
                           roas_trailing=1.0))
    assert result.status == "LEAKING"


def test_fatigued_needs_both_frequency_and_confirmed_decay():
    assert classify(base(frequency=3.0, roas_trailing=1.0, roas_lifetime=2.0)).status == "FATIGUED"
    # High frequency alone is not fatigue — performance has to confirm it.
    assert classify(base(frequency=3.0, roas_trailing=2.2, roas_lifetime=2.2)).status == "WIN"


def test_starved_beats_win():
    """Being the best creative in the ad set is not a reason to leave it on 20%."""
    result = classify(base(is_best_roas_in_adset=True, delivery_share=0.20, roas_lifetime=3.5))
    assert result.status == "STARVED"
    assert classify(
        base(is_best_roas_in_adset=True, delivery_share=0.40, roas_lifetime=3.5)
    ).status == "WIN"


def test_cut_hold_win_boundaries():
    assert classify(base(roas_lifetime=1.39, target_roas=2.0)).status == "CUT"   # < 0.7x
    assert classify(base(roas_lifetime=1.41, target_roas=2.0)).status == "HOLD"  # 0.7x..1.0x
    assert classify(base(roas_lifetime=2.00, target_roas=2.0)).status == "WIN"   # >= target


def test_no_target_holds_and_says_why():
    result = classify(base(target_roas=None))
    assert result.status == "HOLD"
    assert "no ROAS target set" in result.reason


def test_only_ranked_statuses_receive_a_rank():
    assert is_ranked("WIN", 30)
    assert not is_ranked("WIN", 29)
    assert not is_ranked("INSUFFICIENT", 100)
    assert not is_ranked("EXCLUDED", 100)
