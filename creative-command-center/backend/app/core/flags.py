"""The Flag Center engine — section 5.

Every flag is a dataclass carrying the number that fired it, the threshold it
crossed, and the money at stake. The UI renders cards; it does not recompute
anything. Ranking within a severity group is by money at stake, so the
expensive problem is at the top whether or not it is the loudest.

Each flag exposes a `dedupe_key` that is stable across syncs. Snoozes and
`actions_log` entries hang off that key — without it the screen is noise
inside a week.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import (
    COVERAGE_CELL_IMPRESSIONS,
    CPM_INFLATION_RATIO,
    CTR_DECAY_RATIO,
    FREQUENCY_WARN_HIGH,
    FREQUENCY_WARN_LOW,
    HHI_CONCENTRATION,
    HIGH_CAC_RATIO,
    HOOK_WORKS_MAX_ROAS,
    HOOK_WORKS_MIN_SPEND,
    LEAK_MIN_SPEND,
    LEAK_TRANSFER_RATIO,
    LEARNING_THRESHOLD_EVENTS,
    MIN_PURCHASES_FOR_VERDICT,
    SATURATION_REACH_GROWTH,
    SCALE_MAX_FREQUENCY,
    SCALE_ROAS_RATIO,
    SEVERE_FREQUENCY,
    STARVED_DELIVERY_SHARE,
    STARVED_MIN_PURCHASES,
    SYNC_STALE_HOURS,
    UNDERSPEND_RATIO,
)

# --- the catalogue ----------------------------------------------------------
# `why` is shown on the card. The operator should never have to remember why a
# threshold is where it is.

FLAG_DEFS: dict[str, dict] = {
    # RED — act today
    "audience_saturation": {
        "label": "Audience Saturation",
        "severity": "red",
        "why": "The pool is exhausted and performance confirms it — reach has stopped growing "
               "while the same people see the ad again and again.",
    },
    "severe_frequency": {
        "label": "Severe Frequency",
        "severity": "red",
        "why": "Structural, not a soft signal. At this frequency the ad set is re-serving a "
               "closed audience regardless of what ROAS says.",
    },
    "transfer_leak": {
        "label": "Transfer Leak",
        "severity": "red",
        "why": "You are paying for clicks that never land. Healthy transfer runs ~92% on IG Feed, "
               "81–84% on Reels, ~83% on Stories.",
    },
    "high_cac": {
        "label": "High CAC",
        "severity": "red",
        "why": "Confirmed unprofitable at an adequate sample — this is not a small-numbers artefact.",
    },
    "starved_winner": {
        "label": "Starved Winner",
        "severity": "red",
        "why": "Meta is allocating to reach, not return. The best creative in the ad set is being "
               "outspent by a worse one.",
    },
    # AMBER — watch / rotate
    "frequency_warning": {
        "label": "Frequency Warning",
        "severity": "amber",
        "why": "Approaching saturation. Line up the replacement now, not after ROAS turns.",
    },
    "ctr_decay": {
        "label": "CTR Decay",
        "severity": "amber",
        "why": "The hook is wearing out against this audience. Diagnostic only — never a kill "
               "trigger on its own.",
    },
    "cpm_inflation": {
        "label": "CPM Inflation",
        "severity": "amber",
        "why": "Auction cost is rising for the same creative — either competition or a narrowing "
               "delivery pocket.",
    },
    "budget_underspend": {
        "label": "Budget Underspend",
        "severity": "amber",
        "why": "The ad set cannot spend what you gave it. Usually a targeting or bid constraint.",
    },
    "under_learning_threshold": {
        "label": "Under Learning Threshold",
        "severity": "amber",
        "why": "Below 50 optimisation events a week, Meta's delivery is guesswork and so is your read.",
    },
    "hook_works_sell_fails": {
        "label": "Hook Works / Sell Fails",
        "severity": "amber",
        "why": "People click and do not buy. The creative is doing its job; the offer or the page "
               "is not.",
    },
    "self_competition": {
        "label": "Self-Competition",
        "severity": "amber",
        "why": "The same category is live in two campaigns at once, bidding against itself in the "
               "same auction.",
    },
    # BLUE — opportunity
    "scale_candidate": {
        "label": "Scale Candidate",
        "severity": "blue",
        "why": "Well over target at an adequate sample with headroom left in the audience.",
    },
    "coverage_gap": {
        "label": "Coverage Gap",
        "severity": "blue",
        "why": "This category x angle cell has never really been tested. Untested is not the same "
               "as tried and failed.",
    },
    "concentration_risk": {
        "label": "Concentration Risk",
        "severity": "blue",
        "why": "Spend inside this AOV band is bunched into too few angles. One fatigue event takes "
               "the whole band down.",
    },
    "proven_one_adset": {
        "label": "Proven Creative, One Ad Set",
        "severity": "blue",
        "why": "A creative already over target running in a single ad set. Duplicating it is the "
               "cheapest scale available.",
    },
    # GREY — data quality
    "untagged_creative": {
        "label": "Untagged Creative",
        "severity": "grey",
        "why": "Every Coverage answer depends on this table. Ad names cannot substitute — most "
               "spend sits on numeric-only names.",
    },
    "zero_delivery": {
        "label": "Zero Delivery",
        "severity": "grey",
        "why": "Switched on and spending nothing. Usually a rejected asset or an unfillable audience.",
    },
    "mixed_objective": {
        "label": "Mixed Objective",
        "severity": "grey",
        "why": "A non-conversion objective in a conversion account drags every account-level "
               "average it touches.",
    },
    "sync_failure": {
        "label": "Sync Failure",
        "severity": "grey",
        "why": "Every verdict on this client is being made on stale numbers.",
    },
}


@dataclass
class Flag:
    key: str
    entity_type: str  # creative | adset | campaign | account | cell
    entity_id: str
    entity_name: str
    account_id: str
    client_name: str = ""
    value: float | None = None
    threshold: float | None = None
    trigger: str = ""
    detail: str = ""
    money_at_stake: float = 0.0
    money_label: str = "spend at risk"
    proposal: dict | None = None
    context: dict = field(default_factory=dict)

    @property
    def severity(self) -> str:
        return FLAG_DEFS[self.key]["severity"]

    @property
    def label(self) -> str:
        return FLAG_DEFS[self.key]["label"]

    @property
    def why(self) -> str:
        return FLAG_DEFS[self.key]["why"]

    @property
    def dedupe_key(self) -> str:
        return f"{self.key}:{self.entity_type}:{self.entity_id}"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "severity": self.severity,
            "why": self.why,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "account_id": self.account_id,
            "client_name": self.client_name,
            "value": self.value,
            "threshold": self.threshold,
            "trigger": self.trigger,
            "detail": self.detail,
            "money_at_stake": round(self.money_at_stake, 2),
            "money_label": self.money_label,
            "proposal": self.proposal,
            "dedupe_key": self.dedupe_key,
            "context": self.context,
        }


# --- evaluation contexts ----------------------------------------------------


@dataclass
class Placement:
    """One (creative, ad set) pairing — where the creative actually competed."""

    adset_id: str
    adset_name: str
    campaign_id: str = ""
    campaign_name: str = ""
    spend: float = 0.0
    roas: float | None = None
    delivery_share: float | None = None
    is_best_roas: bool = False
    adset_spend: float = 0.0
    rival_spend: float = 0.0
    rival_roas: float | None = None
    ad_ids: list[str] = field(default_factory=list)


@dataclass
class CreativeContext:
    account_id: str
    client_name: str
    creative_id: str
    name: str
    status: str
    purchases: float = 0.0
    spend: float = 0.0
    spend_7d: float = 0.0
    roas: float | None = None
    roas_7d: float | None = None
    cpa: float | None = None
    frequency: float | None = None
    lpv_transfer: float | None = None
    outbound_ctr: float | None = None
    outbound_ctr_7d: float | None = None
    outbound_ctr_first7: float | None = None
    cpm_7d: float | None = None
    cpm_first7: float | None = None
    reach_7d: float = 0.0
    reach_prior_7d: float = 0.0
    impressions_7d: float = 0.0
    target_roas: float | None = None
    target_cpa: float | None = None
    category: str | None = None
    aov_band: str | None = None
    angle_id: str | None = None
    objective: str = ""
    objective_is_conversion: bool = True
    effective_status: str = "ACTIVE"
    account_median_outbound_ctr: float | None = None
    placements: list[Placement] = field(default_factory=list)
    thumbnail_url: str | None = None


@dataclass
class AdsetContext:
    account_id: str
    client_name: str
    adset_id: str
    adset_name: str
    campaign_id: str = ""
    campaign_name: str = ""
    daily_budget: float | None = None
    lifetime_budget: float | None = None
    spend_7d: float = 0.0
    purchases_7d: float = 0.0
    optimisation_events_7d: float | None = None
    delivery_status: str = "ACTIVE"


@dataclass
class AccountContext:
    account_id: str
    client_name: str
    hours_since_sync: float | None = None
    last_sync_status: str = "ok"
    last_sync_error: str | None = None
    untagged_spend_share: float = 0.0
    # category -> list of distinct active campaign names
    active_campaigns_by_category: dict[str, list[str]] = field(default_factory=dict)
    category_spend: dict[str, float] = field(default_factory=dict)
    # (aov_band, angle_id) HHI results, from core.coverage
    concentration: list[dict] = field(default_factory=list)
    coverage_gaps: list[dict] = field(default_factory=list)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def _n(v: float | None, p: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{p}f}"


def _money(v: float | None) -> str:
    """Amounts inside card copy. The currency symbol is the UI's job; the
    thousands separators are not, because this string is also read in logs."""
    return "n/a" if v is None else f"{v:,.0f}"


# --- creative-level flags ---------------------------------------------------


def evaluate_creative(c: CreativeContext) -> list[Flag]:
    flags: list[Flag] = []
    base = {
        "entity_type": "creative",
        "entity_id": c.creative_id,
        "entity_name": c.name,
        "account_id": c.account_id,
        "client_name": c.client_name,
    }
    pause_proposal = {
        "action": "pause",
        "entity_type": "ad",
        "ad_ids": [aid for p in c.placements for aid in p.ad_ids],
        "note": "Pause only — never delete. Deleting destroys social proof and history.",
    }

    # RED ---------------------------------------------------------------
    reach_growth = (
        (c.reach_7d - c.reach_prior_7d) / c.reach_prior_7d if c.reach_prior_7d > 0 else None
    )
    if (
        c.frequency is not None
        and c.frequency >= 2.5
        and reach_growth is not None
        and reach_growth < SATURATION_REACH_GROWTH
        and c.roas_7d is not None
        and c.roas is not None
        and c.roas_7d < 0.8 * c.roas
    ):
        flags.append(
            Flag(
                key="audience_saturation",
                value=c.frequency,
                threshold=2.5,
                trigger=f"frequency {_n(c.frequency)} vs 2.50 · reach growth {_pct(reach_growth)} "
                        f"vs 5% · 7d ROAS {_n(c.roas_7d)} vs {_n(0.8 * c.roas)} floor",
                detail="All three conditions are met at once: capped reach, repeated exposure, "
                       "and confirmed decay. Rotate the audience or the creative.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                proposal=pause_proposal,
                **base,
            )
        )

    if c.frequency is not None and c.frequency >= SEVERE_FREQUENCY:
        flags.append(
            Flag(
                key="severe_frequency",
                value=c.frequency,
                threshold=SEVERE_FREQUENCY,
                trigger=f"frequency {_n(c.frequency)} vs {SEVERE_FREQUENCY:.2f}",
                detail="No ROAS reading rescues this. The audience has seen it too many times.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                proposal=pause_proposal,
                **base,
            )
        )

    if (
        c.lpv_transfer is not None
        and c.lpv_transfer < LEAK_TRANSFER_RATIO
        and c.spend >= LEAK_MIN_SPEND
    ):
        flags.append(
            Flag(
                key="transfer_leak",
                value=c.lpv_transfer,
                threshold=LEAK_TRANSFER_RATIO,
                trigger=f"LPV transfer {_pct(c.lpv_transfer)} vs {_pct(LEAK_TRANSFER_RATIO)} floor",
                detail="Fix the destination before touching the creative — the click is already "
                       "paid for by the time it is lost.",
                money_at_stake=c.spend * (1 - (c.lpv_transfer or 0)),
                money_label="spend on clicks that never landed",
                proposal={"action": "review_landing_page", "entity_type": "creative"},
                **base,
            )
        )

    if (
        c.cpa is not None
        and c.target_cpa
        and c.cpa > HIGH_CAC_RATIO * c.target_cpa
        and c.purchases >= MIN_PURCHASES_FOR_VERDICT
    ):
        flags.append(
            Flag(
                key="high_cac",
                value=c.cpa,
                threshold=HIGH_CAC_RATIO * c.target_cpa,
                trigger=f"CPA {_n(c.cpa)} vs {_n(HIGH_CAC_RATIO * c.target_cpa)} "
                        f"({HIGH_CAC_RATIO:g}x the {_n(c.target_cpa)} target) on "
                        f"{int(c.purchases)} purchases",
                detail="Sample is adequate, so this is the creative, not variance.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                proposal=pause_proposal,
                **base,
            )
        )

    for p in c.placements:
        if (
            p.is_best_roas
            and p.delivery_share is not None
            and p.delivery_share < STARVED_DELIVERY_SHARE
            and c.purchases >= STARVED_MIN_PURCHASES
        ):
            gap = None
            if p.roas is not None and p.rival_roas is not None:
                gap = (p.roas - p.rival_roas) * p.rival_spend
            flags.append(
                Flag(
                    key="starved_winner",
                    entity_type="creative",
                    entity_id=c.creative_id,
                    entity_name=c.name,
                    account_id=c.account_id,
                    client_name=c.client_name,
                    value=p.delivery_share,
                    threshold=STARVED_DELIVERY_SHARE,
                    trigger=f"best ROAS in {p.adset_name} at {_n(p.roas)} but only "
                            f"{_pct(p.delivery_share)} of its spend "
                            f"(floor {_pct(STARVED_DELIVERY_SHARE)})",
                    detail=f"The rest of the ad set spent {_money(p.rival_spend)} at "
                           f"{_n(p.rival_roas)} ROAS. This is intra-ad-set misallocation — the "
                           "one place it is visible.",
                    money_at_stake=gap or p.rival_spend,
                    money_label="revenue forgone against the rest of the ad set",
                    proposal={
                        "action": "split_adset",
                        "entity_type": "adset",
                        "adset_id": p.adset_id,
                        "note": "Isolate the winner into its own ad set, or pause the rival ads.",
                    },
                    context={"adset_id": p.adset_id, "adset_name": p.adset_name},
                )
            )

    # AMBER -------------------------------------------------------------
    if c.frequency is not None and FREQUENCY_WARN_LOW <= c.frequency < FREQUENCY_WARN_HIGH:
        flags.append(
            Flag(
                key="frequency_warning",
                value=c.frequency,
                threshold=FREQUENCY_WARN_HIGH,
                trigger=f"frequency {_n(c.frequency)} in the {FREQUENCY_WARN_LOW:.1f}–"
                        f"{FREQUENCY_WARN_HIGH:.1f} band",
                detail="Still working, but the runway is short. Brief the replacement.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                **base,
            )
        )

    if (
        c.outbound_ctr_7d is not None
        and c.outbound_ctr_first7
        and c.outbound_ctr_7d < CTR_DECAY_RATIO * c.outbound_ctr_first7
    ):
        flags.append(
            Flag(
                key="ctr_decay",
                value=c.outbound_ctr_7d,
                threshold=CTR_DECAY_RATIO * c.outbound_ctr_first7,
                trigger=f"7d outbound CTR {_pct(c.outbound_ctr_7d)} vs "
                        f"{_pct(c.outbound_ctr_first7)} in its first 7 days",
                detail="Diagnostic. Pair it with cost per outbound click before acting — CTR "
                       "alone has never justified a pause.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                **base,
            )
        )

    if c.cpm_7d is not None and c.cpm_first7 and c.cpm_7d > CPM_INFLATION_RATIO * c.cpm_first7:
        flags.append(
            Flag(
                key="cpm_inflation",
                value=c.cpm_7d,
                threshold=CPM_INFLATION_RATIO * c.cpm_first7,
                trigger=f"7d CPM {_n(c.cpm_7d)} vs {_n(c.cpm_first7)} at launch "
                        f"(+{_pct((c.cpm_7d / c.cpm_first7) - 1)})",
                detail="Same creative, dearer auction.",
                money_at_stake=c.spend_7d,
                money_label="spend in the last settled 7 days",
                **base,
            )
        )

    if (
        c.outbound_ctr is not None
        and c.account_median_outbound_ctr is not None
        and c.outbound_ctr > c.account_median_outbound_ctr
        and c.roas is not None
        and c.roas < HOOK_WORKS_MAX_ROAS
        and c.spend >= HOOK_WORKS_MIN_SPEND
    ):
        flags.append(
            Flag(
                key="hook_works_sell_fails",
                value=c.roas,
                threshold=HOOK_WORKS_MAX_ROAS,
                trigger=f"outbound CTR {_pct(c.outbound_ctr)} beats the account median "
                        f"{_pct(c.account_median_outbound_ctr)} but ROAS is {_n(c.roas)}",
                detail="Keep the hook, change what it sells or where it lands.",
                money_at_stake=c.spend,
                money_label="spend on a hook that converts nothing",
                **base,
            )
        )

    # BLUE --------------------------------------------------------------
    if (
        c.target_roas
        and c.roas is not None
        and c.roas >= SCALE_ROAS_RATIO * c.target_roas
        and c.purchases >= MIN_PURCHASES_FOR_VERDICT
        and c.frequency is not None
        and c.frequency < SCALE_MAX_FREQUENCY
    ):
        flags.append(
            Flag(
                key="scale_candidate",
                value=c.roas,
                threshold=SCALE_ROAS_RATIO * c.target_roas,
                trigger=f"ROAS {_n(c.roas)} vs {_n(SCALE_ROAS_RATIO * c.target_roas)} "
                        f"({SCALE_ROAS_RATIO:g}x target) on {int(c.purchases)} purchases, "
                        f"frequency {_n(c.frequency)}",
                detail="Over target with audience left to reach.",
                money_at_stake=c.spend_7d * max((c.roas - c.target_roas), 0),
                money_label="revenue above target at current spend",
                proposal={
                    "action": "raise_budget",
                    "entity_type": "adset",
                    "adset_ids": [p.adset_id for p in c.placements],
                },
                **base,
            )
        )

    if (
        c.target_roas
        and c.roas is not None
        and c.roas >= c.target_roas
        and c.purchases >= MIN_PURCHASES_FOR_VERDICT
        and len(c.placements) == 1
    ):
        flags.append(
            Flag(
                key="proven_one_adset",
                value=c.roas,
                threshold=c.target_roas,
                trigger=f"ROAS {_n(c.roas)} at or above the {_n(c.target_roas)} target on "
                        f"{int(c.purchases)} purchases, live in 1 ad set",
                detail=f"Only running in {c.placements[0].adset_name}. Duplicate it into another.",
                money_at_stake=c.spend_7d * (c.roas or 0),
                money_label="revenue this creative already produces from one ad set",
                proposal={
                    "action": "duplicate_into_adset",
                    "entity_type": "creative",
                    "from_adset_id": c.placements[0].adset_id,
                },
                **base,
            )
        )

    # GREY --------------------------------------------------------------
    if not c.category or not c.aov_band:
        missing = [n for n, v in (("category", c.category), ("AOV band", c.aov_band)) if not v]
        flags.append(
            Flag(
                key="untagged_creative",
                value=c.spend,
                threshold=None,
                trigger=f"missing {' and '.join(missing)}",
                detail="Coverage, Concentration and the testing queue all read this table.",
                money_at_stake=c.spend,
                money_label="spend the Coverage module cannot see",
                proposal={"action": "tag_creative", "entity_type": "creative"},
                **base,
            )
        )

    if str(c.effective_status).upper() == "ACTIVE" and c.impressions_7d <= 0:
        flags.append(
            Flag(
                key="zero_delivery",
                value=0.0,
                threshold=None,
                trigger="ACTIVE with 0 impressions in the trailing 7 settled days",
                detail="Check for a rejected asset or an audience that cannot fill.",
                money_at_stake=0.0,
                money_label="no spend — but the slot is doing nothing",
                **base,
            )
        )

    if not c.objective_is_conversion:
        flags.append(
            Flag(
                key="mixed_objective",
                value=None,
                threshold=None,
                trigger=f"objective {c.objective or 'unknown'} in a conversion account",
                detail="Excluded from ROAS verdicts so it cannot drag account medians.",
                money_at_stake=c.spend,
                money_label="spend outside the conversion read",
                **base,
            )
        )

    return flags


# --- ad-set-level flags -----------------------------------------------------


def evaluate_adset(a: AdsetContext) -> list[Flag]:
    flags: list[Flag] = []
    base = {
        "entity_type": "adset",
        "entity_id": a.adset_id,
        "entity_name": a.adset_name,
        "account_id": a.account_id,
        "client_name": a.client_name,
    }

    if a.daily_budget:
        expected = a.daily_budget * 7
        if a.spend_7d < UNDERSPEND_RATIO * expected:
            flags.append(
                Flag(
                    key="budget_underspend",
                    value=a.spend_7d,
                    threshold=UNDERSPEND_RATIO * expected,
                    trigger=f"7d spend {_money(a.spend_7d)} vs {_money(expected)} budgeted "
                            f"({_pct(a.spend_7d / expected if expected else 0)} of plan)",
                    detail="Budget you are not using is budget the winners could have had.",
                    money_at_stake=expected - a.spend_7d,
                    money_label="budget left unspent",
                    proposal={
                        "action": "reallocate_budget",
                        "entity_type": "adset",
                        "adset_id": a.adset_id,
                    },
                    **base,
                )
            )

    events = a.optimisation_events_7d if a.optimisation_events_7d is not None else a.purchases_7d
    if events < LEARNING_THRESHOLD_EVENTS:
        flags.append(
            Flag(
                key="under_learning_threshold",
                value=events,
                threshold=float(LEARNING_THRESHOLD_EVENTS),
                trigger=f"{int(events)} optimisation events in the trailing 7 settled days vs "
                        f"{LEARNING_THRESHOLD_EVENTS}",
                detail="Consolidate ad sets or widen the audience before reading anything into "
                       "this ad set's numbers.",
                money_at_stake=a.spend_7d,
                money_label="spend delivering under the learning threshold",
                **base,
            )
        )

    return flags


# --- account-level flags ----------------------------------------------------


def evaluate_account(acc: AccountContext) -> list[Flag]:
    flags: list[Flag] = []
    base = {
        "entity_type": "account",
        "entity_id": acc.account_id,
        "entity_name": acc.client_name,
        "account_id": acc.account_id,
        "client_name": acc.client_name,
    }

    for category, campaigns in acc.active_campaigns_by_category.items():
        if len(campaigns) >= 2:
            flags.append(
                Flag(
                    key="self_competition",
                    entity_type="category",
                    entity_id=f"{acc.account_id}:{category}",
                    entity_name=category,
                    account_id=acc.account_id,
                    client_name=acc.client_name,
                    value=float(len(campaigns)),
                    threshold=2.0,
                    trigger=f"{category} is live in {len(campaigns)} campaigns: "
                            f"{', '.join(campaigns[:4])}",
                    detail="Same category, same auction, your own money on both sides.",
                    money_at_stake=acc.category_spend.get(category, 0.0),
                    money_label="spend in this category",
                )
            )

    gaps_per_category: dict[str, int] = {}
    for cell in acc.coverage_gaps:
        gaps_per_category[cell["category"]] = gaps_per_category.get(cell["category"], 0) + 1

    for cell in acc.coverage_gaps:
        # Split the category's spend across its own gaps. Attributing the whole
        # category to each cell would multiply it by the number of gaps and
        # make the group total meaningless.
        share = acc.category_spend.get(cell["category"], 0.0) / gaps_per_category[cell["category"]]
        flags.append(
            Flag(
                key="coverage_gap",
                entity_type="cell",
                entity_id=f"{acc.account_id}:{cell['category']}:{cell['angle_id']}",
                entity_name=f"{cell['category']} x {cell['angle_id']}",
                account_id=acc.account_id,
                client_name=acc.client_name,
                value=float(cell["impressions"]),
                threshold=float(COVERAGE_CELL_IMPRESSIONS),
                trigger=f"{int(cell['impressions']):,} cumulative impressions vs "
                        f"{COVERAGE_CELL_IMPRESSIONS:,}",
                detail="Untested, not failed. Brief it before writing the angle off.",
                money_at_stake=share,
                money_label="share of category spend riding on the angles already tested",
                proposal={"action": "brief_creative", "entity_type": "cell"},
            )
        )

    for band in acc.concentration:
        if band["hhi"] > HHI_CONCENTRATION:
            flags.append(
                Flag(
                    key="concentration_risk",
                    entity_type="band",
                    entity_id=f"{acc.account_id}:{band['aov_band']}",
                    entity_name=f"{band['aov_band']} AOV band",
                    account_id=acc.account_id,
                    client_name=acc.client_name,
                    value=band["hhi"],
                    threshold=HHI_CONCENTRATION,
                    trigger=f"HHI {band['hhi']:.3f} vs {HHI_CONCENTRATION} across "
                            f"{band['angles']} angles",
                    detail=f"{_pct(band['top_share'])} of band spend sits on "
                           f"{band['top_angle']}.",
                    money_at_stake=band["spend"],
                    money_label="lifetime spend concentrated in this band",
                )
            )

    if acc.last_sync_status != "ok" or (
        acc.hours_since_sync is not None and acc.hours_since_sync > SYNC_STALE_HOURS
    ):
        hours = acc.hours_since_sync
        flags.append(
            Flag(
                key="sync_failure",
                value=hours,
                threshold=float(SYNC_STALE_HOURS),
                trigger=(
                    f"last sync {_n(hours, 1)}h ago vs {SYNC_STALE_HOURS}h"
                    if hours is not None
                    else "never synced"
                )
                + (f" · {acc.last_sync_error}" if acc.last_sync_error else ""),
                detail="Every verdict below is being made on stale numbers.",
                money_at_stake=0.0,
                money_label="no direct cost — but nothing here is current",
                proposal={"action": "resync", "entity_type": "account"},
                **base,
            )
        )

    return flags


def rank_flags(flags: list[Flag]) -> list[Flag]:
    """Section 5: rank within severity groups by money at stake."""
    order = {"red": 0, "amber": 1, "blue": 2, "grey": 3}
    return sorted(flags, key=lambda f: (order[f.severity], -f.money_at_stake, f.entity_name))
