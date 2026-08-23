"""Meta Marketing API client — section 2.

Three things here are field-tested rather than obvious:

* `level` is set explicitly on every Insights call. It does not inherit, and a
  missing `level` silently returns the wrong grain.
* Ad-level pulls time out on large accounts. On timeout we do not retry the
  same request — we switch to the async report flow (schedule, poll, fetch).
* Ad-level pulls are filtered by explicit `adset.id` values in an `IN` array.
  Filtering ad level by `adset.name` is unreliable, and partial-name filters on
  category names break on substring collisions — "Ring" captures "Earring".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

# Insights fields, ad level. `creative_id` and `effective_status` are not
# insights fields — they come off the /ads edge, joined in during sync.
AD_INSIGHT_FIELDS = [
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "campaign_id",
    "campaign_name",
    "objective",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "cpm",
    "ctr",
    "outbound_clicks",
    "outbound_clicks_ctr",
    "actions",
    "purchase_roas",
]

ADSET_INSIGHT_FIELDS = [
    "adset_id",
    "adset_name",
    "campaign_id",
    "campaign_name",
    "objective",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "cpm",
    "ctr",
    "outbound_clicks",
    "outbound_clicks_ctr",
    "actions",
    "purchase_roas",
]

CAMPAIGN_INSIGHT_FIELDS = [
    "campaign_id",
    "campaign_name",
    "objective",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "cpm",
    "ctr",
    "outbound_clicks",
    "outbound_clicks_ctr",
    "actions",
    "purchase_roas",
]

AD_ENTITY_FIELDS = "id,name,adset_id,campaign_id,effective_status,created_time,creative{id,thumbnail_url}"
ADSET_ENTITY_FIELDS = (
    "id,name,campaign_id,daily_budget,lifetime_budget,bid_strategy,"
    "optimization_goal,effective_status,status"
)
CAMPAIGN_ENTITY_FIELDS = "id,name,objective,daily_budget,lifetime_budget,bid_strategy,effective_status,status"


class MetaApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


@dataclass
class CallCounter:
    """Surfaced on the Sync Settings screen — the operator should be able to see
    how much of their rate limit a sync just spent."""

    calls: int = 0
    async_reports: int = 0
    errors: list[str] = field(default_factory=list)


class MetaClient:
    def __init__(self, access_token: str, ad_account_id: str, *, counter: CallCounter | None = None):
        settings = get_settings()
        self.token = access_token
        self.account = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self.base = f"{settings.meta_api_base}/{settings.meta_api_version}"
        self.timeout = settings.meta_sync_timeout_seconds
        self.counter = counter or CallCounter()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> MetaClient:
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("MetaClient must be used as an async context manager")
        return self._client

    # --- plumbing ----------------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        params = {**params, "access_token": self.token}
        self.counter.calls += 1
        response = await self.http.get(f"{self.base}/{path}", params=params)
        return self._unwrap(response)

    async def _post(self, path: str, data: dict) -> dict:
        data = {**data, "access_token": self.token}
        self.counter.calls += 1
        response = await self.http.post(f"{self.base}/{path}", data=data)
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict:
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": {"message": response.text[:500]}}
            error = payload.get("error", {})
            raise MetaApiError(
                error.get("message") or f"HTTP {response.status_code}",
                status=response.status_code,
                payload=payload,
            )
        return response.json()

    async def _paginate(self, path: str, params: dict) -> AsyncIterator[dict]:
        page = await self._get(path, params)
        while True:
            for row in page.get("data", []):
                yield row
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                return
            self.counter.calls += 1
            response = await self.http.get(nxt)
            page = self._unwrap(response)

    # --- entity edges ------------------------------------------------------

    async def fetch_ads(self) -> list[dict]:
        """Ad -> creative_id, thumbnail, effective_status.

        Insights does not carry `creative_id`, and data rule 3 is unenforceable
        without it: without this join every row would have to be keyed on
        `ad_id` and the same asset in three ad sets would fragment into three
        Leaderboard ranks.
        """
        rows = []
        async for ad in self._paginate(
            f"{self.account}/ads", {"fields": AD_ENTITY_FIELDS, "limit": 500}
        ):
            creative = ad.get("creative") or {}
            rows.append(
                {
                    "ad_id": str(ad["id"]),
                    "ad_name": ad.get("name"),
                    "adset_id": str(ad.get("adset_id") or ""),
                    "campaign_id": str(ad.get("campaign_id") or ""),
                    "creative_id": str(creative.get("id") or ""),
                    "thumbnail_url": creative.get("thumbnail_url"),
                    "effective_status": ad.get("effective_status"),
                    "created_time": ad.get("created_time"),
                }
            )
        return rows

    async def fetch_adsets(self) -> list[dict]:
        rows = []
        async for adset in self._paginate(
            f"{self.account}/adsets", {"fields": ADSET_ENTITY_FIELDS, "limit": 500}
        ):
            rows.append(
                {
                    "entity_type": "adset",
                    "entity_id": str(adset["id"]),
                    "name": adset.get("name"),
                    "campaign_id": str(adset.get("campaign_id") or ""),
                    # Meta returns budgets in minor units (paise for INR).
                    "daily_budget": _minor_to_major(adset.get("daily_budget")),
                    "lifetime_budget": _minor_to_major(adset.get("lifetime_budget")),
                    "bid_strategy": adset.get("bid_strategy"),
                    "optimization_goal": adset.get("optimization_goal"),
                    "delivery_status": adset.get("effective_status"),
                }
            )
        return rows

    async def fetch_campaigns(self) -> list[dict]:
        rows = []
        async for campaign in self._paginate(
            f"{self.account}/campaigns", {"fields": CAMPAIGN_ENTITY_FIELDS, "limit": 500}
        ):
            rows.append(
                {
                    "entity_type": "campaign",
                    "entity_id": str(campaign["id"]),
                    "name": campaign.get("name"),
                    "objective": campaign.get("objective"),
                    "daily_budget": _minor_to_major(campaign.get("daily_budget")),
                    "lifetime_budget": _minor_to_major(campaign.get("lifetime_budget")),
                    "bid_strategy": campaign.get("bid_strategy"),
                    "delivery_status": campaign.get("effective_status"),
                }
            )
        return rows

    # --- insights ----------------------------------------------------------

    def _insight_params(
        self,
        level: str,
        since: date,
        until: date,
        adset_ids: Sequence[str] | None = None,
    ) -> dict:
        fields = {
            "ad": AD_INSIGHT_FIELDS,
            "adset": ADSET_INSIGHT_FIELDS,
            "campaign": CAMPAIGN_INSIGHT_FIELDS,
        }[level]
        params = {
            # Set explicitly on every call. `level` does not inherit, and the
            # wrong grain is silent, not an error.
            "level": level,
            "fields": ",".join(fields),
            "time_increment": 1,
            "time_range": f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}',
            "limit": 500,
            "action_report_time": "conversion",
            "use_unified_attribution_setting": "true",
        }
        if adset_ids:
            # Explicit ids in an IN array. Never `adset.name`, and never a
            # partial-name filter — "Ring" would capture "Earring".
            import json

            params["filtering"] = json.dumps(
                [{"field": "adset.id", "operator": "IN", "value": list(adset_ids)}]
            )
        return params

    async def window_insights(
        self,
        level: str,
        since: date,
        until: date,
        adset_ids: Sequence[str] | None = None,
    ) -> list[dict]:
        """The same pull with no `time_increment`.

        This is the only way to get Meta's *deduplicated* reach for a window.
        Summing daily reach counts a returning person once per day, which puts
        every frequency threshold in section 5 permanently out of reach.
        """
        params = self._insight_params(level, since, until, adset_ids)
        params.pop("time_increment", None)
        try:
            rows = []
            async for row in self._paginate(f"{self.account}/insights", params):
                rows.append(row)
            return rows
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout):
            return await self.async_report(params)
        except MetaApiError as exc:
            if _is_too_much_data(exc):
                return await self.async_report(params)
            raise

    async def insights(
        self,
        level: str,
        since: date,
        until: date,
        adset_ids: Sequence[str] | None = None,
    ) -> list[dict]:
        """One Insights pull. Falls back to the async report flow on timeout."""
        params = self._insight_params(level, since, until, adset_ids)
        try:
            rows = []
            async for row in self._paginate(f"{self.account}/insights", params):
                rows.append(row)
            return rows
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout):
            log.warning(
                "%s-level insights timed out for %s (%s..%s) — switching to the async report flow",
                level,
                self.account,
                since,
                until,
            )
            return await self.async_report(params)
        except MetaApiError as exc:
            # Code 1/2 and the "reduce the amount of data" family mean the same
            # thing as a timeout: this query is too big for a sync call.
            if _is_too_much_data(exc):
                log.warning("%s-level insights too large — async report flow", level)
                return await self.async_report(params)
            raise

    async def async_report(self, params: dict) -> list[dict]:
        """Schedule a report, poll until it completes, fetch the rows."""
        settings = get_settings()
        self.counter.async_reports += 1
        started = await self._post(f"{self.account}/insights", {**params, "async": "true"})
        report_id = started.get("report_run_id") or started.get("id")
        if not report_id:
            raise MetaApiError("Async report did not return a report_run_id", payload=started)

        for _ in range(settings.meta_async_max_polls):
            await asyncio.sleep(settings.meta_async_poll_seconds)
            status = await self._get(str(report_id), {"fields": "async_status,async_percent_completion"})
            state = status.get("async_status")
            if state == "Job Completed":
                break
            if state in {"Job Failed", "Job Skipped"}:
                raise MetaApiError(f"Async report {report_id} ended as {state}", payload=status)
        else:
            raise MetaApiError(f"Async report {report_id} did not complete in time")

        rows = []
        async for row in self._paginate(f"{report_id}/insights", {"limit": 500}):
            rows.append(row)
        return rows

    async def fetch_thumbnails(self, creative_ids: Sequence[str]) -> dict[str, str]:
        """Fetched once per creative_id and cached by the caller.

        Thumbnails do not change and there can be thousands of them; refetching
        every sync is the easiest way to burn a rate limit on nothing.
        """
        out: dict[str, str] = {}
        for chunk in _chunks(list(creative_ids), 50):
            payload = await self._get("", {"ids": ",".join(chunk), "fields": "id,thumbnail_url"})
            for creative_id, body in payload.items():
                if body.get("thumbnail_url"):
                    out[str(creative_id)] = body["thumbnail_url"]
        return out


def _is_too_much_data(exc: MetaApiError) -> bool:
    error = (exc.payload.get("error") or {})
    code = error.get("code")
    message = str(error.get("message", "")).lower()
    return code in {1, 2} or "reduce the amount of data" in message or "please reduce" in message


def _minor_to_major(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
