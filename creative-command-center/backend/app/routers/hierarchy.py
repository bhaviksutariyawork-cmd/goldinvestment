"""Screen A — Hierarchy Explorer.

The URL carries the whole position (level, campaign, ad set, window), so the
browser back button works and a view can be pasted to someone else.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..core.hierarchy import adset_delivery_bar, budget_pacing, build_level, learning_threshold
from ..core.windows import parse_date, settled_end
from ..deps import bundle_or_404
from ..service import build_flags

router = APIRouter(prefix="/api/hierarchy", tags=["hierarchy"])


@router.get("/{account_id}")
async def hierarchy(
    account_id: str,
    level: str = Query("campaign", pattern="^(campaign|adset|ad)$"),
    campaign_id: str | None = None,
    adset_id: str | None = None,
    preset: str = "30d",
    start: str | None = None,
    end: str | None = None,
):
    """One table. `level=ad` with no parent id is the "All Ads" flatten."""
    bundle = await bundle_or_404(account_id, preset, start, end)
    rows = build_level(
        bundle.rows,
        level,
        bundle.window,
        campaign_id=campaign_id,
        adset_id=adset_id,
        targets_by_band=bundle.targets_by_band,
        meta_by_creative=bundle.meta,
    )

    # Attach flags so the last column is populated without a second request.
    flags = build_flags(bundle)
    by_entity: dict[str, list[dict]] = {}
    for flag in flags:
        by_entity.setdefault(flag.entity_id, []).append(
            {"key": flag.key, "label": flag.label, "severity": flag.severity, "trigger": flag.trigger}
        )
    for row in rows:
        keys = {row["id"]}
        if level == "ad" and row.get("creative_id"):
            keys.add(row["creative_id"])
        row["flags"] = [f for key in keys for f in by_entity.get(key, [])]

    return {
        "meta": bundle.as_meta(),
        "level": level,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "rows": rows,
        "breadcrumb": _breadcrumb(bundle.rows, campaign_id, adset_id),
    }


def _breadcrumb(rows, campaign_id: str | None, adset_id: str | None) -> list[dict]:
    crumbs = [{"label": "All campaigns", "level": "campaign"}]
    if campaign_id:
        name = next(
            (r.get("campaign_name") for r in rows if str(r.get("campaign_id")) == campaign_id),
            campaign_id,
        )
        crumbs.append({"label": name, "level": "adset", "campaign_id": campaign_id})
    if adset_id:
        name = next(
            (r.get("adset_name") for r in rows if str(r.get("adset_id")) == adset_id), adset_id
        )
        crumbs.append(
            {"label": name, "level": "ad", "campaign_id": campaign_id, "adset_id": adset_id}
        )
    return crumbs


@router.get("/{account_id}/adset/{adset_id}")
async def adset_detail(
    account_id: str,
    adset_id: str,
    preset: str = "30d",
    start: str | None = None,
    end: str | None = None,
):
    """Ad-set detail: the delivery bar, the learning line, and budget pacing."""
    bundle = await bundle_or_404(account_id, preset, start, end)
    segments = adset_delivery_bar(bundle.rows, adset_id, bundle.window)
    if not segments:
        raise HTTPException(status_code=404, detail="No delivery for that ad set in this window")

    ads = build_level(
        bundle.rows,
        "ad",
        bundle.window,
        adset_id=adset_id,
        targets_by_band=bundle.targets_by_band,
        meta_by_creative=bundle.meta,
    )
    best = max(segments, key=lambda s: (s["roas"] or 0))
    widest = max(segments, key=lambda s: s["delivery_share"])
    misallocated = best["ad_id"] != widest["ad_id"] and (best["roas"] or 0) > (widest["roas"] or 0)

    return {
        "meta": bundle.as_meta(),
        "adset_id": adset_id,
        "adset_name": next(
            (r.get("adset_name") for r in bundle.rows if str(r.get("adset_id")) == adset_id),
            adset_id,
        ),
        "delivery_bar": segments,
        "ads": ads,
        "learning_threshold": learning_threshold(bundle.rows, adset_id, bundle.as_of),
        "budget_pacing": budget_pacing(bundle.rows, bundle.entity_rows, adset_id, bundle.as_of),
        "misallocation": {
            "present": misallocated,
            "best_ad": best,
            "widest_ad": widest,
            "gap_pct": round(
                (widest["delivery_share"] / best["delivery_share"] - 1) * 100, 1
            )
            if misallocated and best["delivery_share"]
            else None,
        },
    }


@router.get("/{account_id}/dates")
async def available_dates(account_id: str):
    bundle = await bundle_or_404(account_id, "90d", with_views=False)
    days = sorted({str(r["date"]) for r in bundle.rows})
    first_settling = parse_date(bundle.settling["start"])
    return {
        "as_of": bundle.as_of.isoformat(),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "settled_through": settled_end(bundle.as_of).isoformat(),
        "settling_window": bundle.settling,
        # Every chart marks these days "settling" rather than plotting them as fact.
        "settling_dates": [d for d in days if parse_date(d) >= first_settling],
    }
