"""The one-glance summary the Flag Center header reads."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..core.constants import SETTLING_DAYS
from ..db import get_db
from ..repo import active_snoozes, list_accounts
from ..service import build_flags, load_bundle, untagged

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(preset: str = "30d"):
    db = get_db()
    accounts = await list_accounts(db)
    snoozed = await active_snoozes(db)
    bundles = [
        b
        for b in await asyncio.gather(
            *(load_bundle(db, str(a["_id"]), preset=preset) for a in accounts)
        )
        if b
    ]

    clients = []
    totals = {"red": 0, "amber": 0, "blue": 0, "grey": 0}
    for bundle in bundles:
        flags = build_flags(bundle, snoozed)
        counts = {level: sum(1 for f in flags if f.severity == level) for level in totals}
        for level, count in counts.items():
            totals[level] += count
        statuses: dict[str, int] = {}
        for view in bundle.views:
            statuses[view.status.status] = statuses.get(view.status.status, 0) + 1
        clients.append(
            {
                "account_id": bundle.account_id,
                "client_name": bundle.client_name,
                "currency": bundle.account.get("currency", "INR"),
                "as_of": bundle.as_of.isoformat(),
                "spend": round(sum(v.window.spend for v in bundle.views), 2),
                "revenue": round(sum(v.window.revenue for v in bundle.views), 2),
                "purchases": int(sum(v.window.purchases for v in bundle.views)),
                "roas": round(
                    sum(v.window.revenue for v in bundle.views)
                    / max(sum(v.window.spend for v in bundle.views), 1e-9),
                    3,
                ),
                "flags": counts,
                "red_money": round(
                    sum(f.money_at_stake for f in flags if f.severity == "red"), 2
                ),
                "statuses": statuses,
                # Stays on the dashboard until it drops under 10%.
                "untagged": untagged(bundle),
                "targets_complete": {"low", "high"} <= {t["aov_band"] for t in bundle.targets},
                "last_sync_at": bundle.account.get("last_sync_at"),
                "last_sync_status": bundle.account.get("last_sync_status"),
                "settling_window": bundle.settling,
            }
        )

    clients.sort(key=lambda c: -c["red_money"])
    return {
        "clients": clients,
        "flag_totals": totals,
        "settling_days": SETTLING_DAYS,
        "window": bundles[0].window.as_dict() if bundles else None,
    }
