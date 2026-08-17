"""Two-way Telegram commands.

Latency note: these are answered on the scheduled watch tick, because GitHub
Actions cannot hold a webhook listener open. Worst-case delay is one tick.

Cost note: `/now`, `/why` and `/deep` call the model and go through the same
pre-call spend guard as a briefing. `/levels` and `/set` are computed locally and
cost nothing — levels and highs and lows are arithmetic, and paying for a model
call to read numbers off a table would be waste.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from goldagent import collect, db, synthesize
from goldagent.config import Config
from goldagent.models import Snapshot

log = logging.getLogger(__name__)

HELP = """*Gold agent commands*

`/now` — current prices, INR and USD, with a one-line read
`/why` — what drove the last 24 hours
`/levels` — key technical and psychological levels
`/set <price>` — add an INR/10g alert level (e.g. `/set 118000`)
`/unset <price>` — remove a level you added
`/mute 4h` — suppress event alerts for a while
`/deep <topic>` — longer analysis on one driver
`/help` — this list

Commands are answered on the next scheduled check, so a reply can take up to \
{interval} minutes. Scheduled briefings arrive at 07:30 and 18:30 IST, plus \
Sunday at 10:00."""

# Sanity band for a level, so a fat-fingered `/set 118` or `/set 11800000` is
# rejected rather than silently creating an alert that can never fire.
LEVEL_BAND_INR = (20_000.0, 1_000_000.0)

MAX_DEEP_TOPIC_CHARS = 200


@dataclass(slots=True)
class Reply:
    text: str
    # False when the command was understood but produced no model call — used by
    # the caller to decide whether to bother recording spend.
    called_model: bool = False


@dataclass(slots=True)
class Context:
    conn: sqlite3.Connection
    config: Config
    client: object | None  # anthropic.Anthropic, or None in dry-run
    system_prompt: str
    now: datetime


def dispatch(text: str, ctx: Context) -> Reply | None:
    """Route one message. Returns None for anything that is not a command."""
    text = text.strip()
    if not text.startswith("/"):
        return None

    # Telegram appends @botname when a command is used in a group.
    head, _, rest = text.partition(" ")
    command = head.split("@", 1)[0].lower()
    argument = rest.strip()

    handlers = {
        "/now": _now,
        "/why": _why,
        "/levels": _levels,
        "/set": _set,
        "/unset": _unset,
        "/mute": _mute,
        "/unmute": _unmute,
        "/deep": _deep,
        "/help": _help,
        "/start": _help,
    }

    handler = handlers.get(command)
    if handler is None:
        return Reply(
            f"Unknown command `{command}`.\n\n" + _help_text(ctx),
        )

    try:
        return handler(argument, ctx)
    except synthesize.SpendCapExceeded as exc:
        log.warning("command hit the spend cap", extra={"command": command, "error": str(exc)})
        return Reply(
            f"⚠️ Daily Claude budget reached, so `{command}` was not answered.\n\n"
            f"{exc}\n\nScheduled briefings take priority over commands. Raise "
            f"`llm.spend.daily_usd_cap` in `config/alerts.yaml` if you want more headroom."
        )
    except Exception as exc:  # noqa: BLE001 — a bad command must not kill the run
        log.error("command handler failed", extra={"command": command}, exc_info=True)
        return Reply(f"⚠️ `{command}` failed: {type(exc).__name__}: {str(exc)[:300]}")


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def _help(_argument: str, ctx: Context) -> Reply:
    return Reply(_help_text(ctx))


def _help_text(ctx: Context) -> str:
    return HELP.format(interval=ctx.config.alerts.check_interval_minutes)


def _now(_argument: str, ctx: Context) -> Reply:
    snapshot = collect.quick(ctx.config)
    prices = _price_lines(snapshot, ctx.config)

    if not prices:
        return Reply(
            "⚠️ Could not retrieve prices right now.\n\n"
            + "\n".join(f"- {name}: {r.error}" for name, r in snapshot.results.items() if not r.ok)
        )

    body = "\n".join(prices)

    if ctx.client is None:
        return Reply(f"*Now*\n\n{body}\n\n_(dry run — no model read)_")

    user_message = (
        f"# /now request\n\n"
        f"Current readings:\n\n{body}\n\n"
        f"## YOUR TASK\n\n"
        f"Give exactly one sentence reading what these levels imply for someone "
        f"accumulating gold. No preamble, no recommendation, no forecast. One sentence."
    )
    brief = synthesize.synthesize(
        ctx.client,
        ctx.conn,
        ctx.config,
        ctx.system_prompt,
        user_message,
        "why",
        max_output_tokens=200,
        now=ctx.now,
    )
    return Reply(f"*Now*\n\n{body}\n\n{brief.text}", called_model=True)


def _why(_argument: str, ctx: Context) -> Reply:
    if ctx.client is None:
        return Reply("`/why` needs the Claude API and this is a dry run.")

    snapshot = collect.quick(ctx.config)
    user_message = synthesize.build_user_message(
        ctx.config,
        snapshot,
        ctx.conn,
        "why",
        previous=db.last_brief(ctx.conn),
        now=ctx.now,
    )
    brief = synthesize.synthesize(
        ctx.client,
        ctx.conn,
        ctx.config,
        ctx.system_prompt,
        user_message,
        "why",
        max_output_tokens=600,
        now=ctx.now,
    )
    return Reply(f"*Why — last 24h*\n\n{brief.text}", called_model=True)


def _deep(argument: str, ctx: Context) -> Reply:
    if not argument:
        return Reply(
            "`/deep` needs a topic. For example:\n"
            "`/deep real yields`\n`/deep central bank demand`\n`/deep the rupee leg`"
        )
    if ctx.client is None:
        return Reply("`/deep` needs the Claude API and this is a dry run.")

    topic = argument[:MAX_DEEP_TOPIC_CHARS]
    snapshot = collect.quick(ctx.config)
    user_message = synthesize.build_user_message(
        ctx.config,
        snapshot,
        ctx.conn,
        "deep",
        previous=db.last_brief(ctx.conn),
        topic=topic,
        now=ctx.now,
    )
    brief = synthesize.synthesize(
        ctx.client,
        ctx.conn,
        ctx.config,
        ctx.system_prompt,
        user_message,
        "deep",
        max_output_tokens=1800,
        now=ctx.now,
    )
    return Reply(f"*Deep dive — {topic}*\n\n{brief.text}", called_model=True)


def _levels(_argument: str, ctx: Context) -> Reply:
    """Computed locally: recent extremes, round numbers, and your own levels."""
    conn, config = ctx.conn, ctx.config

    usd = db.latest(conn, "gold_usd_oz")
    inr = db.latest(conn, "gold_inr_per_10g")

    if usd is None and inr is None:
        return Reply(
            "No stored price history yet, so there are no levels to compute. "
            "Levels appear once the agent has run a few times."
        )

    lines = ["*Levels*", ""]

    if usd and usd.value:
        lines.append(f"Gold: *${usd.value:,.2f}*/oz")
    if inr and inr.value:
        lines.append(f"INR: *₹{inr.value:,.0f}*/10g (derived, all-in)")
    lines.append("")

    for metric, label, fmt in (
        ("gold_usd_oz", "USD/oz", "${:,.2f}"),
        ("gold_inr_per_10g", "INR/10g", "₹{:,.0f}"),
    ):
        extremes = _extremes(conn, metric, config, ctx.now)
        if not extremes:
            continue
        lines.append(f"*{label} range*")
        for window_label, low, high in extremes:
            lines.append(f"  {window_label}: {fmt.format(low)} – {fmt.format(high)}")
        lines.append("")

    if usd and usd.value:
        near = _round_levels(usd.value, step=50)
        lines.append("*USD round numbers*")
        lines.append("  " + " · ".join(f"${level:,.0f}" for level in near))
        lines.append("")

    if inr and inr.value:
        near = _round_levels(inr.value, step=2000)
        lines.append("*INR round numbers*")
        lines.append("  " + " · ".join(f"₹{level:,.0f}" for level in near))
        lines.append("")

    from goldagent.alerts import _all_levels

    own = _all_levels(conn, config.alerts.triggers.inr_price_levels)
    if own:
        lines.append("*Your alert levels* (INR/10g)")
        current = inr.value if inr else None
        for level in sorted(own, key=lambda item: item.value):
            distance = ""
            if current:
                distance = f" — {(level.value / current - 1) * 100:+.1f}% away"
            note = f" ({level.note})" if level.note else ""
            armed = "" if not db.level_already_hit(conn, level.value, level.direction) else " [hit]"
            lines.append(
                f"  ₹{level.value:,.0f} {level.direction}{note}{distance}{armed} · {level.origin}"
            )
    else:
        lines.append("*Your alert levels*: none set. Add one with `/set 118000`.")

    lines.append("")
    lines.append(
        "_Ranges and round numbers are computed from stored history — no interpretation. "
        "Ask `/deep levels` for a read._"
    )
    return Reply("\n".join(lines))


def _set(argument: str, ctx: Context) -> Reply:
    match = re.search(r"-?[\d,]+(?:\.\d+)?", argument.replace(" ", ""))
    if not match:
        return Reply("`/set` needs a price in INR per 10g. For example `/set 118000`.")

    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return Reply(f"Could not read a price from `{argument}`.")

    if not (LEVEL_BAND_INR[0] <= value <= LEVEL_BAND_INR[1]):
        return Reply(
            f"₹{value:,.0f}/10g is outside the plausible band "
            f"(₹{LEVEL_BAND_INR[0]:,.0f}–₹{LEVEL_BAND_INR[1]:,.0f}). "
            f"Levels are per 10 grams, not per gram or per ounce."
        )

    direction = "either"
    lowered = argument.lower()
    if "above" in lowered or "over" in lowered:
        direction = "above"
    elif "below" in lowered or "under" in lowered:
        direction = "below"

    note = re.sub(r"[\d,.]+", "", argument)
    note = re.sub(r"\b(above|over|below|under)\b", "", note, flags=re.IGNORECASE).strip(" -—:")

    added = db.add_user_level(ctx.conn, value, direction, note[:120], ctx.now)
    if not added:
        return Reply(f"₹{value:,.0f}/10g is already on your list. `/levels` shows all of them.")

    current = db.latest(ctx.conn, "gold_inr_per_10g")
    distance = ""
    if current and current.value:
        distance = f" Currently ₹{current.value:,.0f} ({(value / current.value - 1) * 100:+.1f}% away)."

    return Reply(
        f"Level set: *₹{value:,.0f}/10g* ({direction}).{distance}\n\n"
        f"_Stored in state, not in alerts.yaml, so the config file keeps its comments. "
        f"Fires once per crossing, then re-arms after a {0.5}% pullback._"
    )


def _unset(argument: str, ctx: Context) -> Reply:
    match = re.search(r"[\d,]+(?:\.\d+)?", argument.replace(" ", ""))
    if not match:
        return Reply("`/unset` needs the price to remove, e.g. `/unset 118000`.")
    value = float(match.group(0).replace(",", ""))

    if db.remove_user_level(ctx.conn, value):
        return Reply(f"Removed the ₹{value:,.0f}/10g level.")
    return Reply(
        f"No level at ₹{value:,.0f}/10g among the ones you added. Levels written by hand "
        f"in `config/alerts.yaml` have to be removed there."
    )


def _mute(argument: str, ctx: Context) -> Reply:
    cap = ctx.config.telegram.mute.max_duration_hours
    hours = _parse_duration(argument)

    if hours is None:
        return Reply(
            f"`/mute` needs a duration, e.g. `/mute 4h` or `/mute 90m`. "
            f"Maximum {cap:g}h. `/unmute` clears it."
        )
    if hours <= 0:
        return Reply("Duration must be positive. `/unmute` clears an active mute.")

    clamped = min(hours, cap)
    until = ctx.now + timedelta(hours=clamped)
    db.set_mute_until(ctx.conn, until)

    suffix = f" (capped from {hours:g}h by `telegram.mute.max_duration_hours`)" if clamped < hours else ""
    local = until.astimezone(ctx.config.tz)
    return Reply(
        f"Event alerts muted for {clamped:g}h, until *{local:%H:%M on %d %b}* "
        f"{ctx.config.investor.timezone}{suffix}.\n\n"
        f"_Scheduled briefings still arrive — mute only suppresses event alerts._"
    )


def _unmute(_argument: str, ctx: Context) -> Reply:
    if not db.is_muted(ctx.conn, ctx.now):
        return Reply("Alerts are not muted.")
    db.set_mute_until(ctx.conn, None)
    return Reply("Alerts unmuted.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _price_lines(snapshot: Snapshot, config: Config) -> list[str]:
    out: list[str] = []

    gold = snapshot.value("gold_usd_oz") or snapshot.value("gold_future_usd_oz")
    if gold:
        change = _change(snapshot, "gold_usd_oz")
        suffix = f" ({change:+.2f}%)" if change is not None else ""
        out.append(f"Gold: *${gold:,.2f}*/oz{suffix}")

    inr = snapshot.value("gold_inr_per_10g")
    if inr:
        out.append(f"INR: *₹{inr:,.0f}*/10g all-in (derived)")
    ex_gst = snapshot.value("gold_inr_per_10g_ex_gst")
    if ex_gst:
        out.append(f"  ex-GST: ₹{ex_gst:,.0f}/10g")

    usdinr = snapshot.value("usdinr")
    if usdinr:
        change = _change(snapshot, "usdinr")
        suffix = f" ({change:+.2f}%)" if change is not None else ""
        out.append(f"USD/INR: {usdinr:,.4f}{suffix}")

    dxy = snapshot.value("dxy")
    if dxy:
        change = _change(snapshot, "dxy")
        suffix = f" ({change:+.2f}%)" if change is not None else ""
        out.append(f"DXY: {dxy:,.2f}{suffix}")

    ratio = snapshot.value("gold_silver_ratio")
    if ratio:
        out.append(f"Gold/silver: {ratio:,.1f}")

    for note in snapshot.notes():
        if "DIVERGENCE" in note:
            out.append(f"\n⚠️ {note}")

    return out


def _change(snapshot: Snapshot, metric: str) -> float | None:
    for result in snapshot.results.values():
        reading = result.reading(metric)
        if reading and reading.extra.get("change_pct") is not None:
            return float(reading.extra["change_pct"])
    return None


def _extremes(
    conn: sqlite3.Connection, metric: str, config: Config, now: datetime
) -> list[tuple[str, float, float]]:
    out = []
    for days, label in ((7, "7d"), (30, "30d")):
        series = db.daily_series(conn, metric, days, config.tz, now=now)
        values = [value for _, value in series]
        if len(values) >= 2:
            out.append((label, min(values), max(values)))
    return out


def _round_levels(current: float, step: float, count: int = 3) -> list[float]:
    """Psychological levels: the nearest round numbers either side of spot."""
    base = round(current / step) * step
    levels = {base + offset * step for offset in range(-count, count + 1)}
    return sorted(level for level in levels if level > 0)


def _parse_duration(argument: str) -> float | None:
    """Parse `4h`, `90m`, `2`, `1.5h` into hours."""
    text = argument.strip().lower().replace(" ", "")
    if not text:
        return None

    match = re.fullmatch(r"(\d+(?:\.\d+)?)(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)?", text)
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2) or "h"
    if unit.startswith("m"):
        return amount / 60.0
    return amount


def now_utc() -> datetime:
    return datetime.now(UTC)
