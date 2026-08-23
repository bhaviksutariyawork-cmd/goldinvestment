"""Load the synthetic account into MongoDB.

    python -m app.seed              # create/refresh the demo client
    python -m app.seed --drop       # wipe it first

Useful before a real access token exists, and as a fixed dataset to check the
verdict engine against after a change.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime

from . import sample_data
from .db import ensure_indexes, get_db
from .service import invalidate


async def seed(drop: bool = False, days: int = 90, as_of: date | None = None) -> dict:
    db = get_db()
    await ensure_indexes(db)
    as_of = as_of or date.today()

    account = await db.accounts.find_one({"meta_ad_account_id": sample_data.ACCOUNT})
    if account and drop:
        await _wipe(db, str(account["_id"]))
        account = None

    if not account:
        result = await db.accounts.insert_one(
            {
                "client_name": sample_data.CLIENT,
                "meta_ad_account_id": sample_data.ACCOUNT,
                "currency": "INR",
                "timezone": "Asia/Kolkata",
                "access_token": None,
                "token_hint": None,
                "last_sync_at": datetime.now(UTC),
                "last_sync_status": "ok",
                "last_sync_error": None,
                "api_calls_last_sync": 0,
                "is_demo": True,
                "created_at": datetime.now(UTC),
            }
        )
        account_id = str(result.inserted_id)
    else:
        account_id = str(account["_id"])
        await _wipe(db, account_id)
        await db.accounts.update_one(
            {"_id": account["_id"]},
            {"$set": {"last_sync_at": datetime.now(UTC), "last_sync_status": "ok"}},
        )

    rows = sample_data.generate(days, as_of=as_of, account_id=account_id)
    await db.snapshots_daily.insert_many(rows)
    await db.entity_daily.insert_many(
        sample_data.entity_rows(days, as_of=as_of, account_id=account_id)
    )
    await db.reach_windows.insert_many(
        sample_data.reach_window_docs(as_of=as_of, days=days, account_id=account_id)
    )
    meta_docs = sample_data.creative_meta_docs(account_id)
    if meta_docs:
        await db.creative_meta.insert_many(meta_docs)
    for target in sample_data.target_docs(account_id):
        await db.targets.update_one(
            {"account_id": account_id, "aov_band": target["aov_band"]},
            {"$set": target},
            upsert=True,
        )

    invalidate(account_id)
    return {
        "account_id": account_id,
        "client_name": sample_data.CLIENT,
        "snapshot_rows": len(rows),
        "tagged_creatives": len(meta_docs),
        "as_of": as_of.isoformat(),
    }


async def _wipe(db, account_id: str) -> None:
    for collection in (
        "snapshots_daily",
        "entity_daily",
        "reach_windows",
        "creative_meta",
        "targets",
        "actions_log",
        "flag_snoozes",
        "sync_log",
    ):
        await db[collection].delete_many({"account_id": account_id})


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo client")
    parser.add_argument("--drop", action="store_true", help="Remove the demo account first")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    print(asyncio.run(seed(drop=args.drop, days=args.days)))


if __name__ == "__main__":
    main()
