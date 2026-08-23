"""Proposals and the audit trail.

Nothing here pauses anything by itself. Every action is a proposal the
operator confirms, and confirmation records the intent — it does not call the
Meta API. Pausing only: an ad is never deleted, because deleting it destroys
the social proof and the history attached to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import ActionConfirmIn, ActionProposeIn
from ..service import invalidate

router = APIRouter(prefix="/api/actions", tags=["actions"])

# Pause is in. Delete is not, and never will be.
ALLOWED_ACTIONS = {
    "pause",
    "raise_budget",
    "reallocate_budget",
    "split_adset",
    "duplicate_into_adset",
    "review_landing_page",
    "brief_creative",
    "tag_creative",
    "resync",
    "snooze_flag",
}


@router.post("/{account_id}/propose", status_code=201)
async def propose(account_id: str, payload: ActionProposeIn):
    if payload.action not in ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"{payload.action} is not a proposable action. "
            "Deleting an ad is deliberately not supported — pause instead.",
        )
    db = get_db()
    doc = {
        "account_id": account_id,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "entity_name": payload.entity_name,
        "action": payload.action,
        "reason_flag": payload.reason_flag,
        "proposed_at": datetime.now(UTC),
        "confirmed_at": None,
        "confirmed_by": None,
        "prior_value": payload.prior_value,
        "new_value": {**(payload.new_value or {}), "ad_ids": payload.ad_ids},
    }
    result = await db.actions_log.insert_one(doc)
    return {**doc, "_id": str(result.inserted_id)}


@router.post("/{account_id}/{action_id}/confirm")
async def confirm(account_id: str, action_id: str, payload: ActionConfirmIn):
    """Confirm a proposal. This records the decision; it does not call Meta.

    Auto-pausing is out of scope by design — the operator confirms, then acts
    in Ads Manager, and this log is what they audit their own decisions
    against later.
    """
    db = get_db()
    now = datetime.now(UTC)
    doc = await db.actions_log.find_one_and_update(
        {"_id": ObjectId(action_id), "account_id": account_id},
        {
            "$set": {
                "confirmed_at": now,
                "confirmed_by": payload.confirmed_by,
                "note": payload.note,
            }
        },
        return_document=True,
    )
    if not doc:
        raise HTTPException(status_code=404, detail="No such proposal")
    invalidate(account_id)
    return {**doc, "_id": str(doc["_id"])}


@router.delete("/{account_id}/{action_id}")
async def withdraw(account_id: str, action_id: str):
    """Withdraw an unconfirmed proposal. Confirmed ones stay — the log is the
    record, and a record you can delete is not one."""
    db = get_db()
    result = await db.actions_log.delete_one(
        {"_id": ObjectId(action_id), "account_id": account_id, "confirmed_at": None}
    )
    if not result.deleted_count:
        raise HTTPException(
            status_code=404, detail="No unconfirmed proposal with that id"
        )
    return {"withdrawn": action_id}


@router.get("/{account_id}")
async def list_actions(account_id: str, limit: int = 200, pending_only: bool = False):
    db = get_db()
    query: dict = {"account_id": account_id}
    if pending_only:
        query["confirmed_at"] = None
    return [
        {**doc, "_id": str(doc["_id"])}
        async for doc in db.actions_log.find(query).sort("proposed_at", -1).limit(limit)
    ]
