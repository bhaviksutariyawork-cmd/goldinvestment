"""Ingestion: Meta's JSON shapes, and the fields we refuse to read."""

from __future__ import annotations

from datetime import date, timedelta

from app.core.reach import ReachKey, index, key_for, named_windows
from app.core.windows import settled_end
from app.meta.client import AD_INSIGHT_FIELDS, MetaClient
from app.meta.normalize import normalize_insight, outbound_clicks, purchase_roas

AD_INDEX = {
    "6301": {
        "ad_id": "6301",
        "creative_id": "cr_88",
        "thumbnail_url": "https://example.test/t.jpg",
        "effective_status": "ACTIVE",
    }
}

RAW = {
    "date_start": "2026-08-14",
    "date_stop": "2026-08-14",
    "ad_id": "6301",
    "ad_name": "112-4",
    "adset_id": "5501",
    "adset_name": "Rings | Broad",
    "campaign_id": "4401",
    "campaign_name": "Prospecting",
    "objective": "OUTCOME_SALES",
    "spend": "2400.55",
    "impressions": "84000",
    "reach": "51000",
    "frequency": "1.647",
    "cpm": "28.58",
    "ctr": "1.21",
    "outbound_clicks": [{"action_type": "outbound_click", "value": "612"}],
    "outbound_clicks_ctr": [{"action_type": "outbound_click", "value": "0.728"}],
    "actions": [
        {"action_type": "omni_purchase", "value": "4"},
        {"action_type": "omni_add_to_cart", "value": "31"},
        {"action_type": "omni_landing_page_view", "value": "551"},
        {"action_type": "link_click", "value": "890"},
    ],
    "purchase_roas": [{"action_type": "omni_purchase", "value": "1.7455"}],
    "omni_purchase_values": "6800000",  # the 100x-inflated field, present and ignored
}


def test_ad_row_normalises_into_a_snapshot_document():
    doc = normalize_insight(RAW, account_id="acct", level="ad", ad_index=AD_INDEX)
    assert doc["creative_id"] == "cr_88"
    assert doc["ad_id"] == "6301"
    assert doc["amount_spent"] == 2400.55
    assert doc["omni_purchase"] == 4
    assert doc["purchase_roas"] == 1.7455
    assert doc["thumbnail_url"] == "https://example.test/t.jpg"
    assert doc["date"] == "2026-08-14"


def test_the_inflated_revenue_field_is_never_carried_through():
    doc = normalize_insight(RAW, account_id="acct", level="ad", ad_index=AD_INDEX)
    assert "omni_purchase_values" not in doc
    assert "action_values" not in doc


def test_outbound_clicks_not_link_clicks():
    """`link_click` is 890 in the payload. The denominator must be 612."""
    doc = normalize_insight(RAW, account_id="acct", level="ad", ad_index=AD_INDEX)
    assert doc["outbound_clicks"] == 612
    assert outbound_clicks({"outbound_clicks": 44}) == 44


def test_purchase_roas_is_read_out_of_the_array():
    assert purchase_roas({"purchase_roas": [{"action_type": "purchase", "value": "2.5"}]}) == 2.5
    assert purchase_roas({"purchase_roas": 3.0}) == 3.0
    assert purchase_roas({}) == 0.0


def test_an_ad_with_no_resolvable_creative_is_dropped_not_invented():
    """A wrong creative_id would silently merge two different assets into one
    Leaderboard row."""
    assert normalize_insight(RAW, account_id="acct", level="ad", ad_index={}) is None


def test_campaign_and_adset_levels_produce_entity_documents():
    raw = {**RAW, "campaign_name": "Prospecting"}
    doc = normalize_insight(raw, account_id="acct", level="campaign")
    assert doc["entity_type"] == "campaign"
    assert doc["entity_id"] == "4401"
    assert "creative_id" not in doc


def test_insight_params_always_set_level_explicitly():
    """`level` does not inherit, and the wrong grain is silent, not an error."""
    client = MetaClient("token", "act_1", counter=None)
    for level in ("campaign", "adset", "ad"):
        params = client._insight_params(level, date(2026, 8, 1), date(2026, 8, 10))
        assert params["level"] == level
        assert params["time_increment"] == 1


def test_ad_level_filters_on_explicit_adset_ids():
    """Never `adset.name`: a partial-name filter on "Ring" captures "Earring"."""
    client = MetaClient("token", "act_1")
    params = client._insight_params("ad", date(2026, 8, 1), date(2026, 8, 10), ["5501", "5502"])
    assert '"field": "adset.id"' in params["filtering"]
    assert '"operator": "IN"' in params["filtering"]
    assert "adset.name" not in params["filtering"]


def test_requested_ad_fields_cover_the_brief():
    assert {"outbound_clicks", "outbound_clicks_ctr", "purchase_roas", "reach",
            "frequency", "actions"} <= set(AD_INSIGHT_FIELDS)
    assert "omni_purchase_values" not in AD_INSIGHT_FIELDS


def test_named_reach_windows_end_at_the_settled_edge():
    as_of = date(2026, 8, 23)
    windows = named_windows(as_of, first_date=date(2026, 6, 1))
    edge = settled_end(as_of)
    assert windows["trailing_7"].end == edge
    assert windows["prior_7"].end == edge - timedelta(days=7)
    assert key_for(windows["trailing_7"], as_of) == "trailing_7"
    assert key_for(windows["lifetime"], as_of) is None  # no stored match for an odd span


def test_reach_index_rolls_ads_up_and_marks_the_basis():
    docs = [
        {"creative_id": "c1", "window_key": "trailing_7", "reach": 10_000},
        {"creative_id": "c1", "window_key": "trailing_7", "reach": 4_000},
        {"creative_id": "c2", "window_key": "trailing_7", "reach": 9_000},
    ]
    rolled = index(docs)
    assert rolled[ReachKey("c1", "trailing_7")]["reach"] == 14_000
    assert rolled[ReachKey("c1", "trailing_7")]["placements"] == 2
    assert rolled[ReachKey("c2", "trailing_7")]["placements"] == 1
