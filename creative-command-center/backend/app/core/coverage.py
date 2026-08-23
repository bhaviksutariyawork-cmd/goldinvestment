"""Coverage module — section 7. Answers "which category needs new creative".

Everything here reads `creative_meta`. Nothing in it can be derived from the
Marketing API: in the reference account 65% of spend sits on numeric-only ad
names like `112-4`. Until the tagging screen has been worked through, these
answers are partial, which is why `untagged_spend_share` travels with them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .constants import COVERAGE_CELL_IMPRESSIONS, HHI_CONCENTRATION
from .metrics import hhi


@dataclass
class CoverageRow:
    """One tagged creative's contribution to the grid."""

    creative_id: str
    category: str | None
    angle_id: str | None
    aov_band: str | None
    impressions: float = 0.0
    spend: float = 0.0
    revenue: float = 0.0
    revenue_recent: float = 0.0
    spend_recent: float = 0.0
    revenue_prior: float = 0.0
    spend_prior: float = 0.0


@dataclass
class Cell:
    category: str
    angle_id: str
    impressions: float = 0.0
    spend: float = 0.0
    revenue: float = 0.0
    creatives: int = 0

    @property
    def tested(self) -> bool:
        """Under the impression floor a cell has not been tested — it has been
        glanced at. Rendering it as "failed" is how live angles get written off."""
        return self.impressions >= COVERAGE_CELL_IMPRESSIONS

    @property
    def roas(self) -> float | None:
        return self.revenue / self.spend if self.spend > 0 else None

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "angle_id": self.angle_id,
            "impressions": int(self.impressions),
            "spend": round(self.spend, 2),
            "roas": round(self.roas, 3) if self.roas is not None else None,
            "creatives": self.creatives,
            "tested": self.tested,
            "state": "tested" if self.tested else ("partial" if self.impressions > 0 else "untested"),
        }


def coverage_matrix(rows: Iterable[CoverageRow]) -> dict:
    """category x angle grid, cell value = cumulative impressions."""
    cells: dict[tuple[str, str], Cell] = {}
    categories: set[str] = set()
    angles: set[str] = set()

    for row in rows:
        if not row.category or not row.angle_id:
            continue
        categories.add(row.category)
        angles.add(row.angle_id)
        key = (row.category, row.angle_id)
        cell = cells.setdefault(key, Cell(row.category, row.angle_id))
        cell.impressions += row.impressions
        cell.spend += row.spend
        cell.revenue += row.revenue
        cell.creatives += 1

    ordered_categories = sorted(categories)
    ordered_angles = sorted(angles)
    # Materialise every intersection: an absent cell is the most interesting
    # kind of gap, and it cannot be seen if the grid only draws what exists.
    grid = []
    for category in ordered_categories:
        for angle in ordered_angles:
            grid.append(cells.get((category, angle), Cell(category, angle)).as_dict())

    return {
        "categories": ordered_categories,
        "angles": ordered_angles,
        "cells": grid,
        "impression_floor": COVERAGE_CELL_IMPRESSIONS,
        "untested_cells": [c for c in grid if not c["tested"]],
    }


def coverage_gaps(rows: Iterable[CoverageRow]) -> list[dict]:
    """Cells under the impression floor, richest category first."""
    matrix = coverage_matrix(rows)
    gaps = [c for c in matrix["cells"] if not c["tested"]]
    category_spend: dict[str, float] = {}
    for row in rows:
        if row.category:
            category_spend[row.category] = category_spend.get(row.category, 0.0) + row.spend
    gaps.sort(key=lambda c: -category_spend.get(c["category"], 0.0))
    return gaps


def concentration(rows: Iterable[CoverageRow]) -> list[dict]:
    """HHI on spend share by angle, within each AOV band.

    Above 0.25 the band is one fatigue event away from a hole in the account.
    """
    bands: dict[str, dict[str, float]] = {}
    for row in rows:
        if not row.aov_band or not row.angle_id:
            continue
        bands.setdefault(row.aov_band, {})
        bands[row.aov_band][row.angle_id] = (
            bands[row.aov_band].get(row.angle_id, 0.0) + row.spend
        )

    out = []
    for band, angle_spend in sorted(bands.items()):
        total = sum(angle_spend.values())
        if total <= 0:
            continue
        shares = {a: s / total for a, s in angle_spend.items()}
        top_angle, top_share = max(shares.items(), key=lambda kv: kv[1])
        out.append(
            {
                "aov_band": band,
                "hhi": round(hhi(shares.values()), 4),
                "threshold": HHI_CONCENTRATION,
                "concentrated": hhi(shares.values()) > HHI_CONCENTRATION,
                "angles": len(shares),
                "spend": round(total, 2),
                "top_angle": top_angle,
                "top_share": round(top_share, 4),
                "shares": [
                    {"angle_id": a, "share": round(s, 4), "spend": round(angle_spend[a], 2)}
                    for a, s in sorted(shares.items(), key=lambda kv: -kv[1])
                ],
            }
        )
    return out


@dataclass
class PriorityEntry:
    category: str
    spend: float
    spend_share: float
    angles_total: int
    angles_untested: int
    untested_share: float
    roas_recent: float | None
    roas_prior: float | None
    trend: float | None
    trend_factor: float
    score: float
    untested_angles: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "spend": round(self.spend, 2),
            "spend_share": round(self.spend_share, 4),
            "angles_total": self.angles_total,
            "angles_untested": self.angles_untested,
            "untested_share": round(self.untested_share, 4),
            "roas_recent": round(self.roas_recent, 3) if self.roas_recent is not None else None,
            "roas_prior": round(self.roas_prior, 3) if self.roas_prior is not None else None,
            "trend": round(self.trend, 3) if self.trend is not None else None,
            "trend_factor": round(self.trend_factor, 3),
            "score": round(self.score, 5),
            "untested_angles": self.untested_angles,
        }


def _trend_factor(recent: float | None, prior: float | None) -> float:
    """Turn a ROAS trend into an urgency multiplier.

    A category whose ROAS is sliding is the one that needs new creative
    soonest, so a falling trend raises the score and a rising one lowers it.
    Clamped so a single wild ratio cannot dominate the queue.
    """
    if not recent or not prior or prior <= 0:
        return 1.0
    return max(0.25, min(2.0, 2.0 - (recent / prior)))


def testing_priority(rows: Sequence[CoverageRow], all_angles: Sequence[str] | None = None) -> list[dict]:
    """Rank categories by `spend share x untested angle share x ROAS trend`.

    Output is the brief list: what to commission next, in order.
    """
    rows = list(rows)
    angles = sorted({a for a in (all_angles or [r.angle_id for r in rows]) if a})
    if not angles:
        return []

    by_category: dict[str, list[CoverageRow]] = {}
    for row in rows:
        if row.category:
            by_category.setdefault(row.category, []).append(row)

    total_spend = sum(r.spend for r in rows) or 1.0
    entries: list[PriorityEntry] = []

    for category, crows in by_category.items():
        impressions_by_angle: dict[str, float] = dict.fromkeys(angles, 0.0)
        for r in crows:
            if r.angle_id in impressions_by_angle:
                impressions_by_angle[r.angle_id] += r.impressions

        untested = [a for a, imp in impressions_by_angle.items() if imp < COVERAGE_CELL_IMPRESSIONS]
        spend = sum(r.spend for r in crows)
        spend_recent = sum(r.spend_recent for r in crows)
        spend_prior = sum(r.spend_prior for r in crows)
        roas_recent = (
            sum(r.revenue_recent for r in crows) / spend_recent if spend_recent > 0 else None
        )
        roas_prior = sum(r.revenue_prior for r in crows) / spend_prior if spend_prior > 0 else None
        factor = _trend_factor(roas_recent, roas_prior)
        spend_share = spend / total_spend
        untested_share = len(untested) / len(angles)

        entries.append(
            PriorityEntry(
                category=category,
                spend=spend,
                spend_share=spend_share,
                angles_total=len(angles),
                angles_untested=len(untested),
                untested_share=untested_share,
                roas_recent=roas_recent,
                roas_prior=roas_prior,
                trend=(roas_recent / roas_prior) if roas_recent and roas_prior else None,
                trend_factor=factor,
                score=spend_share * untested_share * factor,
                untested_angles=sorted(untested),
            )
        )

    entries.sort(key=lambda e: -e.score)
    return [e.as_dict() for e in entries]


def untagged_spend_share(tagged_spend: float, untagged_spend: float) -> dict:
    """The figure that stays on the dashboard until it drops under 10%."""
    total = tagged_spend + untagged_spend
    share = untagged_spend / total if total > 0 else 0.0
    return {
        "untagged_spend": round(untagged_spend, 2),
        "tagged_spend": round(tagged_spend, 2),
        "total_spend": round(total, 2),
        "untagged_share": round(share, 4),
        "threshold": 0.10,
        "visible": share >= 0.10,
    }
