"""Sync orchestration — section 2.

Append-only (data rule 5). A re-sync of a day whose numbers have moved writes
a *new revision* and demotes the old one with `is_current: False`. The old
document keeps its values untouched, so the record of what Meta told us on
each day survives; only the pointer moves. Trend, streak, decay and
saturation all read that history.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from ..config import get_settings
from ..core.reach import ad_level_docs, named_windows
from ..crypto import decrypt_token
from .client import CallCounter, MetaClient
from .normalize import normalize_insight

log = logging.getLogger(__name__)

# Ad-level filtering goes out in batches of explicit adset ids. Small enough to
# stay under the URL and complexity limits, large enough not to make 200 calls.
ADSET_BATCH = 25

_running: set[str] = set()


class SyncBusy(RuntimeError):
    pass


async def sync_account(db, account: dict, *, mode: str = "refresh", days: int | None = None) -> dict:
    """Pull campaign, adset and ad level for one account and append snapshots."""
    account_id = str(account["_id"])
    if account_id in _running:
        raise SyncBusy(f"A sync is already running for {account.get('client_name')}")
    _running.add(account_id)

    settings = get_settings()
    counter = CallCounter()
    started = datetime.now(UTC)
    steps: list[dict] = []
    written = {"snapshots": 0, "entities": 0}
    status = "ok"
    error: str | None = None

    try:
        token = decrypt_token(account["access_token"])
        existing = await db.snapshots_daily.count_documents({"account_id": account_id}, limit=1)
        span = days or (
            settings.backfill_days if (mode == "backfill" or not existing) else settings.refresh_days
        )
        until = date.today()
        since = until - timedelta(days=span - 1)
        steps.append({"step": "range", "detail": f"{since} to {until} ({span} days)"})

        async with MetaClient(token, account["meta_ad_account_id"], counter=counter) as client:
            ads = await client.fetch_ads()
            ad_index = {a["ad_id"]: a for a in ads}
            steps.append({"step": "ads", "detail": f"{len(ads)} ads resolved to creatives"})

            await _cache_thumbnails(db, client, ads)

            adsets = await client.fetch_adsets()
            campaigns = await client.fetch_campaigns()
            entity_props = {e["entity_id"]: e for e in (*adsets, *campaigns)}
            steps.append(
                {"step": "entities", "detail": f"{len(adsets)} ad sets, {len(campaigns)} campaigns"}
            )

            # Three levels, three separate calls. `level` is set explicitly on
            # each one — it does not inherit.
            for level in ("campaign", "adset"):
                rows = await client.insights(level, since, until)
                docs = [
                    normalize_insight(r, account_id=account_id, level=level)
                    for r in rows
                ]
                docs = [d for d in docs if d]
                for doc in docs:
                    doc.update(_entity_properties(entity_props.get(doc["entity_id"], {})))
                written["entities"] += await _append(db.entity_daily, docs, _entity_key)
                steps.append({"step": f"{level}-insights", "detail": f"{len(docs)} rows"})

            adset_ids = sorted({a["adset_id"] for a in ads if a.get("adset_id")})
            ad_rows: list[dict] = []
            for batch in _batched(adset_ids, ADSET_BATCH):
                ad_rows.extend(await client.insights("ad", since, until, adset_ids=batch))
            docs = [
                normalize_insight(r, account_id=account_id, level="ad", ad_index=ad_index)
                for r in ad_rows
            ]
            dropped = sum(1 for d in docs if d is None)
            docs = [d for d in docs if d]
            written["snapshots"] += await _append(db.snapshots_daily, docs, _snapshot_key)
            steps.append(
                {
                    "step": "ad-insights",
                    "detail": f"{len(docs)} rows in {len(adset_ids)} ad sets"
                    + (f", {dropped} dropped with no resolvable creative" if dropped else ""),
                }
            )

            written["reach"] = await _sync_reach(
                db, client, account_id, ad_index, adset_ids, since, until
            )
            steps.append(
                {"step": "reach-windows", "detail": f"{written['reach']} deduplicated reach rows"}
            )

    except Exception as exc:  # noqa: BLE001 - every failure is recorded and surfaced, not swallowed
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        log.exception("Sync failed for %s", account.get("client_name"))
        steps.append({"step": "error", "detail": error})
    finally:
        _running.discard(account_id)

    finished = datetime.now(UTC)
    result = {
        "account_id": account_id,
        "mode": mode,
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": round((finished - started).total_seconds(), 1),
        "status": status,
        "error": error,
        "api_calls": counter.calls,
        "async_reports": counter.async_reports,
        "rows_written": written,
        "steps": steps,
    }

    await db.sync_log.insert_one(dict(result))
    await db.accounts.update_one(
        {"_id": account["_id"]},
        {
            "$set": {
                "last_sync_at": finished,
                "last_sync_status": status,
                "last_sync_error": error,
                "api_calls_last_sync": counter.calls,
            }
        },
    )
    result.pop("_id", None)
    return result


async def _sync_reach(
    db, client: MetaClient, account_id: str, ad_index: dict, adset_ids: Sequence[str],
    since: date, until: date
) -> int:
    """Fetch Meta's deduplicated reach for each named window.

    A few extra calls per sync, and the only way frequency means anything —
    see `core.reach` for the arithmetic this replaces.
    """
    from pymongo import UpdateMany

    as_of = until
    windows = named_windows(as_of, first_date=since)
    stamp = as_of.isoformat()
    written = 0

    for window_key, window in windows.items():
        if window.start > window.end:
            continue
        rows: list[dict] = []
        for batch in _batched(list(adset_ids), ADSET_BATCH):
            rows.extend(
                await client.window_insights("ad", window.start, window.end, adset_ids=batch)
            )
        docs = ad_level_docs(rows, account_id, window_key, ad_index)
        if not docs:
            continue
        now = datetime.now(UTC)
        await db.reach_windows.update_many(
            {"account_id": account_id, "window_key": window_key, "is_current": True},
            {"$set": {"is_current": False}},
        )
        await db.reach_windows.bulk_write(
            [
                UpdateMany(
                    {
                        "account_id": account_id,
                        "ad_id": d["ad_id"],
                        "window_key": window_key,
                        "as_of": stamp,
                    },
                    {
                        "$set": {
                            **d,
                            "as_of": stamp,
                            "window_start": window.start.isoformat(),
                            "window_end": window.end.isoformat(),
                            "is_current": True,
                            "synced_at": now,
                        }
                    },
                    upsert=True,
                )
                for d in docs
            ],
            ordered=False,
        )
        written += len(docs)
    return written


async def _cache_thumbnails(db, client: MetaClient, ads: Sequence[dict]) -> None:
    """Fetched once per creative_id and cached. Thumbnails do not change."""
    seen = {a["creative_id"]: a.get("thumbnail_url") for a in ads if a.get("creative_id")}
    known = {
        doc["creative_id"]
        async for doc in db.thumbnails.find(
            {"creative_id": {"$in": list(seen)}}, {"creative_id": 1}
        )
    }
    missing = [cid for cid, url in seen.items() if cid not in known and not url]
    fetched = await client.fetch_thumbnails(missing) if missing else {}

    updates = []
    for creative_id, url in seen.items():
        if creative_id in known:
            continue
        resolved = url or fetched.get(creative_id)
        if resolved:
            updates.append({"creative_id": creative_id, "thumbnail_url": resolved})
    if updates:
        from pymongo import UpdateOne

        await db.thumbnails.bulk_write(
            [
                UpdateOne({"creative_id": u["creative_id"]}, {"$set": u}, upsert=True)
                for u in updates
            ],
            ordered=False,
        )


def _entity_properties(props: dict) -> dict:
    return {
        "daily_budget": props.get("daily_budget"),
        "lifetime_budget": props.get("lifetime_budget"),
        "bid_strategy": props.get("bid_strategy"),
        "optimization_goal": props.get("optimization_goal"),
        "delivery_status": props.get("delivery_status"),
    }


def _snapshot_key(doc: dict) -> dict:
    return {
        "account_id": doc["account_id"],
        "creative_id": doc["creative_id"],
        "ad_id": doc["ad_id"],
        "date": doc["date"],
    }


def _entity_key(doc: dict) -> dict:
    return {
        "account_id": doc["account_id"],
        "entity_type": doc["entity_type"],
        "entity_id": doc["entity_id"],
        "date": doc["date"],
    }


# Fields whose movement means Meta revised the day and a new revision is due.
_MEASURED = (
    "amount_spent",
    "impressions",
    "reach",
    "outbound_clicks",
    "omni_landing_page_view",
    "omni_add_to_cart",
    "omni_purchase",
    "purchase_roas",
)


async def _append(collection, docs: Sequence[dict], key_fn) -> int:
    """Append-only write. Never overwrites a stored measurement.

    An unchanged day is a no-op: re-storing identical numbers under a new
    revision would bloat the collection without adding history.
    """
    from pymongo import InsertOne, UpdateMany

    if not docs:
        return 0

    keys = [key_fn(d) for d in docs]
    current: dict[tuple, dict] = {}
    async for existing in collection.find({"$or": keys, "is_current": True}):
        current[tuple(sorted(key_fn(existing).items()))] = existing

    now = datetime.now(UTC)
    operations = []
    inserted = 0
    for doc in docs:
        key = key_fn(doc)
        previous = current.get(tuple(sorted(key.items())))
        if previous and all(
            _close(previous.get(f), doc.get(f)) for f in _MEASURED
        ):
            continue
        revision = int(previous.get("revision", 0)) + 1 if previous else 1
        if previous:
            operations.append(UpdateMany(key, {"$set": {"is_current": False}}))
        operations.append(
            InsertOne({**doc, "revision": revision, "is_current": True, "synced_at": now})
        )
        inserted += 1

    if operations:
        await collection.bulk_write(operations, ordered=True)
    return inserted


def _close(a, b, tolerance: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    try:
        return abs(float(a or 0) - float(b or 0)) <= tolerance
    except (TypeError, ValueError):
        return a == b


def _batched(items: Sequence[str], size: int):
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


async def reconcile(db, account_id: str, since: str, until: str) -> dict:
    """Build-order step 1: verify ad-level totals against a known window total.

    Sums ad-level spend and purchases against the campaign-level pull for the
    same window. They come from different Insights calls, so agreement is real
    evidence the grain and the filters are right. A gap over ~1% usually means
    an ad-set batch was missed or `level` was wrong on one of the calls.
    """
    match = {"account_id": account_id, "is_current": True, "date": {"$gte": since, "$lte": until}}
    group = {
        "_id": None,
        "spend": {"$sum": "$amount_spent"},
        "purchases": {"$sum": "$omni_purchase"},
        "impressions": {"$sum": "$impressions"},
    }

    ad_totals = await _one(db.snapshots_daily.aggregate([{"$match": match}, {"$group": group}]))
    campaign_totals = await _one(
        db.entity_daily.aggregate(
            [{"$match": {**match, "entity_type": "campaign"}}, {"$group": group}]
        )
    )

    def gap(a: float, b: float) -> float | None:
        return round(abs(a - b) / b, 5) if b else None

    spend_gap = gap(ad_totals.get("spend", 0.0), campaign_totals.get("spend", 0.0))
    return {
        "account_id": account_id,
        "window": {"since": since, "until": until},
        "ad_level": {k: round(v, 2) for k, v in ad_totals.items() if k != "_id"},
        "campaign_level": {k: round(v, 2) for k, v in campaign_totals.items() if k != "_id"},
        "spend_gap": spend_gap,
        "purchase_gap": gap(
            ad_totals.get("purchases", 0.0), campaign_totals.get("purchases", 0.0)
        ),
        "verdict": (
            "unknown"
            if spend_gap is None
            else "match"
            if spend_gap <= 0.01
            else "investigate"
        ),
    }


async def _one(cursor) -> dict:
    async for doc in cursor:
        return doc
    return {}


async def sync_all(db) -> list[dict]:
    """Scheduled run — every account with a token, sequentially.

    Sequential on purpose: two accounts syncing at once share one app-level
    rate limit, and a 429 costs more than the wait.
    """
    results = []
    async for account in db.accounts.find({"access_token": {"$ne": None}}):
        try:
            results.append(await sync_account(db, account, mode="refresh"))
        except SyncBusy as exc:
            results.append({"account_id": str(account["_id"]), "status": "skipped", "error": str(exc)})
        await asyncio.sleep(1)
    return results
