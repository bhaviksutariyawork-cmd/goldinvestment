"""Accounts and per-band targets."""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException

from ..crypto import TokenVaultError, encrypt_token, mask
from ..db import get_db
from ..models import AccountIn, AccountOut, TargetIn
from ..service import invalidate

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _out(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "client_name": doc.get("client_name"),
        "meta_ad_account_id": doc.get("meta_ad_account_id"),
        "currency": doc.get("currency", "INR"),
        "timezone": doc.get("timezone", "Asia/Kolkata"),
        "has_token": bool(doc.get("access_token")),
        "token_hint": doc.get("token_hint"),
        "last_sync_at": doc.get("last_sync_at"),
        "last_sync_status": doc.get("last_sync_status"),
        "last_sync_error": doc.get("last_sync_error"),
        "api_calls_last_sync": doc.get("api_calls_last_sync"),
    }


@router.get("", response_model=list[AccountOut])
async def list_accounts():
    db = get_db()
    return [_out(a) async for a in db.accounts.find().sort("client_name", 1)]


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(payload: AccountIn):
    db = get_db()
    doc = {
        "client_name": payload.client_name,
        "meta_ad_account_id": payload.meta_ad_account_id,
        "currency": payload.currency,
        "timezone": payload.timezone,
        "access_token": None,
        "token_hint": None,
        "last_sync_at": None,
        "last_sync_status": None,
        "last_sync_error": None,
        "created_at": datetime.now(UTC),
    }
    if payload.access_token:
        try:
            doc["access_token"] = encrypt_token(payload.access_token)
        except TokenVaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        doc["token_hint"] = mask(payload.access_token)
    result = await db.accounts.insert_one(doc)
    return _out({**doc, "_id": result.inserted_id})


@router.put("/{account_id}", response_model=AccountOut)
async def update_account(account_id: str, payload: AccountIn):
    db = get_db()
    update = {
        "client_name": payload.client_name,
        "meta_ad_account_id": payload.meta_ad_account_id,
        "currency": payload.currency,
        "timezone": payload.timezone,
    }
    if payload.access_token:
        try:
            update["access_token"] = encrypt_token(payload.access_token)
        except TokenVaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        update["token_hint"] = mask(payload.access_token)
    doc = await db.accounts.find_one_and_update(
        {"_id": ObjectId(account_id)}, {"$set": update}, return_document=True
    )
    if not doc:
        raise HTTPException(status_code=404, detail="No such account")
    invalidate(account_id)
    return _out(doc)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str):
    db = get_db()
    await db.accounts.delete_one({"_id": ObjectId(account_id)})
    invalidate(account_id)


@router.get("/{account_id}/targets")
async def list_targets(account_id: str):
    """Targets live per client AND per AOV band.

    The UI blocks verdicts until at least two bands are set: one client-level
    ROAS target mislabels both.
    """
    db = get_db()
    targets = [
        {**t, "id": str(t.pop("_id"))} async for t in db.targets.find({"account_id": account_id})
    ]
    return {
        "targets": targets,
        "bands_set": sorted({t["aov_band"] for t in targets}),
        "complete": {"low", "high"} <= {t["aov_band"] for t in targets},
        "warning": None
        if {"low", "high"} <= {t["aov_band"] for t in targets}
        else "Set a target for both AOV bands. A single client-level ROAS target mislabels both.",
    }


@router.put("/{account_id}/targets/{aov_band}")
async def upsert_target(account_id: str, aov_band: str, payload: TargetIn):
    db = get_db()
    if aov_band != payload.aov_band:
        raise HTTPException(status_code=400, detail="Band in the path and body must match")
    doc = {"account_id": account_id, **payload.model_dump()}
    await db.targets.update_one(
        {"account_id": account_id, "aov_band": aov_band}, {"$set": doc}, upsert=True
    )
    invalidate(account_id)
    return doc
