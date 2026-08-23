"""Screen C — the Flag Center. Every problem across every client, one screen."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException

from ..core.constants import SEVERITIES
from ..core.flags import FLAG_DEFS
from ..db import get_db
from ..models import SnoozeIn
from ..repo import active_snoozes, list_accounts
from ..service import build_flags, load_bundle, untagged

router = APIRouter(prefix="/api/flags", tags=["flags"])


@router.get("")
async def flag_center(
    account_id: str | None = None,
    preset: str = "30d",
    severity: str | None = None,
    include_snoozed: bool = False,
):
    """Grouped by severity, ranked within each group by money at stake.

    Red is losing money now, amber is degrading, blue is opportunity, grey is
    data quality. Snoozed flags are hidden by default — without suppression
    this screen becomes noise inside a week.
    """
    db = get_db()
    accounts = await list_accounts(db)
    if account_id:
        accounts = [a for a in accounts if str(a["_id"]) == account_id]
    if not accounts:
        raise HTTPException(status_code=404, detail="No accounts to flag")

    snoozed = set() if include_snoozed else await active_snoozes(db)
    bundles = await asyncio.gather(
        *(load_bundle(db, str(a["_id"]), preset=preset) for a in accounts)
    )

    flags = []
    dashboard = []
    for bundle in bundles:
        if bundle is None:
            continue
        flags.extend(f.as_dict() for f in build_flags(bundle, snoozed))
        dashboard.append(
            {
                "account_id": bundle.account_id,
                "client_name": bundle.client_name,
                "untagged": untagged(bundle),
                "as_of": bundle.as_of.isoformat(),
                "settling_window": bundle.settling,
            }
        )

    if severity:
        flags = [f for f in flags if f["severity"] == severity]

    groups = []
    for level in SEVERITIES:
        items = [f for f in flags if f["severity"] == level]
        groups.append(
            {
                "severity": level,
                "label": {
                    "red": "Act today",
                    "amber": "Watch / rotate",
                    "blue": "Opportunity",
                    "grey": "Data quality",
                }[level],
                "count": len(items),
                "money_at_stake": round(sum(f["money_at_stake"] for f in items), 2),
                "flags": items,
            }
        )

    return {
        "groups": groups,
        "total": len(flags),
        "clients": dashboard,
        "catalogue": FLAG_DEFS,
    }


@router.post("/snooze")
async def snooze(payload: SnoozeIn):
    """Suppress one flag on one entity for 7/14/30 days.

    A reason is required and the snooze is written to `actions_log` as well —
    the operator should be able to audit what they chose to ignore, not only
    what they acted on.
    """
    db = get_db()
    now = datetime.now(UTC)
    until = now + timedelta(days=payload.days)
    doc = {
        "dedupe_key": payload.dedupe_key,
        "account_id": payload.account_id,
        "flag_key": payload.flag_key,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "reason": payload.reason,
        "snoozed_by": payload.snoozed_by,
        "snoozed_at": now,
        "snoozed_until": until,
        "days": payload.days,
    }
    await db.flag_snoozes.update_one(
        {"dedupe_key": payload.dedupe_key}, {"$set": doc}, upsert=True
    )
    await db.actions_log.insert_one(
        {
            "account_id": doc["account_id"],
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
            "action": "snooze_flag",
            "reason_flag": payload.flag_key,
            "proposed_at": now,
            "confirmed_at": now,
            "confirmed_by": payload.snoozed_by,
            "prior_value": None,
            "new_value": {"snoozed_until": until, "reason": payload.reason},
        }
    )
    return {"dedupe_key": payload.dedupe_key, "snoozed_until": until}


@router.delete("/snooze/{dedupe_key:path}")
async def unsnooze(dedupe_key: str):
    db = get_db()
    await db.flag_snoozes.delete_one({"dedupe_key": dedupe_key})
    return {"dedupe_key": dedupe_key, "snoozed": False}


@router.get("/snoozes")
async def list_snoozes(account_id: str | None = None):
    db = get_db()
    query = {"account_id": account_id} if account_id else {}
    return [
        {**doc, "_id": str(doc["_id"])}
        async for doc in db.flag_snoozes.find(query).sort("snoozed_until", 1)
    ]
