"""Every endpoint, against a seeded in-memory Mongo.

The unit tests prove the engine is right; this proves the wiring is — routes,
serialisation, and the shapes the frontend types are written against. It runs
on `mongomock_motor` so it needs no server.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

pytest.importorskip("mongomock_motor", reason="dev-only: pip install -r requirements-dev.txt")

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from app import db as dbmod  # noqa: E402

AS_OF = date(2026, 8, 23)


@pytest.fixture(scope="module")
def client():
    mock = AsyncMongoMockClient()
    database = mock["ccc_test"]

    import sys

    dbmod.get_client = lambda: mock
    dbmod.get_db = lambda: database

    async def _no_indexes(db=None):
        return None

    dbmod.ensure_indexes = _no_indexes

    from app import config

    config.get_settings.cache_clear()
    settings = config.get_settings()
    settings.enable_scheduler = False

    import app.seed  # noqa: F401
    from app.main import app as fastapi_app

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "get_db"):
            module.get_db = dbmod.get_db

    from app.seed import seed

    result = asyncio.get_event_loop().run_until_complete(
        seed(drop=True, days=90, as_of=AS_OF)
    )
    with TestClient(fastapi_app) as test_client:
        test_client.account_id = result["account_id"]  # type: ignore[attr-defined]
        yield test_client


def account(client) -> str:
    return client.account_id  # type: ignore[attr-defined]


def ok(client, path: str) -> dict:
    response = client.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:400]}"
    return response.json()


def test_health_and_rules(client):
    assert ok(client, "/api/health")["status"] == "ok"
    rules = ok(client, "/api/rules")
    assert rules["settling_days"] == 3
    assert rules["min_purchases_for_verdict"] == 30
    assert len(rules["data_rules"]) == 7


def test_accounts_and_targets(client):
    accounts = ok(client, "/api/accounts")
    assert accounts and accounts[0]["client_name"]
    # A token is never returned, only a hint.
    assert "access_token" not in accounts[0]

    targets = ok(client, f"/api/accounts/{account(client)}/targets")
    assert targets["complete"] is True
    assert {t["aov_band"] for t in targets["targets"]} == {"low", "high"}
    assert all("_id" not in t for t in targets["targets"])


def test_dashboard(client):
    payload = ok(client, "/api/dashboard?preset=30d")
    summary = payload["clients"][0]
    assert summary["spend"] > 0
    assert summary["untagged"]["visible"] is True
    assert sum(payload["flag_totals"].values()) > 0


def test_flag_center_groups_and_ranking(client):
    payload = ok(client, "/api/flags?preset=30d")
    groups = {g["severity"]: g for g in payload["groups"]}
    assert set(groups) == {"red", "amber", "blue", "grey"}
    red = groups["red"]["flags"]
    assert red, "the demo account should have red flags"
    assert [f["money_at_stake"] for f in red] == sorted(
        (f["money_at_stake"] for f in red), reverse=True
    )
    assert all(f["trigger"] and f["why"] for f in red)


def test_snooze_round_trip(client):
    payload = ok(client, "/api/flags?preset=30d")
    flag = payload["groups"][0]["flags"][0]

    response = client.post(
        "/api/flags/snooze",
        json={
            "dedupe_key": flag["dedupe_key"],
            "account_id": flag["account_id"],
            "entity_type": flag["entity_type"],
            "entity_id": flag["entity_id"],
            "flag_key": flag["key"],
            "days": 7,
            "reason": "Client is rebuilding the landing page this week",
        },
    )
    assert response.status_code == 200

    after = ok(client, "/api/flags?preset=30d")
    keys = {f["dedupe_key"] for g in after["groups"] for f in g["flags"]}
    assert flag["dedupe_key"] not in keys

    # And it is auditable, not just hidden.
    log = ok(client, f"/api/actions/{flag['account_id']}")
    assert any(entry["action"] == "snooze_flag" for entry in log)

    client.delete(f"/api/flags/snooze/{flag['dedupe_key']}")
    restored = ok(client, "/api/flags?preset=30d")
    assert flag["dedupe_key"] in {f["dedupe_key"] for g in restored["groups"] for f in g["flags"]}


def test_hierarchy_every_level(client):
    acct = account(client)
    for level in ("campaign", "adset", "ad"):
        payload = ok(client, f"/api/hierarchy/{acct}?level={level}&preset=30d")
        assert payload["rows"], level
        for row in payload["rows"]:
            assert "delivery_share" in row
            assert row["metrics"]["spend"] > 0

    campaigns = ok(client, f"/api/hierarchy/{acct}?level=campaign&preset=30d")["rows"]
    shares = [row["delivery_share"] for row in campaigns]
    assert abs(sum(shares) - 1.0) < 0.01, "campaign shares should cover the account"


def test_adset_detail_surfaces_misallocation(client):
    detail = ok(client, f"/api/hierarchy/{account(client)}/adset/60011?preset=30d")
    assert detail["misallocation"]["present"] is True
    assert detail["misallocation"]["best_ad"]["delivery_share"] < 0.25
    assert detail["learning_threshold"]["threshold"] == 50
    assert detail["budget_pacing"]["days"]


def test_leaderboard_gate_and_testing_table(client):
    payload = ok(client, f"/api/leaderboard?account_id={account(client)}&preset=30d")
    assert all(row["metrics"]["purchases"] >= 30 for row in payload["ranked"])
    assert all(row["rank"] == index + 1 for index, row in enumerate(payload["ranked"]))
    assert payload["ranked"][0]["badge"] == "gold"

    for row in payload["testing"]:
        # Upper-funnel metrics only — no ROAS to misread on a thin sample.
        assert "roas" not in row["metrics"]
        assert "cost_per_outbound_click" in row["metrics"]

    multi = next(r for r in payload["ranked"] if r["creative_id"] == "cr_multi_05")
    assert multi["adset_count"] == 3, "one row in the Leaderboard, three ad sets underneath"


def test_within_adset_only_groups_real_competitors(client):
    payload = ok(client, f"/api/leaderboard/within-adset?account_id={account(client)}&preset=30d")
    assert payload["misallocated_count"] > 0
    contested = next(g for g in payload["groups"] if g["adset_id"] == "60011")
    assert contested["misallocated"] is True
    assert contested["creatives"][0]["rank_in_adset"] == 1


def test_tagging_and_bulk_apply(client):
    acct = account(client)
    queue = ok(client, f"/api/tagging/{acct}?only_untagged=true")
    assert queue["untagged"]["visible"] is True
    assert queue["rows"], "the demo account opens with tagging work to do"
    assert queue["rows"] == sorted(queue["rows"], key=lambda r: -r["spend"])

    creative_ids = [row["creative_id"] for row in queue["rows"]]
    response = client.post(
        f"/api/tagging/{acct}/bulk",
        json={"creative_ids": creative_ids, "tags": {"category": "Rings", "aov_band": "low"}},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == len(creative_ids)

    after = ok(client, f"/api/tagging/{acct}?only_untagged=true")
    assert after["untagged"]["untagged_share"] < queue["untagged"]["untagged_share"]


def test_coverage_matrix_and_queue(client):
    payload = ok(client, f"/api/coverage/{account(client)}?preset=90d")
    assert payload["matrix"]["categories"]
    assert payload["matrix"]["impression_floor"] == 5000
    assert any(cell["state"] == "untested" for cell in payload["matrix"]["cells"])
    assert payload["priority_queue"][0]["score"] >= payload["priority_queue"][-1]["score"]
    assert payload["concentration"]


def test_creative_detail_marks_settling_days(client):
    payload = ok(client, f"/api/creatives/{account(client)}/cr_scale_01?preset=90d")
    assert payload["creative"]["status"] == "STARVED"
    settling = [point for point in payload["series"] if point["settling"]]
    assert len(settling) == 3
    assert all(point["date"] > payload["settled_through"] for point in settling)


def test_action_proposal_is_never_automatic(client):
    acct = account(client)
    created = client.post(
        f"/api/actions/{acct}/propose",
        json={
            "entity_type": "creative",
            "entity_id": "cr_cut_06",
            "entity_name": "104-1",
            "action": "pause",
            "reason_flag": "high_cac",
            "ad_ids": ["ad_cr_cut_06_60015"],
        },
    )
    assert created.status_code == 201
    entry = created.json()
    assert entry["confirmed_at"] is None, "a proposal is not an action"

    confirmed = client.post(f"/api/actions/{acct}/{entry['_id']}/confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_at"]


def test_delete_is_not_a_proposable_action(client):
    """Pausing only — deleting an ad destroys social proof and history."""
    response = client.post(
        f"/api/actions/{account(client)}/propose",
        json={"entity_type": "ad", "entity_id": "ad1", "action": "delete"},
    )
    assert response.status_code == 400
    assert "pause instead" in response.json()["detail"]


def test_sync_status_and_reconciliation(client):
    acct = account(client)
    status = ok(client, f"/api/sync/{acct}")
    assert status["snapshot_rows"] > 0

    window = status["date_range"]
    result = ok(client, f"/api/sync/{acct}/reconcile?since={window['from']}&until={window['to']}")
    assert result["verdict"] == "match"
    assert result["spend_gap"] == 0.0


def test_sync_without_a_token_is_refused(client):
    response = client.post(f"/api/sync/{account(client)}", json={"mode": "refresh"})
    assert response.status_code == 400
    assert "access token" in response.json()["detail"]
