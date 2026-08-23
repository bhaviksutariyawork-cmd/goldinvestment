"""Creative detail — trend charts, placements, verdict history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..core.metrics import daily_series, group_by
from ..core.windows import parse_date, settled_end
from ..deps import bundle_or_404
from ..service import build_flags

router = APIRouter(prefix="/api/creatives", tags=["creatives"])


@router.get("/{account_id}/{creative_id}")
async def creative_detail(account_id: str, creative_id: str, preset: str = "90d"):
    """One creative, everything known about it.

    The series carries a `settling` flag per point. Charts must render those
    days differently — they are still moving, and a reader who treats the last
    three points as fact will see a decline that is not there.
    """
    bundle = await bundle_or_404(account_id, preset)
    view = next((v for v in bundle.views if v.creative_id == creative_id), None)
    if view is None:
        raise HTTPException(status_code=404, detail="No such creative in this window")

    rows = [r for r in bundle.rows if str(r["creative_id"]) == creative_id]
    edge = settled_end(bundle.as_of)
    series = daily_series(rows)
    for point in series:
        point["settling"] = parse_date(point["date"]) > edge

    by_adset = group_by(rows, "adset_id", bundle.window)
    flags = [
        f.as_dict()
        for f in build_flags(bundle)
        if f.entity_id == creative_id
        or f.entity_id in {p.adset_id for p in view.placements}
    ]

    return {
        "meta": bundle.as_meta(),
        "creative": view.as_dict(),
        "series": series,
        "settled_through": edge.isoformat(),
        "flags": flags,
        "by_adset": [
            {"adset_id": adset_id, **metrics.as_dict()} for adset_id, metrics in by_adset.items()
        ],
        "ads": sorted(
            {
                (str(r["ad_id"]), r.get("ad_name") or str(r["ad_id"]), str(r.get("adset_id")))
                for r in rows
            }
        ),
    }
