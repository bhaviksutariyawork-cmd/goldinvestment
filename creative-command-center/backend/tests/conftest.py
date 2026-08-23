from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.reach import index as reach_index  # noqa: E402
from app.sample_data import (  # noqa: E402
    creative_meta_docs,
    generate,
    reach_window_docs,
    target_docs,
)

AS_OF = date(2026, 8, 23)


def row(
    day: date,
    *,
    creative_id: str = "cr1",
    ad_id: str = "ad1",
    adset_id: str = "as1",
    campaign_id: str = "cmp1",
    spend: float = 100.0,
    impressions: float = 10_000,
    reach: float = 8_000,
    clicks: float = 100,
    lpv: float = 90,
    atc: float = 20,
    purchases: float = 5,
    roas: float = 2.0,
    status: str = "ACTIVE",
    objective: str = "OUTCOME_SALES",
) -> dict:
    """A single snapshot row, with only the fields the engine reads."""
    return {
        "account_id": "acct",
        "date": day.isoformat(),
        "campaign_id": campaign_id,
        "campaign_name": f"Campaign {campaign_id}",
        "adset_id": adset_id,
        "adset_name": f"Ad set {adset_id}",
        "ad_id": ad_id,
        "ad_name": ad_id,
        "creative_id": creative_id,
        "effective_status": status,
        "objective": objective,
        "amount_spent": spend,
        "impressions": impressions,
        "reach": reach,
        "outbound_clicks": clicks,
        "omni_landing_page_view": lpv,
        "omni_add_to_cart": atc,
        "omni_purchase": purchases,
        "purchase_roas": roas,
    }


def series(days: int, end: date = AS_OF, **kwargs) -> list[dict]:
    """`days` identical rows ending on `end`."""
    return [row(end - timedelta(days=offset), **kwargs) for offset in range(days)]


@pytest.fixture
def as_of() -> date:
    return AS_OF


@pytest.fixture
def sample_rows() -> list[dict]:
    return generate(90, as_of=AS_OF, account_id="acct")


@pytest.fixture
def sample_meta() -> dict[str, dict]:
    return {d["creative_id"]: d for d in creative_meta_docs("acct")}


@pytest.fixture
def sample_targets() -> list[dict]:
    return target_docs("acct")


@pytest.fixture
def sample_reach() -> dict:
    return reach_index(reach_window_docs(as_of=AS_OF, account_id="acct"))


@pytest.fixture
def account() -> dict:
    return {"_id": "acct", "client_name": "Aurelia Jewels", "currency": "INR"}
