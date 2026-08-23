"""Creative Command Center — API.

One job: which creative to kill, which to scale, which to leave alone, and
which category needs new creative.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .core.constants import (
    COVERAGE_CELL_IMPRESSIONS,
    HHI_CONCENTRATION,
    LEARNING_THRESHOLD_EVENTS,
    MIN_PURCHASES_FOR_VERDICT,
    SETTLING_DAYS,
)
from .core.flags import FLAG_DEFS
from .core.status import STATUS_ACTION, STATUSES
from .db import close_client, ensure_indexes, get_db
from .meta.sync import sync_all
from .routers import (
    accounts,
    actions,
    coverage,
    creatives,
    dashboard,
    flags,
    hierarchy,
    leaderboard,
    sync,
    tagging,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ccc")

scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    global scheduler
    try:
        await ensure_indexes()
    except Exception:  # noqa: BLE001 - the API is still useful while Mongo is coming up
        log.exception("Index setup failed — is MongoDB reachable at %s?", settings.mongodb_uri)

    if settings.enable_scheduler:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            lambda: sync_all(get_db()),
            "interval",
            hours=settings.sync_interval_hours,
            id="meta-sync",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        log.info("Scheduled Meta sync every %sh", settings.sync_interval_hours)

    yield

    if scheduler:
        scheduler.shutdown(wait=False)
    await close_client()


app = FastAPI(
    title="Creative Command Center",
    description=(
        "Kill / scale / hold decisions across multiple Meta ad accounts, without opening "
        "Ads Manager."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    dashboard,
    accounts,
    sync,
    hierarchy,
    leaderboard,
    flags,
    tagging,
    coverage,
    creatives,
    actions,
):
    app.include_router(module.router)


@app.get("/api/health")
async def health():
    db = get_db()
    try:
        await db.command("ping")
        mongo = "ok"
    except Exception as exc:  # noqa: BLE001
        mongo = f"unreachable: {exc}"
    return {"status": "ok", "mongodb": mongo}


@app.get("/api/rules")
async def rules():
    """The thresholds the UI renders, served from the same constants the engine
    uses. A number shown on a card is never a copy of one in a React file."""
    return {
        "settling_days": SETTLING_DAYS,
        "min_purchases_for_verdict": MIN_PURCHASES_FOR_VERDICT,
        "coverage_cell_impressions": COVERAGE_CELL_IMPRESSIONS,
        "hhi_threshold": HHI_CONCENTRATION,
        "learning_threshold_events": LEARNING_THRESHOLD_EVENTS,
        "statuses": [
            {"status": s, "action": STATUS_ACTION[s]} for s in STATUSES
        ],
        "flags": FLAG_DEFS,
        "data_rules": [
            "Revenue = purchase_roas x amount_spent. omni_purchase_values is never read.",
            "Purchases gate verdicts, not spend. 30 purchases minimum for any ROAS verdict.",
            "Rows aggregate by creative_id, not ad_id. ad_id is kept for drill-down and pauses.",
            "The trailing 3 days are attribution-incomplete and are excluded from every comparison.",
            "Snapshots are append-only. Nothing is ever overwritten.",
            "outbound_clicks is the click denominator, not link_clicks or clicks (all).",
            "Nothing is auto-paused. Every action is a proposal. Ads are paused, never deleted.",
        ],
    }
