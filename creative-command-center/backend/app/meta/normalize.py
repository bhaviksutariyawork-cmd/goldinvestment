"""Insights JSON -> snapshot documents.

Two data rules are enforced at the door, so nothing downstream has to
remember them:

* `omni_purchase_values` is never read. It carries a confirmed 100x decimal
  error in the Marketing API. Revenue is derived from `purchase_roas` and
  `spend` at read time — see `core.metrics.row_revenue`.
* The click denominator is `outbound_clicks`, never `link_clicks` and never
  `clicks (all)`. Those count clicks that never left Meta.
"""

from __future__ import annotations

from collections.abc import Iterable

# Preferred action types, best first. Meta's naming drifted over the years and
# older accounts still report the un-prefixed variants.
ACTION_ALIASES = {
    "omni_purchase": ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"),
    "omni_add_to_cart": (
        "omni_add_to_cart",
        "add_to_cart",
        "offsite_conversion.fb_pixel_add_to_cart",
    ),
    "omni_landing_page_view": (
        "omni_landing_page_view",
        "landing_page_view",
        "offsite_conversion.fb_pixel_view_content",
    ),
}

# Never read. Named here so a future reader can see the omission is deliberate.
FORBIDDEN_FIELDS = ("omni_purchase_values", "action_values", "purchase_values")


def action_value(actions: Iterable[dict] | None, canonical: str) -> float:
    """Pull one action type out of Meta's actions array."""
    if not actions:
        return 0.0
    index = {str(a.get("action_type")): a for a in actions}
    for alias in ACTION_ALIASES.get(canonical, (canonical,)):
        if alias in index:
            return _f(index[alias].get("value"))
    return 0.0


def outbound_clicks(row: dict) -> float:
    """Data rule 6. `outbound_clicks` arrives as an array, not a scalar."""
    value = row.get("outbound_clicks")
    if isinstance(value, list):
        return sum(_f(v.get("value")) for v in value)
    return _f(value)


def outbound_ctr(row: dict) -> float | None:
    value = row.get("outbound_clicks_ctr")
    if isinstance(value, list):
        return sum(_f(v.get("value")) for v in value) / 100.0 or None
    return (_f(value) / 100.0) if value not in (None, "") else None


def purchase_roas(row: dict) -> float:
    """The ROAS Meta reports, kept raw. Revenue is `this x spend`, computed at
    read time — the multiplication never happens here so a single definition of
    revenue lives in `core.metrics`."""
    value = row.get("purchase_roas")
    if isinstance(value, list):
        for entry in value:
            if str(entry.get("action_type")) in ("omni_purchase", "purchase"):
                return _f(entry.get("value"))
        return _f(value[0].get("value")) if value else 0.0
    return _f(value)


def normalize_insight(
    row: dict,
    *,
    account_id: str,
    level: str,
    ad_index: dict[str, dict] | None = None,
) -> dict | None:
    """One insights row -> one snapshot document.

    `ad_index` supplies `creative_id`, `effective_status` and `thumbnail_url`,
    which insights does not return. An ad we cannot resolve to a creative is
    dropped rather than stored under a fabricated key — a wrong `creative_id`
    would silently merge two different assets into one Leaderboard row.
    """
    ad_index = ad_index or {}
    day = row.get("date_start")
    if not day:
        return None

    actions = row.get("actions")
    base = {
        "account_id": account_id,
        "date": str(day)[:10],
        "campaign_id": str(row.get("campaign_id") or ""),
        "campaign_name": row.get("campaign_name"),
        "adset_id": str(row.get("adset_id") or ""),
        "adset_name": row.get("adset_name"),
        "objective": row.get("objective"),
        "amount_spent": _f(row.get("spend")),
        "impressions": _f(row.get("impressions")),
        "reach": _f(row.get("reach")),
        "frequency": _f(row.get("frequency")),
        "cpm": _f(row.get("cpm")),
        "ctr": _f(row.get("ctr")) / 100.0 if row.get("ctr") not in (None, "") else None,
        "outbound_clicks": outbound_clicks(row),
        "outbound_clicks_ctr": outbound_ctr(row),
        "omni_landing_page_view": action_value(actions, "omni_landing_page_view"),
        "omni_add_to_cart": action_value(actions, "omni_add_to_cart"),
        "omni_purchase": action_value(actions, "omni_purchase"),
        "purchase_roas": purchase_roas(row),
    }

    if level != "ad":
        entity_id = str(row.get(f"{level}_id") or "")
        return {
            **base,
            "entity_type": level,
            "entity_id": entity_id,
            "name": row.get(f"{level}_name"),
        }

    ad_id = str(row.get("ad_id") or "")
    ad = ad_index.get(ad_id)
    if not ad or not ad.get("creative_id"):
        return None

    return {
        **base,
        "ad_id": ad_id,
        "ad_name": row.get("ad_name") or ad.get("ad_name"),
        "creative_id": str(ad["creative_id"]),
        "thumbnail_url": ad.get("thumbnail_url"),
        "effective_status": ad.get("effective_status") or "UNKNOWN",
        "created_time": ad.get("created_time"),
    }


def _f(value) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
