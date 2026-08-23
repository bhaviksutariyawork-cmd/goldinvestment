"""The status cascade — section 6. First match wins, applied at creative_id level.

The ordering is the whole design. LEAKING sits above CUT so a creative with a
broken landing page is diagnosed as a landing-page problem rather than killed
as a bad creative. INSUFFICIENT sits above all of them so nothing is judged on
ROAS before the sample supports it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    CUT_ROAS_RATIO,
    FATIGUE_FREQUENCY,
    FATIGUE_ROAS_RATIO,
    HOLD_ROAS_RATIO,
    LEAK_TRANSFER_RATIO,
    MIN_PURCHASES_FOR_VERDICT,
    STARVED_DELIVERY_SHARE,
)

STATUSES = (
    "PAUSED",
    "EXCLUDED",
    "INSUFFICIENT",
    "LEAKING",
    "FATIGUED",
    "STARVED",
    "CUT",
    "HOLD",
    "WIN",
)

# What the operator should do with each status, in the fewest words that fit a cell.
STATUS_ACTION = {
    "PAUSED": "off",
    "EXCLUDED": "not judged",
    "INSUFFICIENT": "judge on hook",
    "LEAKING": "fix the landing page",
    "FATIGUED": "rotate",
    "STARVED": "split or raise budget",
    "CUT": "propose pause",
    "HOLD": "leave alone",
    "WIN": "scale",
}


@dataclass
class StatusInput:
    """Everything the cascade reads, and nothing else.

    Kept as flat scalars deliberately: the cascade is the most consequential
    function in the app, and it should be readable and testable without
    constructing a database.
    """

    purchases: float
    manual_paused: bool = False
    effective_status: str = "ACTIVE"
    objective_is_conversion: bool = True
    impressions_in_window: float = 0.0
    lpv_transfer: float | None = None
    frequency: float | None = None
    roas_trailing: float | None = None
    roas_lifetime: float | None = None
    is_best_roas_in_adset: bool = False
    delivery_share: float | None = None
    target_roas: float | None = None
    # Upper-funnel context, used only to give INSUFFICIENT a real verdict.
    cost_per_outbound_click: float | None = None
    account_cpoc_p50: float | None = None
    account_cpoc_p75: float | None = None


@dataclass
class StatusResult:
    status: str
    reason: str
    action: str
    upper_funnel_verdict: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "action": self.action,
            "upper_funnel_verdict": self.upper_funnel_verdict,
        }


def upper_funnel_verdict(s: StatusInput) -> str:
    """What an under-sampled creative can still be judged on.

    INSUFFICIENT is not "too early to judge". It means judgeable on hook and
    click, not on ROAS. Killing a category on a thin ROAS reading is the
    operator's stated primary risk; this is the read that prevents it.

    Priced in cost per outbound click, never CTR — see `Metrics.cost_per_outbound_click`.
    """
    cpoc = s.cost_per_outbound_click
    if cpoc is None:
        return "NO_CLICK_DATA"
    if s.account_cpoc_p75 is not None and cpoc > s.account_cpoc_p75:
        return "WEAK_HOOK"
    if s.account_cpoc_p50 is not None and cpoc <= s.account_cpoc_p50:
        return "STRONG_HOOK"
    return "VIABLE_HOOK"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def classify(s: StatusInput) -> StatusResult:
    """Run the cascade. First match wins."""

    if s.manual_paused or str(s.effective_status).upper() in {
        "PAUSED",
        "ADSET_PAUSED",
        "CAMPAIGN_PAUSED",
        "ARCHIVED",
        "DELETED",
    }:
        return _result("PAUSED", "not delivering — paused")

    if not s.objective_is_conversion:
        return _result("EXCLUDED", "non-conversion objective — ROAS is not the yardstick")
    if s.impressions_in_window <= 0:
        return _result("EXCLUDED", "no delivery in the window")

    if s.purchases < MIN_PURCHASES_FOR_VERDICT:
        verdict = upper_funnel_verdict(s)
        return _result(
            "INSUFFICIENT",
            f"{int(s.purchases)} purchases — under the {MIN_PURCHASES_FOR_VERDICT} needed for a "
            f"ROAS verdict; judged on hook instead ({_num(s.cost_per_outbound_click)} per outbound "
            f"click vs account p75 {_num(s.account_cpoc_p75)})",
            upper_funnel=verdict,
        )

    if s.lpv_transfer is not None and s.lpv_transfer < LEAK_TRANSFER_RATIO:
        return _result(
            "LEAKING",
            f"only {_pct(s.lpv_transfer)} of outbound clicks became landing page views "
            f"(floor {_pct(LEAK_TRANSFER_RATIO)}) — the leak is downstream of the creative",
        )

    if (
        s.frequency is not None
        and s.frequency >= FATIGUE_FREQUENCY
        and s.roas_trailing is not None
        and s.roas_lifetime is not None
        and s.roas_trailing < FATIGUE_ROAS_RATIO * s.roas_lifetime
    ):
        return _result(
            "FATIGUED",
            f"frequency {_num(s.frequency)} and 7d ROAS {_num(s.roas_trailing)} has fallen below "
            f"{FATIGUE_ROAS_RATIO:g}x its lifetime {_num(s.roas_lifetime)}",
        )

    if (
        s.is_best_roas_in_adset
        and s.delivery_share is not None
        and s.delivery_share < STARVED_DELIVERY_SHARE
    ):
        return _result(
            "STARVED",
            f"best ROAS in its ad set on only {_pct(s.delivery_share)} of the budget "
            f"(floor {_pct(STARVED_DELIVERY_SHARE)})",
        )

    if s.target_roas is None:
        return _result("HOLD", "no ROAS target set for this AOV band — set one to get a verdict")

    if s.roas_lifetime is not None and s.roas_lifetime < CUT_ROAS_RATIO * s.target_roas:
        return _result(
            "CUT",
            f"ROAS {_num(s.roas_lifetime)} against a {_num(s.target_roas)} target on "
            f"{int(s.purchases)} purchases — under {CUT_ROAS_RATIO:g}x target at an adequate sample",
        )

    if s.roas_lifetime is not None and s.roas_lifetime < HOLD_ROAS_RATIO * s.target_roas:
        return _result(
            "HOLD",
            f"ROAS {_num(s.roas_lifetime)} against a {_num(s.target_roas)} target — under target "
            f"but not by enough to kill",
        )

    return _result(
        "WIN",
        f"ROAS {_num(s.roas_lifetime)} at or above the {_num(s.target_roas)} target on "
        f"{int(s.purchases)} purchases",
    )


def _result(status: str, reason: str, upper_funnel: str | None = None) -> StatusResult:
    return StatusResult(
        status=status,
        reason=reason,
        action=STATUS_ACTION[status],
        upper_funnel_verdict=upper_funnel,
    )


def is_ranked(status: str, purchases: float) -> bool:
    """Section 4: creatives below 30 purchases do not receive a rank.

    They are candidates, not losers, and land in the Testing table instead.
    """
    return purchases >= MIN_PURCHASES_FOR_VERDICT and status not in {"EXCLUDED", "INSUFFICIENT"}
