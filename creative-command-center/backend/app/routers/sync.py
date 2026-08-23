"""Sync Settings — trigger a pull, read the log, reconcile the totals."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..db import get_db
from ..meta.sync import SyncBusy, reconcile, sync_account
from ..models import SyncRequest
from ..repo import get_account
from ..service import invalidate

router = APIRouter(prefix="/api/sync", tags=["sync"])


async def _run(account_id: str, mode: str, days: int | None) -> None:
    db = get_db()
    account = await get_account(db, account_id)
    if account:
        await sync_account(db, account, mode=mode, days=days)
        invalidate(account_id)


@router.post("/{account_id}")
async def trigger_sync(account_id: str, payload: SyncRequest, background: BackgroundTasks):
    db = get_db()
    account = await get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="No such account")
    if not account.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="No access token stored for this client. Add one on Sync Settings first.",
        )
    try:
        background.add_task(_run, account_id, payload.mode, payload.days)
    except SyncBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "started", "mode": payload.mode, "account_id": account_id}


@router.get("/{account_id}")
async def sync_status(account_id: str, limit: int = 20):
    db = get_db()
    account = await get_account(db, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="No such account")
    log = [
        {**doc, "_id": str(doc["_id"])}
        async for doc in db.sync_log.find({"account_id": account_id})
        .sort("started_at", -1)
        .limit(limit)
    ]
    snapshots = await db.snapshots_daily.count_documents(
        {"account_id": account_id, "is_current": True}
    )
    newest = await db.snapshots_daily.find_one(
        {"account_id": account_id, "is_current": True}, sort=[("date", -1)]
    )
    oldest = await db.snapshots_daily.find_one(
        {"account_id": account_id, "is_current": True}, sort=[("date", 1)]
    )
    return {
        "account_id": account_id,
        "client_name": account.get("client_name"),
        "last_sync_at": account.get("last_sync_at"),
        "last_sync_status": account.get("last_sync_status"),
        "last_sync_error": account.get("last_sync_error"),
        "api_calls_last_sync": account.get("api_calls_last_sync"),
        "snapshot_rows": snapshots,
        "date_range": {
            "from": oldest.get("date") if oldest else None,
            "to": newest.get("date") if newest else None,
        },
        "log": log,
    }


@router.get("/{account_id}/reconcile")
async def reconcile_totals(account_id: str, since: str, until: str):
    """Ad-level totals against the campaign-level pull for the same window.

    Build-order step 1 says verify before building anything else. A spend gap
    over 1% means an ad-set batch was missed or a `level` was wrong.
    """
    return await reconcile(get_db(), account_id, since, until)
