"""Deduplicated reach, which daily rows cannot give you.

Meta counts a person once inside a window no matter how many days they saw the
ad; a sum of daily reach counts them once per day. So a frequency built from
daily rows is not a frequency — it is roughly the average *daily* frequency,
which sits near 1.0 and would leave every fatigue and saturation threshold in
section 5 permanently unreachable.

The fix is to ask Meta directly: the sync makes a handful of extra ad-level
Insights calls with no `time_increment`, one per named window, and stores the
deduplicated reach it returns. That is an addition to the schema in section 1,
and a deliberate one — without it the frequency column is decorative.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .constants import SETTLING_DAYS, TRAILING_WINDOW_DAYS
from .windows import Window, settled_end, trailing

# The windows worth paying an API call for. Everything the flag engine needs
# is here; any other user-selected range falls back to the daily-sum bound.
WINDOW_KEYS = ("trailing_7", "prior_7", "trailing_30", "lifetime")


def named_windows(as_of: date, first_date: date | None = None) -> dict[str, Window]:
    edge = settled_end(as_of, SETTLING_DAYS)
    return {
        "trailing_7": trailing(edge, TRAILING_WINDOW_DAYS),
        "prior_7": trailing(edge - timedelta(days=TRAILING_WINDOW_DAYS), TRAILING_WINDOW_DAYS),
        "trailing_30": trailing(edge, 30),
        "lifetime": Window(first_date or (edge - timedelta(days=365)), edge),
    }


def key_for(window: Window, as_of: date) -> str | None:
    """Which stored window, if any, matches the range the user selected."""
    edge = settled_end(as_of, SETTLING_DAYS)
    if window.end != edge:
        return None
    return {7: "trailing_7", 30: "trailing_30"}.get(window.days)


@dataclass(frozen=True)
class ReachKey:
    creative_id: str
    window_key: str


def index(docs: Iterable[dict]) -> dict[ReachKey, dict]:
    """Roll ad-level deduplicated reach up to creative level.

    For a creative living in one ad set this is exact. Across several ad sets
    the overlap between them is unknown and unknowable from the API, so the
    sum overstates reach and understates frequency — flagged as
    `summed_placements` so the UI can say the number is a floor.
    """
    rolled: dict[ReachKey, dict] = {}
    for doc in docs:
        key = ReachKey(str(doc["creative_id"]), str(doc["window_key"]))
        entry = rolled.setdefault(key, {"reach": 0.0, "placements": 0})
        entry["reach"] += float(doc.get("reach") or 0)
        entry["placements"] += 1
    return rolled


def lookup(
    reach_index: dict[ReachKey, dict], creative_id: str, window_key: str | None
) -> tuple[float | None, str]:
    if not window_key:
        return None, "daily_sum"
    entry = reach_index.get(ReachKey(creative_id, window_key))
    if not entry or entry["reach"] <= 0:
        return None, "daily_sum"
    return entry["reach"], "window" if entry["placements"] == 1 else "summed_placements"


def ad_level_docs(
    rows: Sequence[dict], account_id: str, window_key: str, ad_index: dict[str, dict]
) -> list[dict]:
    """Window-level insights rows -> reach documents."""
    docs = []
    for row in rows:
        ad_id = str(row.get("ad_id") or "")
        ad = ad_index.get(ad_id)
        if not ad or not ad.get("creative_id"):
            continue
        docs.append(
            {
                "account_id": account_id,
                "ad_id": ad_id,
                "creative_id": str(ad["creative_id"]),
                "window_key": window_key,
                "reach": float(row.get("reach") or 0),
                "impressions": float(row.get("impressions") or 0),
                "frequency": float(row.get("frequency") or 0),
            }
        )
    return docs
