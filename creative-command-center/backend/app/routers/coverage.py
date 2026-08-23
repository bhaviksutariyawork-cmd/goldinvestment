"""Coverage module — the "what should I brief next" screen."""

from __future__ import annotations

from fastapi import APIRouter

from ..core.coverage import concentration, coverage_matrix, testing_priority
from ..deps import bundle_or_404
from ..service import coverage_rows, untagged

router = APIRouter(prefix="/api/coverage", tags=["coverage"])


@router.get("/{account_id}")
async def coverage(account_id: str, preset: str = "90d"):
    """Matrix, concentration and the testing priority queue in one payload.

    `untagged` travels with it deliberately: every answer below is only as
    complete as `creative_meta`, and a coverage grid built on 40% of spend is
    a grid with holes that are not really holes.
    """
    bundle = await bundle_or_404(account_id, preset)
    rows = coverage_rows(bundle)
    matrix = coverage_matrix(rows)
    return {
        "meta": bundle.as_meta(),
        "matrix": matrix,
        "concentration": concentration(rows),
        "priority_queue": testing_priority(rows, matrix["angles"]),
        "untagged": untagged(bundle),
        "formula": (
            "priority = category spend share x share of angles untested x ROAS trend factor. "
            "A falling trend raises the score: that is the category most in need of new creative."
        ),
    }
