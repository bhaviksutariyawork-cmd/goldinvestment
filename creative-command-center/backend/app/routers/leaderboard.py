"""Screen B — Leaderboard, and the Within Ad Set tab that matters more."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from ..core.hierarchy import within_adset_groups
from ..db import get_db
from ..deps import bundle_or_404
from ..repo import list_accounts
from ..service import Bundle, load_bundle

router = APIRouter(prefix="/api/leaderboard", tags=["leaderboard"])


def _matches(row: dict, filters: dict) -> bool:
    return all(row.get(key) == wanted for key, wanted in filters.items() if wanted)


async def _bundles(account_id: str | None, preset: str, start: str | None, end: str | None):
    if account_id:
        return [await bundle_or_404(account_id, preset, start, end)]
    db = get_db()
    accounts = await list_accounts(db)
    loaded = await asyncio.gather(
        *(
            load_bundle(db, str(a["_id"]), preset=preset, start=start, end=end)
            for a in accounts
        )
    )
    return [b for b in loaded if b]


@router.get("")
async def leaderboard(
    account_id: str | None = None,
    preset: str = "30d",
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
    aov_band: str | None = None,
    status: str | None = None,
    angle_id: str | None = None,
    format: str | None = None,
    limit: int = Query(200, le=1000),
):
    """Ranked by ROAS among creatives that clear the verdict gate.

    Creatives under 30 purchases get no rank at all. They go to the Testing
    table below, sorted by cost per outbound click and showing upper-funnel
    metrics only — they are candidates, not losers, and ranking them on a thin
    ROAS reading is how viable categories get killed early.

    Aggregated by `creative_id`, so an asset running in three ad sets appears
    once here and three times in the Hierarchy Explorer.
    """
    bundles = await _bundles(account_id, preset, start, end)
    filters = {
        "category": category,
        "aov_band": aov_band,
        "status": status,
        "angle_id": angle_id,
        "format": format,
    }

    ranked: list[dict] = []
    testing: list[dict] = []
    for bundle in bundles:
        for view in bundle.views:
            row = view.as_dict()
            if not _matches(row, filters):
                continue
            (ranked if view.is_ranked else testing).append(row)

    # A global rank has to be re-derived once every client's rows are in one list.
    ranked.sort(key=lambda r: (-(r["metrics"]["roas"] or 0), -r["metrics"]["spend"]))
    prior_order = sorted(
        (r for r in ranked if r["prior_rank"] is not None), key=lambda r: r["prior_rank"]
    )
    prior_positions = {r["creative_id"]: i for i, r in enumerate(prior_order, start=1)}
    for position, row in enumerate(ranked[:limit], start=1):
        row["rank"] = position
        row["prior_rank"] = prior_positions.get(row["creative_id"])
        row["rank_movement"] = (
            row["prior_rank"] - position if row["prior_rank"] is not None else None
        )
        row["badge"] = {1: "gold", 2: "silver", 3: "bronze"}.get(position)

    testing.sort(
        key=lambda r: (
            r["metrics"]["cost_per_outbound_click"] is None,
            r["metrics"]["cost_per_outbound_click"] or 0,
        )
    )

    return {
        "meta": _multi_meta(bundles),
        "ranked": ranked[:limit],
        "testing": [_testing_row(r) for r in testing[:limit]],
        "filters": _filter_options(bundles),
        "counts": {"ranked": len(ranked), "testing": len(testing)},
    }


def _testing_row(row: dict) -> dict:
    """Upper-funnel metrics only. Showing a ROAS next to 3 purchases invites
    exactly the reading the 30-purchase gate exists to prevent."""
    metrics = row["metrics"]
    return {
        **{
            k: row[k]
            for k in (
                "creative_id",
                "name",
                "client_name",
                "account_id",
                "thumbnail_url",
                "category",
                "aov_band",
                "angle_id",
                "status",
                "reason",
                "upper_funnel_verdict",
                "ad_ids",
            )
        },
        "metrics": {
            "spend": metrics["spend"],
            "impressions": metrics["impressions"],
            "outbound_clicks": metrics["outbound_clicks"],
            "cost_per_outbound_click": metrics["cost_per_outbound_click"],
            "outbound_ctr": metrics["outbound_ctr"],
            "lpv_transfer": metrics["lpv_transfer"],
            "cpm": metrics["cpm"],
            "frequency": metrics["frequency"],
            "purchases": metrics["purchases"],
            "days_live": metrics["days_live"],
        },
    }


def _filter_options(bundles: list[Bundle]) -> dict:
    def collect(field: str) -> list[str]:
        return sorted(
            {
                v.meta.get(field)
                for b in bundles
                for v in b.views
                if v.meta.get(field)
            }
        )

    return {
        "clients": sorted({b.client_name for b in bundles}),
        "categories": collect("category"),
        "aov_bands": collect("aov_band"),
        "angles": collect("angle_id"),
        "formats": collect("format"),
        "statuses": sorted({v.status.status for b in bundles for v in b.views}),
    }


def _multi_meta(bundles: list[Bundle]) -> dict:
    if len(bundles) == 1:
        return bundles[0].as_meta()
    if not bundles:
        return {"accounts": [], "as_of": None, "window": None, "settling_window": None}
    newest = max(b.as_of for b in bundles)
    return {
        "accounts": [b.as_meta() for b in bundles],
        "as_of": newest.isoformat(),
        "window": bundles[0].window.as_dict(),
        "settling_window": bundles[0].settling,
    }


@router.get("/within-adset")
async def within_adset(
    account_id: str | None = None,
    preset: str = "30d",
    start: str | None = None,
    end: str | None = None,
):
    """Grouped by ad set, showing only ads that competed for the same budget.

    Ads in different ad sets never competed. A global rank comparing them is
    portfolio theatre; this tab is where the real decisions are.
    """
    bundles = await _bundles(account_id, preset, start, end)
    groups = []
    for bundle in bundles:
        for group in within_adset_groups(
            bundle.rows, bundle.window, bundle.meta, bundle.targets_by_band
        ):
            groups.append({**group, "client_name": bundle.client_name, "account_id": bundle.account_id})
    groups.sort(key=lambda g: (not g["misallocated"], -g["spend"]))
    return {
        "meta": _multi_meta(bundles),
        "groups": groups,
        "misallocated_count": sum(1 for g in groups if g["misallocated"]),
    }
