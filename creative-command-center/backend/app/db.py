"""MongoDB connection and index setup.

`snapshots_daily` is append-only (data rule 5). The unique index on
(account_id, creative_id, ad_id, date, revision) lets a re-sync of a still-
settling day append a *new revision* rather than overwrite the old one —
trend, streak, decay and saturation all read history, and an overwrite is how
that history quietly disappears.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from .config import get_settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(get_settings().mongodb_uri, tz_aware=True)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_settings().mongodb_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


INDEXES: dict[str, list[IndexModel]] = {
    "accounts": [IndexModel([("client_name", ASCENDING)])],
    "targets": [
        IndexModel([("account_id", ASCENDING), ("aov_band", ASCENDING)], unique=True),
    ],
    "creative_meta": [
        IndexModel([("account_id", ASCENDING), ("creative_id", ASCENDING)], unique=True),
        IndexModel([("account_id", ASCENDING), ("category", ASCENDING)]),
        IndexModel([("account_id", ASCENDING), ("aov_band", ASCENDING), ("angle_id", ASCENDING)]),
    ],
    "snapshots_daily": [
        IndexModel(
            [
                ("account_id", ASCENDING),
                ("creative_id", ASCENDING),
                ("ad_id", ASCENDING),
                ("date", ASCENDING),
                ("revision", ASCENDING),
            ],
            unique=True,
            name="append_only_key",
        ),
        IndexModel([("account_id", ASCENDING), ("date", DESCENDING)]),
        IndexModel([("account_id", ASCENDING), ("adset_id", ASCENDING), ("date", DESCENDING)]),
        IndexModel([("account_id", ASCENDING), ("campaign_id", ASCENDING), ("date", DESCENDING)]),
        IndexModel([("account_id", ASCENDING), ("is_current", ASCENDING), ("date", DESCENDING)]),
    ],
    "entity_daily": [
        IndexModel(
            [
                ("account_id", ASCENDING),
                ("entity_type", ASCENDING),
                ("entity_id", ASCENDING),
                ("date", ASCENDING),
                ("revision", ASCENDING),
            ],
            unique=True,
            name="append_only_key",
        ),
        IndexModel([("account_id", ASCENDING), ("entity_type", ASCENDING), ("date", DESCENDING)]),
    ],
    "reach_windows": [
        IndexModel(
            [
                ("account_id", ASCENDING),
                ("ad_id", ASCENDING),
                ("window_key", ASCENDING),
                ("as_of", ASCENDING),
            ],
            unique=True,
            name="append_only_key",
        ),
        IndexModel([("account_id", ASCENDING), ("is_current", ASCENDING)]),
    ],
    "actions_log": [
        IndexModel([("account_id", ASCENDING), ("proposed_at", DESCENDING)]),
        IndexModel([("entity_type", ASCENDING), ("entity_id", ASCENDING)]),
    ],
    "flag_snoozes": [
        IndexModel([("dedupe_key", ASCENDING)], unique=True),
        IndexModel([("account_id", ASCENDING), ("snoozed_until", ASCENDING)]),
    ],
    "thumbnails": [IndexModel([("creative_id", ASCENDING)], unique=True)],
    "sync_log": [IndexModel([("account_id", ASCENDING), ("started_at", DESCENDING)])],
}


async def ensure_indexes(db: AsyncIOMotorDatabase | None = None) -> None:
    db = db or get_db()
    for collection, indexes in INDEXES.items():
        await db[collection].create_indexes(indexes)
