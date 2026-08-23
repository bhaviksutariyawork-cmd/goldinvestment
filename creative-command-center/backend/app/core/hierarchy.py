"""Hierarchy Explorer rows — section 3.

Campaign -> Ad Set -> Ads, every level carrying the same column set so the eye
does not have to re-learn the table on each drill-down.

Delivery share is the reason this screen exists. At ad level it is the ad's
share of its parent ad set's spend, and it is the only place intra-ad-set
misallocation becomes visible: an ad set quietly funding a worse-ROAS creative
over a better one shows up nowhere else in Ads Manager.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from .constants import LEARNING_THRESHOLD_EVENTS, TRAILING_WINDOW_DAYS
from .metrics import Metrics, group_by
from .status import StatusInput, classify
from .windows import Window, parse_date, settled_end, trailing

LEVELS = ("campaign", "adset", "ad")

_KEY = {"campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}
_NAME = {"campaign": "campaign_name", "adset": "adset_name", "ad": "ad_name"}
_PARENT_KEY = {"campaign": None, "adset": "campaign_id", "ad": "adset_id"}


def _row_identity(rows: Sequence[dict], level: str) -> dict:
    latest = max(rows, key=lambda r: r["date"])
    return {
        "id": str(latest[_KEY[level]]),
        "name": latest.get(_NAME[level]) or str(latest[_KEY[level]]),
        "status": latest.get("effective_status") or "UNKNOWN",
        "objective": latest.get("objective") or "",
        "campaign_id": str(latest.get("campaign_id") or ""),
        "campaign_name": latest.get("campaign_name") or "",
        "adset_id": str(latest.get("adset_id") or ""),
        "adset_name": latest.get("adset_name") or "",
        "creative_id": str(latest.get("creative_id") or ""),
        "thumbnail_url": latest.get("thumbnail_url"),
    }


def build_level(
    rows: Sequence[dict],
    level: str,
    window: Window,
    *,
    campaign_id: str | None = None,
    adset_id: str | None = None,
    targets_by_band: dict | None = None,
    meta_by_creative: dict[str, dict] | None = None,
) -> list[dict]:
    """One table of rows for the requested level, filtered to the parent in view.

    `delivery_share` is always computed against the row's own parent, so the
    "All Ads" flatten keeps every ad's share meaningful even when ads from
    twenty different ad sets sit in one table.
    """
    meta_by_creative = meta_by_creative or {}
    scoped = [
        r
        for r in rows
        if window.contains(parse_date(r["date"]))
        and (campaign_id is None or str(r.get("campaign_id")) == campaign_id)
        and (adset_id is None or str(r.get("adset_id")) == adset_id)
    ]
    if not scoped:
        return []

    grouped: dict[str, list[dict]] = {}
    for r in scoped:
        grouped.setdefault(str(r[_KEY[level]]), []).append(r)

    parent_key = _PARENT_KEY[level]
    parent_totals = group_by(scoped, parent_key) if parent_key else None
    account_total = sum(m.spend for m in group_by(scoped, _KEY[level]).values())

    out = []
    for erows in grouped.values():
        metrics = Metrics()
        for r in erows:
            metrics.add(r)
        identity = _row_identity(erows, level)

        if parent_key:
            parent_id = str(erows[0].get(parent_key) or "")
            parent = parent_totals.get(parent_id) if parent_totals else None
            share = metrics.spend / parent.spend if parent and parent.spend > 0 else None
            parent_label = "ad set" if level == "ad" else "campaign"
        else:
            share = metrics.spend / account_total if account_total > 0 else None
            parent_label = "account"

        meta = meta_by_creative.get(identity["creative_id"], {}) if level == "ad" else {}
        band = meta.get("aov_band")
        target = (targets_by_band or {}).get(band, {}) if band else {}

        row = {
            **identity,
            "level": level,
            "delivery_share": round(share, 4) if share is not None else None,
            "delivery_share_of": parent_label,
            "metrics": metrics.as_dict(),
            "category": meta.get("category"),
            "aov_band": band,
            "angle_id": meta.get("angle_id"),
            "target_roas": target.get("target_roas"),
            "target_cpa": target.get("target_cpa"),
            "flags": [],
        }
        row["roas_vs_target"] = (
            round(metrics.roas / target["target_roas"], 3)
            if target.get("target_roas") and metrics.roas is not None
            else None
        )
        out.append(row)

    # Delivery share is the column to sort by — that is where the money leak is.
    out.sort(key=lambda r: -(r["metrics"]["spend"] or 0))
    return out


def adset_delivery_bar(rows: Sequence[dict], adset_id: str, window: Window) -> list[dict]:
    """Per-ad delivery share within one ad set, for the horizontal bar.

    Each segment carries its own ROAS so the bar can be coloured by return:
    a wide segment in a bad colour is the picture of misallocation.
    """
    scoped = [
        r
        for r in rows
        if str(r.get("adset_id")) == adset_id and window.contains(parse_date(r["date"]))
    ]
    per_ad = group_by(scoped, "ad_id")
    total = sum(m.spend for m in per_ad.values())
    names = {str(r["ad_id"]): r for r in scoped}

    segments = []
    for ad_id, metrics in per_ad.items():
        latest = names[ad_id]
        segments.append(
            {
                "ad_id": ad_id,
                "ad_name": latest.get("ad_name") or ad_id,
                "creative_id": str(latest.get("creative_id") or ""),
                "thumbnail_url": latest.get("thumbnail_url"),
                "spend": round(metrics.spend, 2),
                "delivery_share": round(metrics.spend / total, 4) if total > 0 else 0.0,
                "roas": round(metrics.roas, 3) if metrics.roas is not None else None,
                "purchases": int(metrics.purchases),
            }
        )
    segments.sort(key=lambda s: -s["delivery_share"])
    return segments


def learning_threshold(rows: Sequence[dict], adset_id: str, as_of: date) -> dict:
    """Purchases in the trailing settled 7 days against the 50-event line."""
    window = trailing(settled_end(as_of), TRAILING_WINDOW_DAYS)
    scoped = [
        r
        for r in rows
        if str(r.get("adset_id")) == adset_id and window.contains(parse_date(r["date"]))
    ]
    events = sum(float(r.get("omni_purchase") or 0) for r in scoped)
    return {
        "events_7d": int(events),
        "threshold": LEARNING_THRESHOLD_EVENTS,
        "share": round(events / LEARNING_THRESHOLD_EVENTS, 3),
        "under_threshold": events < LEARNING_THRESHOLD_EVENTS,
        "window": window.as_dict(),
    }


def budget_pacing(
    rows: Sequence[dict], entity_rows: Sequence[dict], adset_id: str, as_of: date
) -> dict:
    """Actual daily spend against the set daily budget, last 7 settled days.

    `entity_rows` are `entity_daily` documents — the budget is a property of
    the ad set, not of any insight row, and it changes over time, so each day
    is paced against the budget that was actually set that day.
    """
    window = trailing(settled_end(as_of), TRAILING_WINDOW_DAYS)
    budget_by_day = {
        str(e["date"]): e.get("daily_budget")
        for e in entity_rows
        if str(e.get("entity_id")) == adset_id
    }
    per_day = group_by(
        [r for r in rows if str(r.get("adset_id")) == adset_id], "date", window
    )

    days = []
    total_spend = 0.0
    total_budget = 0.0
    for day in sorted(per_day):
        spend = per_day[day].spend
        budget = budget_by_day.get(day)
        total_spend += spend
        total_budget += budget or 0.0
        days.append(
            {
                "date": day,
                "spend": round(spend, 2),
                "daily_budget": budget,
                "pacing": round(spend / budget, 3) if budget else None,
            }
        )

    return {
        "days": days,
        "spend_7d": round(total_spend, 2),
        "budget_7d": round(total_budget, 2),
        "pacing": round(total_spend / total_budget, 3) if total_budget > 0 else None,
        "window": window.as_dict(),
    }


def within_adset_groups(
    rows: Sequence[dict], window: Window, meta_by_creative: dict[str, dict],
    targets_by_band: dict | None = None,
) -> list[dict]:
    """Leaderboard tab 2 — ads grouped by the budget they actually competed for.

    A global rank across ad sets compares creatives that never bid against each
    other. This is the tab where the real decisions live.
    """
    scoped = [r for r in rows if window.contains(parse_date(r["date"]))]
    by_adset: dict[str, list[dict]] = {}
    for r in scoped:
        by_adset.setdefault(str(r.get("adset_id")), []).append(r)

    groups = []
    for adset_id, arows in by_adset.items():
        latest = max(arows, key=lambda r: r["date"])
        per_creative = group_by(arows, "creative_id")
        total = sum(m.spend for m in per_creative.values())
        if len(per_creative) < 2:
            # One creative never competed with anything. Keep it, but say so —
            # it is a candidate for the "Proven Creative, One Ad Set" case.
            pass

        entries = []
        for creative_id, metrics in per_creative.items():
            crow = next(r for r in arows if str(r.get("creative_id")) == creative_id)
            meta = meta_by_creative.get(creative_id, {})
            band = meta.get("aov_band")
            target = (targets_by_band or {}).get(band, {}) if band else {}
            entries.append(
                {
                    "creative_id": creative_id,
                    "name": crow.get("ad_name") or creative_id,
                    "thumbnail_url": crow.get("thumbnail_url"),
                    "ad_ids": sorted({str(r["ad_id"]) for r in arows
                                      if str(r.get("creative_id")) == creative_id}),
                    "delivery_share": round(metrics.spend / total, 4) if total > 0 else None,
                    "metrics": metrics.as_dict(),
                    "category": meta.get("category"),
                    "aov_band": band,
                    "target_roas": target.get("target_roas"),
                    "status": classify(
                        StatusInput(
                            purchases=metrics.purchases,
                            effective_status=crow.get("effective_status") or "ACTIVE",
                            impressions_in_window=metrics.impressions,
                            lpv_transfer=metrics.lpv_transfer,
                            frequency=metrics.frequency,
                            roas_lifetime=metrics.roas,
                            target_roas=target.get("target_roas"),
                            cost_per_outbound_click=metrics.cost_per_outbound_click,
                        )
                    ).status,
                }
            )

        entries.sort(key=lambda e: -(e["metrics"]["roas"] or 0))
        for i, entry in enumerate(entries, start=1):
            entry["rank_in_adset"] = i

        best = entries[0] if entries else None
        misallocated = bool(
            best
            and best["delivery_share"] is not None
            and any(
                e["delivery_share"] is not None and e["delivery_share"] > best["delivery_share"]
                for e in entries[1:]
            )
        )
        gap = None
        if misallocated and best:
            widest = max(entries[1:], key=lambda e: e["delivery_share"] or 0)
            if best["delivery_share"]:
                gap = round((widest["delivery_share"] / best["delivery_share"] - 1) * 100, 1)

        groups.append(
            {
                "adset_id": adset_id,
                "adset_name": latest.get("adset_name") or adset_id,
                "campaign_id": str(latest.get("campaign_id") or ""),
                "campaign_name": latest.get("campaign_name") or "",
                "spend": round(total, 2),
                "creatives": entries,
                "misallocated": misallocated,
                "misallocation_gap_pct": gap,
            }
        )

    groups.sort(key=lambda g: (not g["misallocated"], -g["spend"]))
    return groups
