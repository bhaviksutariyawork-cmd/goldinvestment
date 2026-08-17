"""Configuration loading and validation.

`config/alerts.yaml` is the single source of truth for every threshold. This module
validates it at startup with pydantic so a typo fails loudly on line 1 of the run
rather than silently disabling an alert three hours later.

Secrets come from the environment only and are never written to logs or the DB.
"""

from __future__ import annotations

import os
from datetime import date as Date
from datetime import time as Time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "alerts.yaml"
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / "analyst.md"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "gold.db"


class Strict(BaseModel):
    """Reject unknown keys everywhere, so a misspelled threshold is an error."""

    model_config = ConfigDict(extra="forbid")


def _empty_list_ok(v: object) -> object:
    """Treat a key with no value as an empty list.

    A YAML key whose only content is comments — `levels:` followed by commented-out
    entries — parses as None, not []. That is the normal state of an optional list
    in this config, so coerce rather than fail.
    """
    return [] if v is None else v


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


class Investor(Strict):
    timezone: str = "Asia/Kolkata"
    home_currency: str = "INR"
    physical_unit: str = "10g"
    purity_reference: int = 995

    @field_validator("timezone")
    @classmethod
    def _tz_exists(cls, v: str) -> str:
        ZoneInfo(v)  # raises if the zone is unknown
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Window(Strict):
    start: Time
    end: Time

    def contains(self, t: Time) -> bool:
        """Membership test that handles windows wrapping past midnight."""
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


class Budget(Strict):
    max_per_day: int = Field(3, ge=0)
    cooldown_hours: float = Field(6, ge=0)
    quiet_hours: Window


class TriggerBase(Strict):
    enabled: bool = True
    cooldown_hours: float | None = Field(None, ge=0)


class GoldMove(TriggerBase):
    window_hours: float = Field(4, gt=0)
    threshold_pct: float = Field(1.2, gt=0)
    source: Literal["XAUUSD", "GC=F"] = "XAUUSD"


class PctMove(TriggerBase):
    window_hours: float = Field(24, gt=0)
    threshold_pct: float = Field(gt=0)


class RealYieldMove(TriggerBase):
    series: str = "DFII10"
    window_hours: float = Field(24, gt=0)
    threshold_bp: float = Field(8, gt=0)


class RatioMove(TriggerBase):
    enabled: bool = False
    window_hours: float = Field(24, gt=0)
    threshold_abs: float = Field(2.0, gt=0)


class ConflictEscalation(TriggerBase):
    window_hours: float = Field(2, gt=0)
    min_independent_sources: int = Field(3, ge=1)
    min_keyword_hits: int = Field(2, ge=1)
    keywords: list[str]
    independent_domains: list[str]

    @field_validator("keywords", "independent_domains")
    @classmethod
    def _lowered(cls, v: list[str]) -> list[str]:
        return [item.strip().lower() for item in v if item.strip()]


class RateOddsShift(TriggerBase):
    window_hours: float = Field(24, gt=0)
    threshold_pp: float = Field(10, gt=0)
    meeting: str = "next"


class PriceLevel(Strict):
    value: float = Field(gt=0)
    direction: Literal["above", "below", "either"] = "either"
    note: str = ""
    triggered: bool = False
    added: Date | None = None


class InrPriceLevels(TriggerBase):
    levels: list[PriceLevel] = Field(default_factory=list)

    _coerce_levels = field_validator("levels", mode="before")(_empty_list_ok)


class Triggers(Strict):
    gold_move: GoldMove
    dxy_move: PctMove
    real_yield_move: RealYieldMove
    gold_silver_ratio: RatioMove
    conflict_escalation: ConflictEscalation
    rate_odds_shift: RateOddsShift
    inr_price_levels: InrPriceLevels

    def items(self) -> list[tuple[str, TriggerBase]]:
        return [(name, getattr(self, name)) for name in self.model_fields]


class Alerts(Strict):
    enabled: bool = True
    budget: Budget
    check_interval_minutes: int = Field(30, gt=0)
    active_window: Window
    triggers: Triggers

    def cooldown_for(self, trigger_name: str) -> float:
        """Per-trigger cooldown, falling back to the global budget value."""
        trigger = getattr(self.triggers, trigger_name, None)
        if trigger is not None and trigger.cooldown_hours is not None:
            return trigger.cooldown_hours
        return self.budget.cooldown_hours


class StalenessHours(Strict):
    price: float = 6
    fx: float = 6
    macro: float = 48
    etf_flows: float = 72
    cot: float = 240
    central_bank: float = 1080
    news: float = 12


class DataQuality(Strict):
    spot_divergence_pct: float = Field(0.3, gt=0)
    staleness_hours: StalenessHours
    min_collectors_ok: int = Field(6, ge=1)


# Monday-first, matching datetime.weekday(). Module scope because a leading
# underscore on a class attribute makes pydantic treat it as a private attr.
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Briefing(Strict):
    enabled: bool = True
    ist_time: Time
    days: list[str]
    max_words: int = Field(450, gt=0)
    lookback_days: int = Field(7, ge=0)
    only_if_changed: bool = False
    change_floor_pct: float = Field(0.25, ge=0)
    include: list[str] = Field(default_factory=list)

    _coerce_include = field_validator("include", mode="before")(_empty_list_ok)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, v: list[str]) -> list[str]:
        lowered = [d.strip().lower() for d in v]
        bad = [d for d in lowered if d not in DAY_NAMES]
        if bad:
            raise ValueError(f"unknown day(s) {bad}; expected any of {DAY_NAMES}")
        return lowered

    def runs_on(self, weekday: int) -> bool:
        """`weekday` is Monday=0, matching datetime.weekday()."""
        return DAY_NAMES[weekday] in self.days


class Briefings(Strict):
    morning: Briefing
    evening: Briefing
    weekly: Briefing


class DutyStack(Strict):
    import_duty_pct: float = Field(ge=0)
    agri_infra_cess_pct: float = Field(ge=0)
    gst_pct: float = Field(ge=0)
    as_of: Date
    note: str = ""

    @property
    def landed_multiplier(self) -> float:
        """Multiplier from assessable import value to landed pre-margin cost.

        The levies compound rather than sum: duty and AIDC cess apply to the
        assessable value, and GST applies to the value *plus* those duties. Adding
        the three percentages together understates the total, which is why this is
        a computed multiplier and not `sum(...)`.
        """
        duty_and_cess = 1.0 + (self.import_duty_pct + self.agri_infra_cess_pct) / 100.0
        return duty_and_cess * (1.0 + self.gst_pct / 100.0)

    @property
    def effective_pct(self) -> float:
        """The compounded stack as a single percentage, for reporting."""
        return (self.landed_multiplier - 1.0) * 100.0


class SeasonEvent(Strict):
    name: str
    date: Date | None = None
    start: Date | None = None
    end: Date | None = None

    @model_validator(mode="after")
    def _one_shape(self) -> SeasonEvent:
        has_point = self.date is not None
        has_range = self.start is not None and self.end is not None
        if has_point == has_range:
            raise ValueError(f"event {self.name!r} needs either `date` or both `start` and `end`")
        if has_range and self.end < self.start:
            raise ValueError(f"event {self.name!r} ends before it starts")
        return self


class Seasonality(Strict):
    enabled: bool = True
    lead_days: int = Field(21, ge=0)
    events: list[SeasonEvent] = Field(default_factory=list)

    _coerce_events = field_validator("events", mode="before")(_empty_list_ok)


class SovereignGoldBond(Strict):
    check_enabled: bool = True


class India(Strict):
    duty_stack_fallback: DutyStack
    seasonality: Seasonality
    sovereign_gold_bond: SovereignGoldBond


class Spend(Strict):
    daily_usd_cap: float = Field(1.50, gt=0)
    per_run_usd_cap: float = Field(0.25, gt=0)
    input_usd_per_mtok: float = Field(3.00, ge=0)
    output_usd_per_mtok: float = Field(15.00, ge=0)
    on_cap_exceeded: Literal["notify", "silent"] = "notify"


class Llm(Strict):
    model: str = "claude-sonnet-4-6"
    max_output_tokens: int = Field(2000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    thinking: Literal["adaptive", "disabled"] = "adaptive"
    spend: Spend


class Mute(Strict):
    max_duration_hours: float = Field(24, gt=0)


class Telegram(Strict):
    parse_mode: Literal["Markdown", "MarkdownV2", "HTML"] = "Markdown"
    disable_web_page_preview: bool = True
    commands_enabled: bool = True
    command_poll_interval_minutes: int = Field(30, gt=0)
    mute: Mute
    failure_notice: bool = True


class Config(Strict):
    version: int
    investor: Investor
    alerts: Alerts
    data_quality: DataQuality
    briefings: Briefings
    india: India
    llm: Llm
    telegram: Telegram

    # Populated after load; not part of the YAML.
    path: Path = Field(default=DEFAULT_CONFIG_PATH, exclude=True)

    @property
    def tz(self) -> ZoneInfo:
        return self.investor.tz

    def briefing(self, mode: str) -> Briefing:
        try:
            return getattr(self.briefings, mode)
        except AttributeError as exc:
            raise KeyError(f"no briefing configured for mode {mode!r}") from exc


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #


class Secrets:
    """Environment-only credentials.

    `require()` is called at the point of use rather than at import, so a dry run
    that never touches Telegram does not demand a bot token.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = dict(os.environ if env is None else env)

    def get(self, name: str) -> str | None:
        value = self._env.get(name, "").strip()
        return value or None

    def require(self, name: str, why: str) -> str:
        value = self.get(name)
        if not value:
            raise MissingSecret(
                f"{name} is not set. Needed to {why}. "
                f"Add it under Settings → Secrets and variables → Actions, "
                f"or export it locally. See README.md."
            )
        return value

    # Convenience accessors, named for the GitHub secret they map to.
    @property
    def anthropic_api_key(self) -> str:
        return self.require("ANTHROPIC_API_KEY", "call the Claude API")

    @property
    def telegram_bot_token(self) -> str:
        return self.require("TELEGRAM_BOT_TOKEN", "send Telegram messages")

    @property
    def telegram_chat_id(self) -> str:
        return self.require("TELEGRAM_CHAT_ID", "address Telegram messages to you")

    @property
    def fred_api_key(self) -> str | None:
        # Optional: the FRED collector degrades to stale rather than failing the run.
        return self.get("FRED_API_KEY")


class MissingSecret(RuntimeError):
    """Raised when a credential is needed but absent."""


class ConfigError(RuntimeError):
    """Raised when config/alerts.yaml is missing or invalid."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load(path: str | Path | None = None) -> Config:
    """Read and validate the config. Raises ConfigError with a readable message."""
    config_path = Path(path or os.environ.get("GOLDAGENT_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at the top level")

    try:
        config = Config.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"{config_path} failed validation:\n{exc}") from exc

    config.path = config_path
    return config


def load_prompt(path: str | Path | None = None) -> str:
    """Read the analyst system prompt."""
    prompt_path = Path(path or os.environ.get("GOLDAGENT_PROMPT") or DEFAULT_PROMPT_PATH)
    if not prompt_path.exists():
        raise ConfigError(f"system prompt not found: {prompt_path}")
    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConfigError(f"system prompt is empty: {prompt_path}")
    return text


def save(config: Config, path: str | Path | None = None) -> Path:
    """Write the config back, preserving nothing but the data.

    Used only by `/set`, which appends an INR price level. Comments in the YAML
    are lost on rewrite, so this is deliberately the sole writer — every other
    consumer is read-only.
    """
    target = Path(path or config.path)
    payload = config.model_dump(mode="json", exclude={"path"}, exclude_none=True)
    header = (
        "# Gold Market Intelligence Agent — alert & threshold configuration\n"
        "#\n"
        "# NOTE: this file was last written programmatically (by the /set command),\n"
        "# which does not preserve comments. The committed version in git history\n"
        "# before this rewrite has the full annotated original.\n"
    )
    target.write_text(
        header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target
