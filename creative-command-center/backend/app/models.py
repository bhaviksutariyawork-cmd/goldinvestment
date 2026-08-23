"""Request/response schemas — section 1.

Snapshot documents store raw values only. No ratio is ever persisted: ROAS,
CPA, CPM, transfer rate and delivery share are all computed at read time from
these fields, so a change to how a ratio is defined never requires a backfill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AovBand = Literal["low", "high"]
Severity = Literal["red", "amber", "blue", "grey"]


# --- accounts ---------------------------------------------------------------


class AccountIn(BaseModel):
    client_name: str
    meta_ad_account_id: str = Field(description="act_XXXXXXXX or the bare numeric id")
    access_token: str | None = Field(default=None, description="Stored encrypted, never returned")
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"


class AccountOut(BaseModel):
    id: str
    client_name: str
    meta_ad_account_id: str
    currency: str
    timezone: str
    has_token: bool = False
    token_hint: str | None = None
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    api_calls_last_sync: int | None = None


# --- targets ----------------------------------------------------------------


class TargetIn(BaseModel):
    """Per client AND per AOV band.

    A single client-level ROAS target mislabels both bands: the same target
    that is ambitious on a 2,800 AOV product is trivial on a 565 one. At least
    two rows per account are required before any verdict is trusted.
    """

    aov_band: AovBand
    target_roas: float
    target_cpa: float
    aov_min: float | None = None
    aov_max: float | None = None


class TargetOut(TargetIn):
    id: str
    account_id: str


# --- creative_meta ----------------------------------------------------------


class CreativeMetaIn(BaseModel):
    category: str | None = None
    aov_band: AovBand | None = None
    angle_id: str | None = None
    format: str | None = None
    hook_type: str | None = None
    offer_type: str | None = None
    lp_type: str | None = None
    notes: str | None = None


class BulkTagIn(BaseModel):
    """Multi-select rows and apply one tag to all of them at once."""

    creative_ids: list[str]
    tags: CreativeMetaIn


# --- actions ----------------------------------------------------------------


class ActionProposeIn(BaseModel):
    entity_type: Literal["ad", "adset", "campaign", "creative", "account", "cell", "band", "category"]
    entity_id: str
    entity_name: str | None = None
    action: str
    reason_flag: str | None = None
    prior_value: dict | None = None
    new_value: dict | None = None
    ad_ids: list[str] = Field(default_factory=list)


class ActionConfirmIn(BaseModel):
    confirmed_by: str = "operator"
    note: str | None = None


class SnoozeIn(BaseModel):
    """Suppression is mandatory-reason on purpose. A snooze without a stated
    reason is indistinguishable from ignoring the flag."""

    dedupe_key: str
    account_id: str
    entity_type: str
    entity_id: str
    flag_key: str
    days: Literal[7, 14, 30]
    reason: str = Field(min_length=3)
    snoozed_by: str = "operator"


# --- sync -------------------------------------------------------------------


class SyncRequest(BaseModel):
    mode: Literal["backfill", "refresh"] = "refresh"
    days: int | None = None


class SyncStatus(BaseModel):
    account_id: str
    running: bool
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    api_calls_last_sync: int | None = None
    rows_written: int | None = None
    log: list[dict] = Field(default_factory=list)
