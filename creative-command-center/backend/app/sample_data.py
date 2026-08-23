"""A synthetic but realistic account, so the app can be driven end to end
before a real access token exists.

Shaped after the reference account in the brief: numeric-only ad names on most
of the spend, two AOV bands with very different economics, an ad set funding
the wrong creative, a landing page that leaks, and enough untagged spend that
the dashboard opens with real work on it.

Purchases are derived from spend, ROAS and the band's AOV rather than set
independently — that is the relationship the 30-purchase gate exists to
respect, and hand-picked numbers would quietly break it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

CLIENT = "Aurelia Jewels"
ACCOUNT = "act_1029384756"

# The two AOV bands from the brief's own example: the same 2,400 rupees buys
# four purchases on one and one purchase on the other.
BAND_AOV = {"low": 565.0, "high": 2800.0}
DEFAULT_AOV = 900.0

FORMATS = ["static", "reel", "carousel", "ugc-video"]
HOOKS = ["question", "problem", "demo", "testimonial"]

CAMPAIGNS = [
    ("23851", "Rings — Prospecting"),
    ("23852", "Festive — Broad"),
    ("23853", "Bangles + Necklaces — Retarget"),
]
ADSETS = [
    ("60011", "Rings | 25-44 | Broad"),
    ("60012", "Earrings | Lookalike 3%"),
    ("60013", "Festive | Interest stack"),
    ("60014", "Festive | Broad"),
    ("60015", "Retarget | 30d ATC"),
    ("60016", "Bangles | Broad"),
]


@dataclass(frozen=True)
class Spec:
    creative_id: str
    ad_name: str
    campaign_ix: int
    adset_ix: int
    daily_spend: float
    roas: float
    frequency: float          # steady-state frequency this creative runs at
    transfer: float           # landing page views per outbound click
    ctr: float                # outbound CTR
    category: str | None
    angle: str | None
    band: str | None
    decay: float = 0.05       # how much ROAS falls across the period
    live_days: int | None = None  # None = live for the whole period
    status: str = "ACTIVE"
    objective: str = "OUTCOME_SALES"

    @property
    def aov(self) -> float:
        return BAND_AOV.get(self.band or "", DEFAULT_AOV)

    @property
    def tagged(self) -> bool:
        return bool(self.category and self.band)


SPECS = [
    # Ad set 60011 funds the wrong creative: the better ROAS gets a fifth of
    # the budget. Five of twenty ad sets looked like this in the reference
    # account, with gaps up to 194%.
    Spec("cr_scale_01", "112-4", 0, 0, 500, 3.10, 1.5, 0.88, 0.009, "Rings", "gifting", "low"),
    Spec("cr_hog_02", "112-9", 0, 0, 2000, 1.05, 1.9, 0.90, 0.006, "Rings", "price-anchor", "low"),
    # High ROAS, broken landing page. LEAKING must beat WIN.
    Spec("cr_leak_03", "108-2", 0, 1, 620, 3.30, 1.4, 0.33, 0.011, "Earrings", "social-proof", "low"),
    # Burnt out: severe frequency and a ROAS that has halved since launch.
    Spec("cr_fatigue_04", "110-8", 1, 2, 900, 1.40, 4.4, 0.86, 0.007, "Necklaces", "heritage", "low", decay=0.50),
    # One creative, three ad sets: one Leaderboard row, three Hierarchy rows.
    Spec("cr_multi_05", "Diwali Hero v3", 1, 2, 300, 2.35, 1.3, 0.89, 0.010, "Necklaces", "gifting", "low"),
    Spec("cr_multi_05", "Diwali Hero v3", 1, 3, 340, 2.20, 1.2, 0.90, 0.010, "Necklaces", "gifting", "low"),
    Spec("cr_multi_05", "Diwali Hero v3", 2, 4, 280, 2.45, 1.1, 0.91, 0.010, "Necklaces", "gifting", "low"),
    # Confirmed unprofitable at an adequate sample.
    Spec("cr_cut_06", "104-1", 2, 4, 1100, 0.55, 1.7, 0.87, 0.008, "Bangles", "self-purchase", "low"),
    # Clicks well above the account median, sells nothing.
    Spec("cr_hook_07", "115-3", 2, 5, 900, 0.72, 1.6, 0.84, 0.021, "Bangles", "price-anchor", "low"),
    # Untagged and expensive — why the tagging screen exists.
    Spec("cr_untag_08", "119-2", 1, 3, 810, 1.85, 1.5, 0.88, 0.008, None, None, None),
    Spec("cr_untag_09", "121-7", 0, 1, 700, 1.62, 1.4, 0.85, 0.007, None, None, None),
    # Launched five days ago: flattering ROAS on a sample that cannot carry it.
    Spec("cr_thin_10", "126-5", 2, 5, 90, 1.66, 1.1, 0.86, 0.009, "Rings", "self-purchase", "low", live_days=5),
    # Cheap clicks, no sample yet. A candidate, not a loser.
    Spec("cr_test_11", "127-1", 1, 2, 70, 0.90, 1.05, 0.90, 0.014, "Earrings", "heritage", "low", live_days=4),
    # High band: well over target with audience left. The scale candidate.
    Spec("cr_win_12", "131-6", 1, 3, 800, 4.20, 1.3, 0.92, 0.012, "Necklaces", "social-proof", "high"),
    # High band: under target but not by enough to kill, and warming up.
    Spec("cr_hold_13", "134-2", 2, 5, 2400, 2.05, 2.2, 0.89, 0.008, "Bangles", "gifting", "high"),
]


# Real thumbnails come from the Marketing API and are cached per creative_id.
# The demo account has no API behind it, so it draws its own — inline, so the
# screens look right with no network at all.
_SWATCHES = ["#3987e5", "#d95926", "#199e70", "#9085e9", "#d55181", "#c98500"]


def _placeholder_thumbnail(spec: Spec) -> str:
    import base64

    swatch = _SWATCHES[sum(ord(c) for c in spec.creative_id) % len(_SWATCHES)]
    initials = spec.ad_name[:4]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160">'
        f'<rect width="160" height="160" fill="{swatch}" opacity="0.85"/>'
        '<text x="80" y="94" font-family="system-ui,sans-serif" font-size="38" '
        f'fill="#0d0d0d" text-anchor="middle">{initials}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _row(day: date, spec: Spec, values: dict) -> dict:
    campaign_id, campaign_name = CAMPAIGNS[spec.campaign_ix]
    adset_id, adset_name = ADSETS[spec.adset_ix]
    impressions = values["impressions"]
    spend = values["spend"]
    return {
        "account_id": values["account_id"],
        "date": day.isoformat(),
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "adset_id": adset_id,
        "adset_name": adset_name,
        "ad_id": f"ad_{spec.creative_id}_{adset_id}",
        "ad_name": spec.ad_name,
        "creative_id": spec.creative_id,
        "thumbnail_url": _placeholder_thumbnail(spec),
        "effective_status": spec.status,
        "objective": spec.objective,
        "amount_spent": round(spend, 2),
        "impressions": impressions,
        "reach": values["reach"],
        "frequency": round(impressions / values["reach"], 3) if values["reach"] else 0,
        "cpm": round(spend / impressions * 1000, 2) if impressions else 0,
        "ctr": round(values["clicks"] / impressions, 5) if impressions else 0,
        "outbound_clicks": values["clicks"],
        "outbound_clicks_ctr": round(values["clicks"] / impressions, 5) if impressions else 0,
        "omni_landing_page_view": values["lpv"],
        "omni_add_to_cart": values["atc"],
        "omni_purchase": values["purchases"],
        # Stored raw. Revenue is this times spend, computed at read time.
        "purchase_roas": round(values["roas"], 4),
        "created_time": (day - timedelta(days=45)).isoformat(),
        "is_current": True,
        "revision": 1,
    }


def generate(days: int = 90, as_of: date | None = None, seed: int = 7,
             account_id: str = "seed") -> list[dict]:
    """Daily snapshot rows for the whole account. Deterministic for a seed."""
    rng = random.Random(seed)
    as_of = as_of or date.today()
    rows: list[dict] = []
    # A high-AOV creative earns well under one purchase a day. Rounding each
    # day independently would floor every one of them to zero and erase the
    # sample the 30-purchase gate is supposed to measure, so the fraction is
    # carried forward instead.
    carry: dict[str, float] = {}

    for offset in range(days):
        day = as_of - timedelta(days=days - 1 - offset)
        lag = (as_of - day).days
        age = offset / max(days - 1, 1)
        # Data rule 4 made visible in the data: the trailing 3 days come in
        # short and get revised up. Anything that reads them as final will see
        # a cliff that is not there.
        completeness = [0.45, 0.72, 0.90][lag] if lag < 3 else 1.0

        for spec in SPECS:
            if spec.live_days is not None and lag >= spec.live_days:
                continue

            spend = spec.daily_spend * rng.uniform(0.88, 1.12)
            decayed = spec.roas * (1 - spec.decay * age)
            roas = decayed * rng.uniform(0.92, 1.08) * completeness
            revenue = spend * roas
            key = f"{spec.creative_id}:{spec.adset_ix}"
            owed = carry.get(key, 0.0) + revenue / spec.aov
            purchases = int(owed)
            carry[key] = owed - purchases

            cpm = rng.uniform(240, 360)
            impressions = max(200, int(spend / cpm * 1000))
            # Frequency ramps from ~1 at launch to its steady state as the
            # audience saturates. Reach can never exceed impressions.
            frequency = 1.0 + (spec.frequency - 1.0) * (0.45 + 0.55 * age)
            reach = max(50, int(impressions / max(frequency, 1.0)))
            clicks = max(1, int(impressions * spec.ctr * rng.uniform(0.9, 1.1)))
            lpv = int(clicks * spec.transfer)
            atc = int(lpv * rng.uniform(0.12, 0.25))

            rows.append(
                _row(
                    day,
                    spec,
                    {
                        "account_id": account_id,
                        "spend": spend,
                        "impressions": impressions,
                        "reach": reach,
                        "clicks": clicks,
                        "lpv": lpv,
                        "atc": atc,
                        "purchases": purchases,
                        "roas": roas,
                    },
                )
            )
    return rows


def entity_rows(days: int = 90, as_of: date | None = None,
                account_id: str = "seed") -> list[dict]:
    """`entity_daily` for ad sets and campaigns.

    Ad-set rows carry the budgets that budget pacing paces against. Campaign
    rows carry the same measures as the ad-level pull, so the reconcile check
    has a second, independently-grouped total to compare against — which is
    the whole point of it.
    """
    as_of = as_of or date.today()
    # Sized just above actual delivery, except 60012, which cannot spend what
    # it was given — that is the Budget Underspend flag with something to say.
    budgets = {"60011": 2800.0, "60012": 2400.0, "60013": 1500.0,
               "60014": 2200.0, "60015": 1600.0, "60016": 4000.0}
    snapshots = generate(days, as_of=as_of, account_id=account_id)

    SUMMED = ("amount_spent", "impressions", "reach", "outbound_clicks",
              "omni_landing_page_view", "omni_add_to_cart", "omni_purchase")
    totals: dict[tuple[str, str, str], dict] = {}

    for row in snapshots:
        for entity_type, entity_id, name in (
            ("adset", row["adset_id"], row["adset_name"]),
            ("campaign", row["campaign_id"], row["campaign_name"]),
        ):
            key = (entity_type, entity_id, row["date"])
            entry = totals.get(key)
            if entry is None:
                entry = totals[key] = {
                    "account_id": account_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "name": name,
                    "date": row["date"],
                    "campaign_id": row["campaign_id"],
                    "campaign_name": row["campaign_name"],
                    "objective": row["objective"],
                    "daily_budget": budgets.get(entity_id),
                    "lifetime_budget": None,
                    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                    "optimization_goal": "OFFSITE_CONVERSIONS",
                    "delivery_status": "ACTIVE",
                    "is_current": True,
                    "revision": 1,
                    "_revenue": 0.0,
                    **dict.fromkeys(SUMMED, 0.0),
                }
            for field in SUMMED:
                entry[field] += row[field]
            entry["_revenue"] += row["amount_spent"] * row["purchase_roas"]

    rows = []
    for entry in totals.values():
        revenue = entry.pop("_revenue")
        spend = entry["amount_spent"]
        # The ratio is derived from the sums, never by averaging daily ratios —
        # and it is stored raw, exactly as the API would report it.
        entry["purchase_roas"] = round(revenue / spend, 4) if spend else 0.0
        entry["frequency"] = (
            round(entry["impressions"] / entry["reach"], 3) if entry["reach"] else 0.0
        )
        rows.append(entry)
    return rows


def creative_meta_docs(account_id: str) -> list[dict]:
    """Tags for the creatives that have them. The untagged ones stay untagged
    on purpose."""
    rng = random.Random(11)
    docs = []
    seen: set[str] = set()
    for spec in SPECS:
        if spec.creative_id in seen or not spec.tagged:
            continue
        seen.add(spec.creative_id)
        docs.append(
            {
                "account_id": account_id,
                "creative_id": spec.creative_id,
                "category": spec.category,
                "aov_band": spec.band,
                "angle_id": spec.angle,
                "format": rng.choice(FORMATS),
                "hook_type": rng.choice(HOOKS),
                "offer_type": rng.choice(["none", "10-off", "free-shipping"]),
                "lp_type": rng.choice(["pdp", "collection", "advertorial"]),
                "notes": None,
            }
        )
    return docs


def target_docs(account_id: str) -> list[dict]:
    """Two bands, two targets. The low band buys far more purchases per rupee
    and carries the lower ROAS target — one client-level number would mislabel
    both."""
    return [
        {"account_id": account_id, "aov_band": "low", "target_roas": 1.8,
         "target_cpa": 380.0, "aov_min": 0, "aov_max": 1200},
        {"account_id": account_id, "aov_band": "high", "target_roas": 2.4,
         "target_cpa": 1150.0, "aov_min": 1200, "aov_max": None},
    ]


def reach_window_docs(as_of: date | None = None, days: int = 90,
                      account_id: str = "seed") -> list[dict]:
    """Deduplicated reach per named window, the way the sync stores it.

    Derived from the same frequency curve the daily rows are built on, so the
    demo data behaves like the real thing: window frequency climbs to the
    creative's steady state, while a naive sum of daily reach would sit near 1.
    """
    from .core.reach import named_windows

    as_of = as_of or date.today()
    windows = named_windows(as_of, first_date=as_of - timedelta(days=days - 1))
    rows = generate(days, as_of=as_of, account_id=account_id)
    by_ad_spec = {f"ad_{s.creative_id}_{ADSETS[s.adset_ix][0]}": s for s in SPECS}

    docs = []
    for window_key, window in windows.items():
        totals: dict[str, dict] = {}
        for row in rows:
            day = date.fromisoformat(row["date"])
            if not (window.start <= day <= window.end):
                continue
            entry = totals.setdefault(
                row["ad_id"],
                {"creative_id": row["creative_id"], "impressions": 0.0, "ages": []},
            )
            entry["impressions"] += row["impressions"]
            entry["ages"].append((day - (as_of - timedelta(days=days - 1))).days / max(days - 1, 1))

        for ad_id, entry in totals.items():
            spec = by_ad_spec.get(ad_id)
            if not spec or not entry["ages"]:
                continue
            mean_age = sum(entry["ages"]) / len(entry["ages"])
            frequency = 1.0 + (spec.frequency - 1.0) * (0.45 + 0.55 * mean_age)
            docs.append(
                {
                    "account_id": account_id,
                    "ad_id": ad_id,
                    "creative_id": entry["creative_id"],
                    "window_key": window_key,
                    "reach": round(entry["impressions"] / max(frequency, 1.0)),
                    "impressions": entry["impressions"],
                    "frequency": round(frequency, 3),
                    "as_of": as_of.isoformat(),
                    "window_start": window.start.isoformat(),
                    "window_end": window.end.isoformat(),
                    "is_current": True,
                }
            )
    return docs
