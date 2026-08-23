"""Aggregate raw snapshot rows into metrics.

Two rules from section 0 are enforced here and nowhere else, so there is one
place to check them:

* Revenue is `purchase_roas x amount_spent`, row by row, then summed.
  `omni_purchase_values` is never read — it carries a confirmed 100x decimal
  error in the Marketing API.
* Ratios are computed at read time from stored raw values. We never store a
  ROAS and re-average it later; averaging ratios weights a 100-rupee day the
  same as a 100,000-rupee one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from .windows import Window, parse_date

# Raw counters that sum cleanly across days and across ads.
ADDITIVE_FIELDS = (
    "amount_spent",
    "impressions",
    "outbound_clicks",
    "omni_landing_page_view",
    "omni_add_to_cart",
    "omni_purchase",
)


def row_revenue(row: dict) -> float:
    """Data rule 1. The only place revenue is ever derived."""
    return float(row.get("purchase_roas") or 0.0) * float(row.get("amount_spent") or 0.0)


def _num(row: dict, key: str) -> float:
    value = row.get(key)
    return float(value) if value is not None else 0.0


@dataclass
class Metrics:
    """Raw sums plus ratios derived on demand.

    `reach_dedup` is Meta's deduplicated reach for the window. It cannot be
    derived from daily rows — Meta counts a returning user once, we would count
    them once per day — so the sync fetches it directly with a window-level
    Insights call and `core.build` attaches it here.

    Three bases, in descending order of trust:

    * `window` — Meta's deduplicated figure for exactly this window. Exact.
    * `summed_placements` — a creative running in several ad sets, where each
      ad's deduped reach is exact but the overlap between ad sets is unknown.
      Summing overstates reach, so frequency comes out low.
    * `daily_sum` — no window-level figure available. The sum of daily reach is
      an upper bound on true reach, so frequency is a *lower* bound.

    Both fallbacks under-report frequency, which is the safe direction: they
    can delay a fatigue flag, never invent one. `frequency_is_lower_bound`
    tells the UI to say so.
    """

    spend: float = 0.0
    impressions: float = 0.0
    outbound_clicks: float = 0.0
    landing_page_views: float = 0.0
    add_to_carts: float = 0.0
    purchases: float = 0.0
    revenue: float = 0.0
    reach_sum: float = 0.0
    reach_dedup: float | None = None
    reach_basis: str = "daily_sum"
    days: set[date] = field(default_factory=set)
    active_days: set[date] = field(default_factory=set)

    # ---- ratios, all computed at read time ----

    @property
    def roas(self) -> float | None:
        return self.revenue / self.spend if self.spend > 0 else None

    @property
    def cpa(self) -> float | None:
        return self.spend / self.purchases if self.purchases > 0 else None

    @property
    def cpm(self) -> float | None:
        return self.spend / self.impressions * 1000 if self.impressions > 0 else None

    @property
    def outbound_ctr(self) -> float | None:
        return self.outbound_clicks / self.impressions if self.impressions > 0 else None

    @property
    def cost_per_outbound_click(self) -> float | None:
        """The metric to reach for wherever CTR is tempting.

        Section 6: CTR is a diagnostic, never a kill trigger. The reference
        account's top three ROAS performers all sat below the 25th percentile
        for outbound CTR — low-CPM broad-reach creative where a low rate is
        selectivity, not weakness. Cost per click prices the same signal
        without punishing cheap impressions.
        """
        return self.spend / self.outbound_clicks if self.outbound_clicks > 0 else None

    @property
    def lpv_transfer(self) -> float | None:
        """Landing page views per outbound click. Below 0.60 you are paying
        for clicks that never land."""
        return (
            self.landing_page_views / self.outbound_clicks
            if self.outbound_clicks > 0
            else None
        )

    @property
    def atc_rate(self) -> float | None:
        return (
            self.add_to_carts / self.landing_page_views
            if self.landing_page_views > 0
            else None
        )

    @property
    def aov(self) -> float | None:
        return self.revenue / self.purchases if self.purchases > 0 else None

    @property
    def reach(self) -> float:
        return self.reach_dedup if self.reach_dedup is not None else self.reach_sum

    @property
    def frequency(self) -> float | None:
        return self.impressions / self.reach if self.reach > 0 else None

    @property
    def frequency_is_lower_bound(self) -> bool:
        return self.reach_basis != "window"

    def attach_reach(self, reach: float | None, basis: str) -> Metrics:
        """Attach Meta's deduplicated reach for this window."""
        if reach and reach > 0:
            self.reach_dedup = reach
            self.reach_basis = basis
        return self

    @property
    def days_live(self) -> int:
        return len(self.active_days)

    @property
    def first_active_day(self) -> date | None:
        return min(self.active_days) if self.active_days else None

    @property
    def last_active_day(self) -> date | None:
        return max(self.active_days) if self.active_days else None

    def add(self, row: dict) -> Metrics:
        self.spend += _num(row, "amount_spent")
        self.impressions += _num(row, "impressions")
        self.outbound_clicks += _num(row, "outbound_clicks")
        self.landing_page_views += _num(row, "omni_landing_page_view")
        self.add_to_carts += _num(row, "omni_add_to_cart")
        self.purchases += _num(row, "omni_purchase")
        self.revenue += row_revenue(row)
        self.reach_sum += _num(row, "reach")
        day = parse_date(row["date"])
        self.days.add(day)
        if _num(row, "impressions") > 0:
            self.active_days.add(day)
        return self

    def merge(self, other: Metrics) -> Metrics:
        self.spend += other.spend
        self.impressions += other.impressions
        self.outbound_clicks += other.outbound_clicks
        self.landing_page_views += other.landing_page_views
        self.add_to_carts += other.add_to_carts
        self.purchases += other.purchases
        self.revenue += other.revenue
        self.reach_sum += other.reach_sum
        self.days |= other.days
        self.active_days |= other.active_days
        return self

    def as_dict(self) -> dict:
        return {
            "spend": round(self.spend, 2),
            "impressions": int(self.impressions),
            "reach": int(self.reach),
            "outbound_clicks": int(self.outbound_clicks),
            "landing_page_views": int(self.landing_page_views),
            "add_to_carts": int(self.add_to_carts),
            "purchases": int(self.purchases),
            "revenue": round(self.revenue, 2),
            "roas": _round(self.roas, 4),
            "cpa": _round(self.cpa, 2),
            "cpm": _round(self.cpm, 2),
            "outbound_ctr": _round(self.outbound_ctr, 6),
            "cost_per_outbound_click": _round(self.cost_per_outbound_click, 2),
            "lpv_transfer": _round(self.lpv_transfer, 4),
            "atc_rate": _round(self.atc_rate, 4),
            "aov": _round(self.aov, 2),
            "frequency": _round(self.frequency, 3),
            "frequency_is_lower_bound": self.frequency_is_lower_bound,
            "reach_basis": self.reach_basis,
            "days_live": self.days_live,
        }


def _round(value: float | None, places: int) -> float | None:
    return round(value, places) if value is not None else None


def aggregate(rows: Iterable[dict], window: Window | None = None) -> Metrics:
    """Sum rows, optionally clipped to a window."""
    totals = Metrics()
    for row in rows:
        if window is not None and not window.contains(parse_date(row["date"])):
            continue
        totals.add(row)
    return totals


def group_by(rows: Iterable[dict], key: str, window: Window | None = None) -> dict[str, Metrics]:
    """Aggregate into buckets.

    Data rule 3: the Leaderboard calls this with `creative_id`, so an asset
    running in three ad sets lands in one bucket instead of fragmenting into
    three ranks. The Hierarchy Explorer calls it with `ad_id`, where the three
    rows are the point.
    """
    out: dict[str, Metrics] = {}
    for row in rows:
        if window is not None and not window.contains(parse_date(row["date"])):
            continue
        bucket = row.get(key)
        if bucket is None:
            continue
        out.setdefault(str(bucket), Metrics()).add(row)
    return out


def daily_series(rows: Iterable[dict], window: Window | None = None) -> list[dict]:
    """One point per date, for trend charts. Sorted ascending."""
    per_day = group_by(rows, "date", window)
    series = []
    for day in sorted(per_day):
        metrics = per_day[day].as_dict()
        metrics["date"] = day
        series.append(metrics)
    return series


def first_days_window(rows: Sequence[dict], days: int = 7) -> Window | None:
    """The creative's first N *delivering* days — the baseline CTR decay and
    CPM inflation are measured against."""
    active = sorted({parse_date(r["date"]) for r in rows if _num(r, "impressions") > 0})
    if not active:
        return None
    span = active[: days]
    return Window(span[0], span[-1])


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile. `p` in 0..1."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = p * (len(clean) - 1)
    low = int(position)
    high = min(low + 1, len(clean) - 1)
    weight = position - low
    return clean[low] * (1 - weight) + clean[high] * weight


def median(values: Sequence[float]) -> float | None:
    return percentile(values, 0.5)


def hhi(shares: Iterable[float]) -> float:
    """Herfindahl-Hirschman index over shares that already sum to 1."""
    return sum(s * s for s in shares)
