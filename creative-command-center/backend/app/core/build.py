"""Turn raw snapshot rows into the views every screen reads.

This is the only module that knows how the pieces fit: rows in, creative
views with placements, verdicts, ranks and flag contexts out. Screens read
these views; they never touch snapshot rows themselves.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .constants import (
    CONVERSION_OBJECTIVES,
    MIN_PURCHASES_FOR_VERDICT,
    RANK_MOVEMENT_PRIOR_LAG,
    RANK_MOVEMENT_RECENT_LAG,
    TRAILING_WINDOW_DAYS,
)
from .flags import CreativeContext, Placement
from .metrics import Metrics, aggregate, first_days_window, group_by, median, percentile
from .reach import ReachKey, key_for, lookup
from .status import StatusInput, StatusResult, classify, is_ranked
from .windows import Window, parse_date, settled_end, trailing


def is_conversion_objective(objective: str | None) -> bool:
    return str(objective or "").upper() in CONVERSION_OBJECTIVES


@dataclass
class AccountBenchmarks:
    """Account-relative yardsticks. Percentiles are computed across creatives,
    weighted by nothing — this is a distribution of assets, not of rupees."""

    median_outbound_ctr: float | None = None
    cpoc_p25: float | None = None
    cpoc_p50: float | None = None
    cpoc_p75: float | None = None

    def as_dict(self) -> dict:
        return {
            "median_outbound_ctr": self.median_outbound_ctr,
            "cost_per_outbound_click_p25": self.cpoc_p25,
            "cost_per_outbound_click_p50": self.cpoc_p50,
            "cost_per_outbound_click_p75": self.cpoc_p75,
        }


@dataclass
class CreativeView:
    account_id: str
    client_name: str
    creative_id: str
    name: str
    thumbnail_url: str | None
    ad_ids: list[str]
    effective_status: str
    objective: str
    meta: dict
    window: Metrics
    lifetime: Metrics
    trailing7: Metrics
    prior7: Metrics
    first7: Metrics
    target_roas: float | None
    target_cpa: float | None
    placements: list[Placement]
    status: StatusResult
    streak: int = 0
    rank: int | None = None
    prior_rank: int | None = None

    @property
    def rank_movement(self) -> int | None:
        """Positive means the creative climbed.

        Both ranks come from windows that end clear of the settling lag, so a
        stable creative does not appear to move just because Meta is still
        revising yesterday.
        """
        if self.rank is None or self.prior_rank is None:
            return None
        return self.prior_rank - self.rank

    @property
    def is_ranked(self) -> bool:
        return is_ranked(self.status.status, self.window.purchases)

    def as_dict(self) -> dict:
        return {
            "creative_id": self.creative_id,
            "account_id": self.account_id,
            "client_name": self.client_name,
            "name": self.name,
            "thumbnail_url": self.thumbnail_url,
            "ad_ids": self.ad_ids,
            "ad_count": len(self.ad_ids),
            "adset_count": len(self.placements),
            "effective_status": self.effective_status,
            "objective": self.objective,
            "category": self.meta.get("category"),
            "aov_band": self.meta.get("aov_band"),
            "angle_id": self.meta.get("angle_id"),
            "format": self.meta.get("format"),
            "hook_type": self.meta.get("hook_type"),
            "offer_type": self.meta.get("offer_type"),
            "lp_type": self.meta.get("lp_type"),
            "target_roas": self.target_roas,
            "target_cpa": self.target_cpa,
            "metrics": self.window.as_dict(),
            "trailing7": self.trailing7.as_dict(),
            "lifetime": self.lifetime.as_dict(),
            **self.status.as_dict(),
            "streak": self.streak,
            "rank": self.rank,
            "prior_rank": self.prior_rank,
            "rank_movement": self.rank_movement,
            "is_ranked": self.is_ranked,
            "placements": [
                {
                    "adset_id": p.adset_id,
                    "adset_name": p.adset_name,
                    "campaign_id": p.campaign_id,
                    "campaign_name": p.campaign_name,
                    "spend": round(p.spend, 2),
                    "roas": round(p.roas, 3) if p.roas is not None else None,
                    "delivery_share": round(p.delivery_share, 4)
                    if p.delivery_share is not None
                    else None,
                    "is_best_roas": p.is_best_roas,
                    "rival_spend": round(p.rival_spend, 2),
                    "rival_roas": round(p.rival_roas, 3) if p.rival_roas is not None else None,
                    "ad_ids": p.ad_ids,
                }
                for p in self.placements
            ],
        }


def as_of_date(rows: Sequence[dict]) -> date | None:
    days = [parse_date(r["date"]) for r in rows]
    return max(days) if days else None


def targets_for(targets: Sequence[dict], aov_band: str | None) -> tuple[float | None, float | None]:
    """A single client-level ROAS target mislabels both bands, so targets are
    always resolved per band. An untagged creative gets no target and lands in
    HOLD with an explicit "set a target" reason rather than a false verdict."""
    if not aov_band:
        return None, None
    for t in targets:
        if t.get("aov_band") == aov_band:
            return t.get("target_roas"), t.get("target_cpa")
    return None, None


def _placements(
    creative_rows: Sequence[dict], adset_totals: dict[str, Metrics], adset_names: dict[str, dict],
    adset_best: dict[str, str],
) -> list[Placement]:
    out: list[Placement] = []
    by_adset = group_by(creative_rows, "adset_id")
    for adset_id, metrics in by_adset.items():
        parent = adset_totals.get(adset_id, Metrics())
        rival_spend = max(parent.spend - metrics.spend, 0.0)
        rival_revenue = max(parent.revenue - metrics.revenue, 0.0)
        names = adset_names.get(adset_id, {})
        out.append(
            Placement(
                adset_id=adset_id,
                adset_name=names.get("adset_name", adset_id),
                campaign_id=names.get("campaign_id", ""),
                campaign_name=names.get("campaign_name", ""),
                spend=metrics.spend,
                roas=metrics.roas,
                # Delivery share: the ad's share of its parent ad set's spend.
                # The only place intra-ad-set misallocation becomes visible.
                delivery_share=(metrics.spend / parent.spend) if parent.spend > 0 else None,
                is_best_roas=adset_best.get(adset_id) == _creative_id(creative_rows),
                adset_spend=parent.spend,
                rival_spend=rival_spend,
                rival_roas=(rival_revenue / rival_spend) if rival_spend > 0 else None,
                ad_ids=sorted({str(r["ad_id"]) for r in creative_rows
                               if str(r.get("adset_id")) == adset_id}),
            )
        )
    out.sort(key=lambda p: -p.spend)
    return out


def _creative_id(rows: Sequence[dict]) -> str:
    return str(rows[0]["creative_id"])


def best_creative_per_adset(rows: Iterable[dict], window: Window | None = None) -> dict[str, str]:
    """Which creative holds the best ROAS inside each ad set.

    Restricted to creatives that actually competed for that ad set's budget —
    ads in different ad sets never competed, so comparing them here would be
    the same portfolio theatre the Within Ad Set tab exists to avoid.
    """
    by_adset: dict[str, dict[str, Metrics]] = {}
    for row in rows:
        if window is not None and not window.contains(parse_date(row["date"])):
            continue
        adset_id = str(row.get("adset_id"))
        creative_id = str(row.get("creative_id"))
        by_adset.setdefault(adset_id, {}).setdefault(creative_id, Metrics()).add(row)

    best: dict[str, str] = {}
    for adset_id, creatives in by_adset.items():
        spending = {cid: m for cid, m in creatives.items() if m.spend > 0}
        if len(spending) < 2:
            continue  # a lone creative is not out-earning anyone
        winner = max(spending.items(), key=lambda kv: (kv[1].roas or 0.0, kv[1].spend))
        if (winner[1].roas or 0.0) > 0:
            best[adset_id] = winner[0]
    return best


def win_streak(rows: Sequence[dict], target_roas: float | None, end: date,
               max_days: int = 90) -> int:
    """Consecutive settled days on which the creative held WIN.

    Evaluated on cumulative-to-date numbers, which is how the operator reads
    it: "it has been over target, on an adequate sample, for eleven days".
    """
    if not target_roas:
        return 0
    by_day = group_by(rows, "date")
    days = sorted(d for d in by_day if parse_date(d) <= end)
    if not days:
        return 0

    cumulative = Metrics()
    verdicts: list[tuple[date, bool]] = []
    for day in days:
        cumulative.merge(by_day[day])
        held = (
            cumulative.purchases >= MIN_PURCHASES_FOR_VERDICT
            and cumulative.roas is not None
            and cumulative.roas >= target_roas
        )
        verdicts.append((parse_date(day), held))

    streak = 0
    cursor = end
    for day, held in reversed(verdicts):
        if day != cursor:
            break  # a gap in delivery breaks the streak
        if not held:
            break
        streak += 1
        cursor = cursor - timedelta(days=1)
        if streak >= max_days:
            break
    return streak


def benchmarks(views: Sequence[CreativeView]) -> AccountBenchmarks:
    ctrs = [v.window.outbound_ctr for v in views if v.window.outbound_ctr is not None]
    cpocs = [
        v.window.cost_per_outbound_click
        for v in views
        if v.window.cost_per_outbound_click is not None
    ]
    return AccountBenchmarks(
        median_outbound_ctr=median(ctrs),
        cpoc_p25=percentile(cpocs, 0.25),
        cpoc_p50=percentile(cpocs, 0.50),
        cpoc_p75=percentile(cpocs, 0.75),
    )


def build_views(
    rows: Sequence[dict],
    *,
    window: Window,
    as_of: date,
    meta_by_creative: dict[str, dict],
    targets: Sequence[dict],
    account: dict,
    manual_pauses: set[str] | None = None,
    reach_index: dict[ReachKey, dict] | None = None,
) -> list[CreativeView]:
    """The main entry point. `rows` are snapshot_daily documents for one account.

    `reach_index` carries Meta's deduplicated reach per named window. Without
    it frequency falls back to a lower bound built from daily rows — see
    `core.reach` for why that distinction matters.
    """
    manual_pauses = manual_pauses or set()
    reach_index = reach_index or {}
    window_key = key_for(window, as_of)
    edge = settled_end(as_of)
    trail7 = trailing(edge, TRAILING_WINDOW_DAYS)
    prior7 = trailing(edge - timedelta(days=TRAILING_WINDOW_DAYS), TRAILING_WINDOW_DAYS)

    windowed = [r for r in rows if window.contains(parse_date(r["date"]))]
    adset_totals = group_by(windowed, "adset_id")
    adset_best = best_creative_per_adset(windowed)
    adset_names: dict[str, dict] = {}
    for r in windowed:
        adset_names.setdefault(
            str(r.get("adset_id")),
            {
                "adset_name": r.get("adset_name") or str(r.get("adset_id")),
                "campaign_id": str(r.get("campaign_id") or ""),
                "campaign_name": r.get("campaign_name") or "",
            },
        )

    by_creative: dict[str, list[dict]] = {}
    for r in rows:
        by_creative.setdefault(str(r["creative_id"]), []).append(r)

    views: list[CreativeView] = []
    for creative_id, crows in by_creative.items():
        in_window = [r for r in crows if window.contains(parse_date(r["date"]))]
        if not in_window:
            continue
        latest = max(crows, key=lambda r: r["date"])
        meta = meta_by_creative.get(creative_id, {})
        target_roas, target_cpa = targets_for(targets, meta.get("aov_band"))

        window_metrics = aggregate(in_window).attach_reach(
            *lookup(reach_index, creative_id, window_key)
        )
        lifetime = aggregate([r for r in crows if parse_date(r["date"]) <= edge]).attach_reach(
            *lookup(reach_index, creative_id, "lifetime")
        )
        trailing7 = aggregate(crows, trail7).attach_reach(
            *lookup(reach_index, creative_id, "trailing_7")
        )
        prior7_metrics = aggregate(crows, prior7).attach_reach(
            *lookup(reach_index, creative_id, "prior_7")
        )
        first_window = first_days_window(crows, TRAILING_WINDOW_DAYS)

        placements = _placements(in_window, adset_totals, adset_names, adset_best)
        best_placement = next(
            (p for p in placements if p.is_best_roas),
            None,
        )
        # If it is the best in more than one ad set, the most starved placement
        # is the one worth surfacing.
        starved = min(
            (p for p in placements if p.is_best_roas and p.delivery_share is not None),
            key=lambda p: p.delivery_share,
            default=best_placement,
        )

        objective = latest.get("objective") or ""
        effective_status = latest.get("effective_status") or "ACTIVE"

        status = classify(
            StatusInput(
                purchases=window_metrics.purchases,
                manual_paused=creative_id in manual_pauses,
                effective_status=effective_status,
                objective_is_conversion=is_conversion_objective(objective),
                impressions_in_window=window_metrics.impressions,
                lpv_transfer=window_metrics.lpv_transfer,
                frequency=window_metrics.frequency,
                roas_trailing=trailing7.roas,
                roas_lifetime=lifetime.roas,
                is_best_roas_in_adset=bool(starved and starved.is_best_roas),
                delivery_share=starved.delivery_share if starved else None,
                target_roas=target_roas,
                cost_per_outbound_click=window_metrics.cost_per_outbound_click,
            )
        )

        views.append(
            CreativeView(
                account_id=str(account.get("_id") or account.get("account_id") or ""),
                client_name=account.get("client_name", ""),
                creative_id=creative_id,
                name=meta.get("name") or latest.get("ad_name") or creative_id,
                thumbnail_url=latest.get("thumbnail_url"),
                ad_ids=sorted({str(r["ad_id"]) for r in in_window}),
                effective_status=effective_status,
                objective=objective,
                meta=meta,
                window=window_metrics,
                lifetime=lifetime,
                trailing7=trailing7,
                prior7=prior7_metrics,
                first7=aggregate(crows, first_window) if first_window else Metrics(),
                target_roas=target_roas,
                target_cpa=target_cpa,
                placements=placements,
                status=status,
                streak=win_streak(crows, target_roas, edge),
            )
        )

    # Second pass: account benchmarks feed the upper-funnel verdict that
    # INSUFFICIENT creatives are judged on, so they need every view built first.
    marks = benchmarks(views)
    for view in views:
        if view.status.status == "INSUFFICIENT":
            view.status = classify(
                StatusInput(
                    purchases=view.window.purchases,
                    effective_status=view.effective_status,
                    objective_is_conversion=is_conversion_objective(view.objective),
                    impressions_in_window=view.window.impressions,
                    cost_per_outbound_click=view.window.cost_per_outbound_click,
                    account_cpoc_p50=marks.cpoc_p50,
                    account_cpoc_p75=marks.cpoc_p75,
                    target_roas=view.target_roas,
                )
            )

    assign_ranks(views)
    attach_rank_movement(views, rows, as_of, window.days)
    return views


def attach_rank_movement(
    views: Sequence[CreativeView], rows: Sequence[dict], as_of: date, window_days: int
) -> None:
    """Set `prior_rank` from a window of the same length ending at D-10.

    Only creatives that hold a rank today are compared, and the prior ranks are
    re-densified over that same population — otherwise a creative appearing or
    dropping out would shift everyone else's movement without anything having
    changed about them.
    """
    ranked = {v.creative_id for v in views if v.rank is not None}
    if not ranked:
        return
    _, prior = rank_movement_ranks(rows, as_of, window_days, eligible=ranked)
    order = sorted(prior.items(), key=lambda kv: kv[1])
    densified = {cid: i for i, (cid, _) in enumerate(order, start=1)}
    for view in views:
        view.prior_rank = densified.get(view.creative_id)


def assign_ranks(views: Sequence[CreativeView]) -> None:
    """Rank by ROAS among creatives that clear the verdict gate.

    Everything else stays rank-less and lands in the Testing table. They are
    candidates, not losers.
    """
    ranked = [v for v in views if v.is_ranked and v.window.roas is not None]
    ranked.sort(key=lambda v: (-(v.window.roas or 0), -v.window.spend))
    for position, view in enumerate(ranked, start=1):
        view.rank = position


def rank_movement_ranks(
    rows: Sequence[dict],
    as_of: date,
    window_days: int,
    eligible: set[str] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Ranks at D-3 and D-10 (data rule 4).

    Both windows are the same length and both end clear of the settling lag,
    so movement reflects performance rather than Meta finishing its counting.
    Comparing D-1 to D-2 would produce movement on a creative that did nothing
    at all.
    """
    recent_end = as_of - timedelta(days=RANK_MOVEMENT_RECENT_LAG)
    prior_end = as_of - timedelta(days=RANK_MOVEMENT_PRIOR_LAG)

    def ranks_at(end: date) -> dict[str, int]:
        grouped = group_by(rows, "creative_id", trailing(end, window_days))
        candidates = [
            (cid, m)
            for cid, m in grouped.items()
            if m.purchases >= MIN_PURCHASES_FOR_VERDICT
            and m.roas is not None
            and (eligible is None or cid in eligible)
        ]
        candidates.sort(key=lambda kv: (-(kv[1].roas or 0), -kv[1].spend))
        return {cid: i for i, (cid, _) in enumerate(candidates, start=1)}

    return ranks_at(recent_end), ranks_at(prior_end)


def to_flag_context(
    view: CreativeView, marks: AccountBenchmarks, account: dict
) -> CreativeContext:
    return CreativeContext(
        account_id=view.account_id,
        client_name=view.client_name,
        creative_id=view.creative_id,
        name=view.name,
        status=view.status.status,
        purchases=view.window.purchases,
        spend=view.window.spend,
        spend_7d=view.trailing7.spend,
        roas=view.window.roas,
        roas_7d=view.trailing7.roas,
        cpa=view.window.cpa,
        frequency=view.window.frequency,
        lpv_transfer=view.window.lpv_transfer,
        outbound_ctr=view.window.outbound_ctr,
        outbound_ctr_7d=view.trailing7.outbound_ctr,
        outbound_ctr_first7=view.first7.outbound_ctr,
        cpm_7d=view.trailing7.cpm,
        cpm_first7=view.first7.cpm,
        reach_7d=view.trailing7.reach,
        reach_prior_7d=view.prior7.reach,
        impressions_7d=view.trailing7.impressions,
        target_roas=view.target_roas,
        target_cpa=view.target_cpa,
        category=view.meta.get("category"),
        aov_band=view.meta.get("aov_band"),
        angle_id=view.meta.get("angle_id"),
        objective=view.objective,
        objective_is_conversion=is_conversion_objective(view.objective),
        effective_status=view.effective_status,
        account_median_outbound_ctr=marks.median_outbound_ctr,
        placements=view.placements,
        thumbnail_url=view.thumbnail_url,
    )
