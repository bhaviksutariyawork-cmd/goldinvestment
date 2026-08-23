"""Mongo reads. Every query here filters `is_current` — the collections are
append-only, so without it a revised day would be counted twice."""

from __future__ import annotations

from datetime import date, datetime

from bson import ObjectId


def oid(value: str) -> ObjectId:
    return value if isinstance(value, ObjectId) else ObjectId(value)


async def list_accounts(db) -> list[dict]:
    return [a async for a in db.accounts.find().sort("client_name", 1)]


async def get_account(db, account_id: str) -> dict | None:
    return await db.accounts.find_one({"_id": oid(account_id)})


async def load_snapshots(
    db, account_id: str, since: str | None = None, until: str | None = None
) -> list[dict]:
    query: dict = {"account_id": account_id, "is_current": True}
    if since or until:
        window: dict = {}
        if since:
            window["$gte"] = since
        if until:
            window["$lte"] = until
        query["date"] = window
    return [doc async for doc in db.snapshots_daily.find(query, {"_id": 0})]


async def load_entity_daily(
    db, account_id: str, entity_type: str | None = None,
    since: str | None = None, until: str | None = None
) -> list[dict]:
    query: dict = {"account_id": account_id, "is_current": True}
    if entity_type:
        query["entity_type"] = entity_type
    if since or until:
        window: dict = {}
        if since:
            window["$gte"] = since
        if until:
            window["$lte"] = until
        query["date"] = window
    return [doc async for doc in db.entity_daily.find(query, {"_id": 0})]


async def load_reach_windows(db, account_id: str) -> list[dict]:
    """Meta's deduplicated reach per named window — see `core.reach`."""
    return [
        doc
        async for doc in db.reach_windows.find(
            {"account_id": account_id, "is_current": True}, {"_id": 0}
        )
    ]


async def creative_meta(db, account_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    async for doc in db.creative_meta.find({"account_id": account_id}):
        doc["id"] = str(doc.pop("_id"))
        out[str(doc["creative_id"])] = doc
    return out


async def targets(db, account_id: str) -> list[dict]:
    return [
        {**t, "id": str(t.pop("_id"))}
        async for t in db.targets.find({"account_id": account_id})
    ]


async def targets_by_band(db, account_id: str) -> dict[str, dict]:
    return {t["aov_band"]: t for t in await targets(db, account_id)}


async def latest_date(db, account_id: str) -> date | None:
    doc = await db.snapshots_daily.find_one(
        {"account_id": account_id, "is_current": True},
        sort=[("date", -1)],
        projection={"date": 1},
    )
    return date.fromisoformat(doc["date"]) if doc else None


async def active_snoozes(db, account_id: str | None = None) -> set[str]:
    """Dedupe keys currently suppressed. Expired snoozes simply stop matching."""
    query: dict = {"snoozed_until": {"$gt": datetime.now().astimezone()}}
    if account_id:
        query["account_id"] = account_id
    return {doc["dedupe_key"] async for doc in db.flag_snoozes.find(query, {"dedupe_key": 1})}


async def manual_pauses(db, account_id: str) -> set[str]:
    """Creatives the operator has confirmed a pause on.

    Read from `actions_log`, not from a status column: the log is the record,
    and reconstructing state from it means the audit trail and the UI can never
    disagree.
    """
    query = {
        "account_id": account_id,
        "action": "pause",
        "confirmed_at": {"$ne": None},
        "entity_type": {"$in": ["creative", "ad"]},
    }
    out: set[str] = set()
    async for doc in db.actions_log.find(query, {"entity_id": 1, "new_value": 1}):
        out.add(str(doc["entity_id"]))
        for creative_id in (doc.get("new_value") or {}).get("creative_ids", []):
            out.add(str(creative_id))
    return out
