"""The bulk-tagging screen.

`creative_meta` cannot be derived from the API. Ad names are unreliable — in
the reference account 65% of spend sits on numeric-only names like `112-4`.
Everything in the Coverage module depends on this table being filled in, so
this screen is built for speed: every untagged creative in one table, sorted
by spend, multi-select, one tag applied to all.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

from ..db import get_db
from ..deps import bundle_or_404
from ..models import BulkTagIn, CreativeMetaIn
from ..service import invalidate, untagged

router = APIRouter(prefix="/api/tagging", tags=["tagging"])

TAG_FIELDS = ("category", "aov_band", "angle_id", "format", "hook_type", "offer_type", "lp_type")


@router.get("/{account_id}")
async def tagging_queue(
    account_id: str,
    preset: str = "90d",
    only_untagged: bool = True,
    limit: int = Query(500, le=2000),
):
    """Untagged creatives first, highest spend at the top.

    Working down by spend is what actually moves `untagged share of spend`,
    which is the number that decides when this screen can be put away.
    """
    bundle = await bundle_or_404(account_id, preset)
    rows = []
    for view in bundle.views:
        tags = {field: view.meta.get(field) for field in TAG_FIELDS}
        complete = bool(tags["category"] and tags["aov_band"])
        if only_untagged and complete:
            continue
        rows.append(
            {
                "creative_id": view.creative_id,
                "name": view.name,
                "thumbnail_url": view.thumbnail_url,
                "ad_ids": view.ad_ids,
                "ad_count": len(view.ad_ids),
                "spend": round(view.window.spend, 2),
                "purchases": int(view.window.purchases),
                "roas": round(view.window.roas, 3) if view.window.roas is not None else None,
                "days_live": view.window.days_live,
                "campaigns": sorted({p.campaign_name for p in view.placements if p.campaign_name}),
                "tags": tags,
                "complete": complete,
                "notes": view.meta.get("notes"),
            }
        )
    rows.sort(key=lambda r: -r["spend"])

    return {
        "meta": bundle.as_meta(),
        "rows": rows[:limit],
        "untagged": untagged(bundle),
        "vocabulary": await _vocabulary(account_id),
        "total": len(rows),
    }


async def _vocabulary(account_id: str) -> dict:
    """Existing values, so the dropdowns stay consistent instead of growing a
    new spelling of the same angle every week."""
    db = get_db()
    out: dict[str, list[str]] = {}
    for field in TAG_FIELDS:
        values = await db.creative_meta.distinct(field, {"account_id": account_id})
        out[field] = sorted(v for v in values if v)
    out["aov_band"] = ["low", "high"]
    return out


@router.put("/{account_id}/{creative_id}")
async def tag_one(account_id: str, creative_id: str, payload: CreativeMetaIn):
    db = get_db()
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    await db.creative_meta.update_one(
        {"account_id": account_id, "creative_id": creative_id},
        {
            "$set": {**update, "updated_at": datetime.now(UTC)},
            "$setOnInsert": {"account_id": account_id, "creative_id": creative_id},
        },
        upsert=True,
    )
    invalidate(account_id)
    return {"creative_id": creative_id, "tags": update}


@router.post("/{account_id}/bulk")
async def tag_bulk(account_id: str, payload: BulkTagIn):
    """Multi-select rows and apply a tag to all of them at once.

    Only the fields you actually set are written, so you can sweep `category`
    across forty creatives without clearing the `angle_id` you set yesterday.
    """
    db = get_db()
    update = {k: v for k, v in payload.tags.model_dump().items() if v is not None}
    if not update or not payload.creative_ids:
        return {"updated": 0, "tags": update}

    from pymongo import UpdateOne

    operations = [
        UpdateOne(
            {"account_id": account_id, "creative_id": creative_id},
            {
                "$set": {**update, "updated_at": datetime.now(UTC)},
                "$setOnInsert": {"account_id": account_id, "creative_id": creative_id},
            },
            upsert=True,
        )
        for creative_id in payload.creative_ids
    ]
    result = await db.creative_meta.bulk_write(operations, ordered=False)
    invalidate(account_id)
    return {
        "updated": result.modified_count + result.upserted_count,
        "creative_ids": payload.creative_ids,
        "tags": update,
    }


@router.get("/{account_id}/ad-ids")
async def ad_ids_for_creatives(account_id: str, creative_ids: str):
    """`creative_meta.ad_ids[]` kept current from snapshots.

    Ranks aggregate by creative; pause actions need the ad ids underneath.
    """
    bundle = await bundle_or_404(account_id, "90d")
    wanted = set(creative_ids.split(","))
    return {v.creative_id: v.ad_ids for v in bundle.views if v.creative_id in wanted}
