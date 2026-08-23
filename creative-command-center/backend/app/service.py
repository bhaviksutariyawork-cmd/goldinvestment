"""Assembly layer: rows out of Mongo, views and flags into the routers.

Snapshot rows are cached in-process against the account's `last_sync_at`. A
sync writes a new timestamp, which invalidates the cache; between syncs the
numbers cannot change, so re-reading 90 days of rows on every keystroke in a
filter would be pure waste.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import repo
from .core import coverage as coverage_core
from .core.build import (
    AccountBenchmarks,
    CreativeView,
    benchmarks,
    build_views,
    is_conversion_objective,
    to_flag_context,
)
from .core.constants import COVERAGE_CELL_IMPRESSIONS, TRAILING_WINDOW_DAYS
from .core.flags import (
    AccountContext,
    AdsetContext,
    Flag,
    evaluate_account,
    evaluate_adset,
    evaluate_creative,
    rank_flags,
)
from .core.metrics import group_by
from .core.reach import index as reach_index
from .core.windows import Window, parse_date, resolve_range, settled_end, settling_window, trailing

_CACHE: dict[str, tuple[object, list[dict], list[dict]]] = {}


@dataclass
class Bundle:
    account: dict
    account_id: str
    rows: list[dict]
    entity_rows: list[dict]
    meta: dict[str, dict]
    targets: list[dict]
    targets_by_band: dict[str, dict]
    as_of: date
    window: Window
    views: list[CreativeView] = field(default_factory=list)
    marks: AccountBenchmarks = field(default_factory=AccountBenchmarks)

    @property
    def client_name(self) -> str:
        return self.account.get("client_name", "")

    @property
    def settling(self) -> dict:
        """The days every chart must mark "settling" rather than plot as fact."""
        return settling_window(self.as_of).as_dict()

    def as_meta(self) -> dict:
        return {
            "account_id": self.account_id,
            "client_name": self.client_name,
            "currency": self.account.get("currency", "INR"),
            "as_of": self.as_of.isoformat(),
            "settled_through": settled_end(self.as_of).isoformat(),
            "settling_window": self.settling,
            "window": self.window.as_dict(),
            "benchmarks": self.marks.as_dict(),
            "targets": [
                {k: v for k, v in t.items() if k not in {"_id"}} for t in self.targets
            ],
        }


async def _cached_rows(db, account: dict) -> tuple[list[dict], list[dict]]:
    account_id = str(account["_id"])
    stamp = account.get("last_sync_at")
    cached = _CACHE.get(account_id)
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    rows = await repo.load_snapshots(db, account_id)
    entity_rows = await repo.load_entity_daily(db, account_id)
    _CACHE[account_id] = (stamp, rows, entity_rows)
    return rows, entity_rows


def invalidate(account_id: str | None = None) -> None:
    if account_id:
        _CACHE.pop(account_id, None)
    else:
        _CACHE.clear()


async def load_bundle(
    db,
    account_id: str,
    *,
    preset: str | None = "30d",
    start: str | None = None,
    end: str | None = None,
    with_views: bool = True,
) -> Bundle | None:
    account = await repo.get_account(db, account_id)
    if not account:
        return None

    rows, entity_rows = await _cached_rows(db, account)
    meta = await repo.creative_meta(db, account_id)
    targets = await repo.targets(db, account_id)
    as_of = max((parse_date(r["date"]) for r in rows), default=date.today())
    window = resolve_range(as_of, preset, start, end)

    bundle = Bundle(
        account=account,
        account_id=account_id,
        rows=rows,
        entity_rows=entity_rows,
        meta=meta,
        targets=targets,
        targets_by_band={t["aov_band"]: t for t in targets},
        as_of=as_of,
        window=window,
    )

    if with_views and rows:
        pauses = await repo.manual_pauses(db, account_id)
        bundle.views = build_views(
            rows,
            window=window,
            as_of=as_of,
            meta_by_creative=meta,
            targets=targets,
            account=account,
            manual_pauses=pauses,
            reach_index=reach_index(await repo.load_reach_windows(db, account_id)),
        )
        bundle.marks = benchmarks(bundle.views)
    return bundle


# --- flags ------------------------------------------------------------------


def coverage_rows(bundle: Bundle) -> list[coverage_core.CoverageRow]:
    """One row per tagged creative, with recent and prior halves of the window
    so the testing queue can read a ROAS trend."""
    edge = settled_end(bundle.as_of)
    recent = trailing(edge, TRAILING_WINDOW_DAYS)
    prior = trailing(edge - timedelta(days=TRAILING_WINDOW_DAYS), TRAILING_WINDOW_DAYS)

    out = []
    for view in bundle.views:
        recent_metrics = _slice(bundle.rows, view.creative_id, recent)
        prior_metrics = _slice(bundle.rows, view.creative_id, prior)
        out.append(
            coverage_core.CoverageRow(
                creative_id=view.creative_id,
                category=view.meta.get("category"),
                angle_id=view.meta.get("angle_id"),
                aov_band=view.meta.get("aov_band"),
                impressions=view.lifetime.impressions,
                spend=view.lifetime.spend,
                revenue=view.lifetime.revenue,
                spend_recent=recent_metrics[0],
                revenue_recent=recent_metrics[1],
                spend_prior=prior_metrics[0],
                revenue_prior=prior_metrics[1],
            )
        )
    return out


def _slice(rows, creative_id: str, window: Window) -> tuple[float, float]:
    grouped = group_by(
        [r for r in rows if str(r["creative_id"]) == creative_id], "creative_id", window
    )
    metrics = grouped.get(creative_id)
    return (metrics.spend, metrics.revenue) if metrics else (0.0, 0.0)


def untagged(bundle: Bundle) -> dict:
    tagged = sum(
        v.window.spend for v in bundle.views if v.meta.get("category") and v.meta.get("aov_band")
    )
    untagged_spend = sum(
        v.window.spend
        for v in bundle.views
        if not (v.meta.get("category") and v.meta.get("aov_band"))
    )
    return coverage_core.untagged_spend_share(tagged, untagged_spend)


def adset_contexts(bundle: Bundle) -> list[AdsetContext]:
    edge = settled_end(bundle.as_of)
    window = trailing(edge, TRAILING_WINDOW_DAYS)
    per_adset = group_by(bundle.rows, "adset_id", window)

    budgets: dict[str, dict] = {}
    for row in bundle.entity_rows:
        if row.get("entity_type") != "adset":
            continue
        current = budgets.get(row["entity_id"])
        if not current or row["date"] > current["date"]:
            budgets[row["entity_id"]] = row

    names = {}
    for row in bundle.rows:
        names.setdefault(
            str(row.get("adset_id")),
            {
                "adset_name": row.get("adset_name") or str(row.get("adset_id")),
                "campaign_id": str(row.get("campaign_id") or ""),
                "campaign_name": row.get("campaign_name") or "",
            },
        )

    out = []
    for adset_id, metrics in per_adset.items():
        entity = budgets.get(adset_id, {})
        label = names.get(adset_id, {})
        out.append(
            AdsetContext(
                account_id=bundle.account_id,
                client_name=bundle.client_name,
                adset_id=adset_id,
                adset_name=label.get("adset_name", adset_id),
                campaign_id=label.get("campaign_id", ""),
                campaign_name=label.get("campaign_name", ""),
                daily_budget=entity.get("daily_budget"),
                lifetime_budget=entity.get("lifetime_budget"),
                spend_7d=metrics.spend,
                purchases_7d=metrics.purchases,
                delivery_status=entity.get("delivery_status", "ACTIVE"),
            )
        )
    return out


def account_context(bundle: Bundle) -> AccountContext:
    rows = coverage_rows(bundle)
    gaps = [
        cell
        for cell in coverage_core.coverage_gaps(rows)
        if cell["impressions"] < COVERAGE_CELL_IMPRESSIONS
    ]

    category_campaigns: dict[str, set[str]] = {}
    category_spend: dict[str, float] = {}
    for view in bundle.views:
        category = view.meta.get("category")
        if not category:
            continue
        category_spend[category] = category_spend.get(category, 0.0) + view.window.spend
        if str(view.effective_status).upper() != "ACTIVE":
            continue
        for placement in view.placements:
            if placement.campaign_name:
                category_campaigns.setdefault(category, set()).add(placement.campaign_name)

    last_sync = bundle.account.get("last_sync_at")
    hours = None
    if isinstance(last_sync, datetime):
        reference = datetime.now(last_sync.tzinfo) if last_sync.tzinfo else datetime.now()
        hours = (reference - last_sync).total_seconds() / 3600

    return AccountContext(
        account_id=bundle.account_id,
        client_name=bundle.client_name,
        hours_since_sync=hours,
        last_sync_status=bundle.account.get("last_sync_status") or "never",
        last_sync_error=bundle.account.get("last_sync_error"),
        untagged_spend_share=untagged(bundle)["untagged_share"],
        active_campaigns_by_category={k: sorted(v) for k, v in category_campaigns.items()},
        category_spend=category_spend,
        concentration=coverage_core.concentration(rows),
        coverage_gaps=gaps,
    )


def build_flags(bundle: Bundle, snoozed: set[str] | None = None) -> list[Flag]:
    snoozed = snoozed or set()
    flags: list[Flag] = []
    for view in bundle.views:
        flags.extend(evaluate_creative(to_flag_context(view, bundle.marks, bundle.account)))
    for adset in adset_contexts(bundle):
        flags.extend(evaluate_adset(adset))
    flags.extend(evaluate_account(account_context(bundle)))
    return rank_flags([f for f in flags if f.dedupe_key not in snoozed])


def conversion_account(bundle: Bundle) -> bool:
    """An account is a conversion account if most of its spend chases conversions."""
    total = sum(v.window.spend for v in bundle.views) or 1.0
    conversion = sum(
        v.window.spend for v in bundle.views if is_conversion_objective(v.objective)
    )
    return conversion / total >= 0.5
