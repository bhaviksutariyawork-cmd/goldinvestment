"""Entrypoint: `python -m goldagent.run --mode morning [--dry-run]`.

Modes
-----
`morning` / `evening` / `weekly`  Scheduled briefings.
`watch`                           Event alerts and the Telegram command drain.
`commands`                        Command drain only.
`selftest`                        Config, prompt and DB checks with no network.

`--dry-run` prints to stdout and sends nothing, writes nothing, and spends
nothing on Telegram. It still calls Claude unless `--no-llm` is also passed, so
prompt quality can be iterated on cheaply; alert decisions are evaluated but not
committed, so no cooldown is consumed and no budget is spent.

Exit codes: 0 success (including a deliberately skipped run), 1 failure. A
failure posts a short Telegram notice before exiting so a break is visible.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from goldagent import alerts, collect, commands, db, logging_setup, synthesize
from goldagent.config import (
    DEFAULT_DB_PATH,
    Config,
    ConfigError,
    MissingSecret,
    Secrets,
    load,
    load_prompt,
)
from goldagent.models import Brief, Snapshot
from goldagent.telegram import Telegram, failure_notice

log = logging.getLogger(__name__)

SCHEDULED_MODES = ("morning", "evening", "weekly")
ALL_MODES = (*SCHEDULED_MODES, "watch", "commands", "selftest")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m goldagent.run",
        description="Gold market intelligence agent. Information only — places no orders.",
    )
    parser.add_argument("--mode", choices=ALL_MODES, default="morning")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print to stdout, send nothing, write nothing",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the Claude call entirely and dump the assembled prompt (implies --dry-run)",
    )
    parser.add_argument("--config", default=None, help="path to alerts.yaml")
    parser.add_argument("--prompt", default=None, help="path to the analyst system prompt")
    parser.add_argument("--db", default=None, help="path to the state database")
    parser.add_argument(
        "--force",
        action="store_true",
        help="run a scheduled briefing even if today is not one of its configured days",
    )
    parser.add_argument("--log-level", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.no_llm:
        args.dry_run = True

    logging_setup.setup(args.mode, args.log_level)

    try:
        config = load(args.config)
        system_prompt = load_prompt(args.prompt)
    except ConfigError as exc:
        # No Telegram notice here: without config we may not have a chat id, and
        # a config error is visible in the workflow's own failure.
        log.error("configuration invalid", extra={"error": str(exc)})
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 1

    secrets = Secrets()
    db_path = Path(args.db or DEFAULT_DB_PATH)

    if args.mode == "selftest":
        return _selftest(config, system_prompt, db_path)

    conn = db.connect(db_path)
    try:
        return _dispatch(args, config, system_prompt, secrets, conn)
    except Exception as exc:  # noqa: BLE001 — the top-level handler is the point
        log.error("run failed", extra={"mode": args.mode}, exc_info=True)
        _notify_failure(args, config, secrets, exc)
        print(f"Run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _dispatch(
    args: argparse.Namespace,
    config: Config,
    system_prompt: str,
    secrets: Secrets,
    conn,
) -> int:
    now = datetime.now(UTC)

    if args.mode in SCHEDULED_MODES:
        return _run_briefing(args, config, system_prompt, secrets, conn, now)
    if args.mode == "watch":
        return _run_watch(args, config, system_prompt, secrets, conn, now)
    if args.mode == "commands":
        return _run_commands(args, config, system_prompt, secrets, conn, now)
    raise ValueError(f"unhandled mode {args.mode!r}")


# --------------------------------------------------------------------------- #
# Scheduled briefings
# --------------------------------------------------------------------------- #


def _run_briefing(args, config: Config, system_prompt: str, secrets, conn, now) -> int:
    mode = args.mode
    briefing = config.briefing(mode)

    if not briefing.enabled:
        log.info("briefing disabled in config, nothing to do", extra={"mode": mode})
        print(f"{mode} briefing is disabled in config/alerts.yaml.")
        return 0

    local = now.astimezone(config.tz)
    if not briefing.runs_on(local.weekday()) and not args.force:
        # GitHub's cron is UTC and drifts; a workflow can fire on a day the
        # briefing is not configured for. Skipping is correct, not an error.
        log.info(
            "not a configured day for this briefing",
            extra={"mode": mode, "local_day": local.strftime("%a"), "days": ",".join(briefing.days)},
        )
        print(f"{local:%A} is not a configured day for the {mode} briefing ({briefing.days}).")
        return 0

    snapshot = collect.run(config, secrets_fred_key=secrets.fred_api_key)
    if not args.dry_run:
        collect.persist(conn, snapshot)

    degraded = collect.degraded(config, snapshot)

    if degraded:
        text = synthesize.degraded_notice(config, snapshot)
        brief = Brief(mode=mode, text=text, ts=now, degraded=True)
        log.warning(
            "degraded run — sending a notice instead of a briefing",
            extra={
                "with_data": snapshot.data_count,
                "ok": snapshot.ok_count,
                "minimum": config.data_quality.min_collectors_ok,
            },
        )
        return _deliver(args, config, secrets, conn, brief, snapshot)

    if (
        mode == "evening"
        and briefing.only_if_changed
        and _quiet_since_morning(config, conn, snapshot, briefing.change_floor_pct, now)
    ):
        log.info("evening brief skipped: nothing moved past the floor")
        print(
            f"Nothing moved more than {briefing.change_floor_pct}% since this morning; "
            f"evening note skipped (briefings.evening.only_if_changed)."
        )
        return 0

    previous = db.last_brief(conn, mode if mode == "weekly" else None)
    user_message = synthesize.build_user_message(
        config, snapshot, conn, mode, previous=previous, now=now
    )

    if args.no_llm:
        print("=" * 78)
        print(f"SYSTEM PROMPT ({len(system_prompt):,} chars)")
        print("=" * 78)
        print(system_prompt)
        print()
        print("=" * 78)
        print(f"USER MESSAGE ({len(user_message):,} chars)")
        print("=" * 78)
        print(user_message)
        return 0

    client = _anthropic(secrets)
    brief = synthesize.synthesize(
        client,
        conn,
        config,
        system_prompt,
        user_message,
        mode,
        max_output_tokens=config.llm.max_output_tokens,
        now=now,
        degraded=False,
    )

    _warn_if_over_length(brief, briefing.max_words)
    return _deliver(args, config, secrets, conn, brief, snapshot)


def _quiet_since_morning(
    config: Config, conn, snapshot: Snapshot, floor_pct: float, now
) -> bool:
    """True when nothing material moved since the morning briefing.

    Checks gold in both currencies plus the dollar. If we cannot establish a
    baseline we return False — sending a note we did not need is a smaller error
    than silently skipping one the reader wanted.
    """
    morning = db.last_brief(conn, "morning")
    if morning is None:
        return False

    hours = max(1.0, (now - morning.ts).total_seconds() / 3600.0)
    for metric in ("gold_usd_oz", "gold_inr_per_10g", "dxy"):
        current = snapshot.value(metric)
        baseline = db.window_baseline(conn, metric, hours, now)
        if current is None or baseline is None or not baseline.value:
            return False
        if abs((current / baseline.value - 1.0) * 100.0) >= floor_pct:
            return False
    return True


def _warn_if_over_length(brief: Brief, cap: int) -> None:
    words = len(brief.text.split())
    if words > cap * 1.15:
        log.warning(
            "briefing exceeded its word cap",
            extra={"words": words, "cap": cap, "mode": brief.mode},
        )


# --------------------------------------------------------------------------- #
# Watch: alerts plus commands
# --------------------------------------------------------------------------- #


def _run_watch(args, config: Config, system_prompt: str, secrets, conn, now) -> int:
    local_time = now.astimezone(config.tz).timetz().replace(tzinfo=None)
    window = config.alerts.active_window

    if not window.contains(local_time):
        log.info(
            "outside the active window, exiting early",
            extra={"local_time": local_time.strftime("%H:%M"), "window": f"{window.start}-{window.end}"},
        )
        print(f"{local_time:%H:%M} {config.investor.timezone} is outside the alert window.")
        # Commands are still drained: a command sent overnight should be answered
        # at 06:00, not silently dropped.
        return _run_commands(args, config, system_prompt, secrets, conn, now)

    snapshot = collect.quick(config)
    if not args.dry_run:
        collect.persist(conn, snapshot)

    candidates = alerts.evaluate(conn, config, snapshot, now)
    decisions = alerts.gate(conn, config, candidates, now, commit=not args.dry_run)
    firing = [d.event for d in decisions if d.fired]

    if args.dry_run:
        print(f"Candidates: {len(candidates)}")
        for decision in decisions:
            status = "WOULD FIRE" if decision.fired else f"held ({decision.suppressed_by})"
            print(f"  [{status}] {decision.event.trigger_type}: {decision.event.summary}")
        if not candidates:
            print("  (no thresholds breached)")

    if firing and not args.no_llm:
        client = _anthropic(secrets) if not args.dry_run else _anthropic_or_none(secrets)
        for event in firing:
            if client is None:
                print(f"\n[alert, no model] {event.summary}")
                continue
            user_message = synthesize.build_user_message(
                config, snapshot, conn, "alert", alert=event, now=now
            )
            try:
                brief = synthesize.synthesize(
                    client, conn, config, system_prompt, user_message, "alert",
                    max_output_tokens=400, now=now,
                )
            except synthesize.SpendCapExceeded as exc:
                # Send the raw trigger rather than nothing: the number matters
                # more than the commentary.
                log.warning("alert sent unanalysed, spend cap reached", extra={"error": str(exc)})
                brief = Brief(mode="alert", text=f"⚠️ *{event.summary}*\n\n_({exc})_", ts=now)
            _deliver(args, config, secrets, conn, brief, snapshot, record=False)

    _run_commands(args, config, system_prompt, secrets, conn, now)

    if not args.dry_run:
        db.prune(conn)
    return 0


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _run_commands(args, config: Config, system_prompt: str, secrets, conn, now) -> int:
    if not config.telegram.commands_enabled:
        return 0

    try:
        bot = _telegram(config, secrets)
    except MissingSecret as exc:
        if args.dry_run:
            log.info("no Telegram credentials, skipping the command drain in dry run")
            return 0
        raise exc

    offset = db.get_telegram_offset(conn)
    updates = bot.poll(offset=offset)
    if not updates:
        log.info("no pending commands", extra={"offset": offset})
        return 0

    client = _anthropic_or_none(secrets) if not args.no_llm else None
    ctx = commands.Context(
        conn=conn, config=config, client=client, system_prompt=system_prompt, now=now
    )

    highest = offset - 1
    handled = 0
    for update in updates:
        highest = max(highest, update.update_id)

        # Only answer the configured chat. A bot token is a bearer credential and
        # anyone who learns the bot's name can message it.
        if update.chat_id and update.chat_id != bot.chat_id:
            log.warning(
                "ignoring a message from an unexpected chat",
                extra={"chat_id": update.chat_id, "expected": bot.chat_id},
            )
            continue

        if not update.text:
            continue

        reply = commands.dispatch(update.text, ctx)
        if reply is None:
            continue
        handled += 1

        if args.dry_run:
            print(f"\n>>> {update.text}\n{reply.text}")
        else:
            bot.send(reply.text)

    if not args.dry_run and highest >= offset:
        # Advance past everything seen, including messages we chose not to answer,
        # so they are not re-fetched forever.
        db.set_telegram_offset(conn, highest + 1)

    log.info("commands drained", extra={"seen": len(updates), "handled": handled})
    return 0


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #


def _deliver(
    args,
    config: Config,
    secrets,
    conn,
    brief: Brief,
    snapshot: Snapshot,
    *,
    record: bool = True,
) -> int:
    if args.dry_run:
        print()
        print("=" * 78)
        print(f"{brief.mode.upper()} — {len(brief.text.split())} words, "
              f"${brief.usage.cost_usd:.5f}, not sent")
        print("=" * 78)
        print(brief.text)
        print("=" * 78)
        if snapshot.failed:
            print(f"Collectors unavailable: {', '.join(snapshot.failed)}")
        return 0

    bot = _telegram(config, secrets)
    bot.send(brief.text)

    if record:
        db.record_brief(conn, brief)

    log.info(
        "briefing delivered",
        extra={"mode": brief.mode, "words": len(brief.text.split()), "degraded": brief.degraded},
    )
    return 0


def _notify_failure(args, config: Config, secrets, exc: Exception) -> None:
    """Best-effort failure notice. Never raises — we are already failing."""
    if args.dry_run or not config.telegram.failure_notice:
        return
    try:
        import os

        run_url = None
        repo, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
        if repo and run_id:
            run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
        bot = _telegram(config, secrets)
        bot.send(failure_notice(args.mode, f"{type(exc).__name__}: {exc}", run_url=run_url))
    except Exception:  # noqa: BLE001
        log.error("could not post the failure notice", exc_info=True)


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #


def _telegram(config: Config, secrets) -> Telegram:
    return Telegram(
        secrets.telegram_bot_token,
        secrets.telegram_chat_id,
        parse_mode=config.telegram.parse_mode,
        disable_preview=config.telegram.disable_web_page_preview,
    )


def _anthropic(secrets):
    import anthropic

    return anthropic.Anthropic(api_key=secrets.anthropic_api_key)


def _anthropic_or_none(secrets):
    try:
        return _anthropic(secrets)
    except MissingSecret as exc:
        log.info("no Anthropic key available", extra={"error": str(exc)})
        return None


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #


def _selftest(config: Config, system_prompt: str, db_path: Path) -> int:
    """Validate config, prompt and database without touching the network."""
    print("Config and prompt")
    print(f"  config           {config.path}")
    print(f"  version          {config.version}")
    print(f"  timezone         {config.investor.timezone}")
    print(f"  system prompt    {len(system_prompt):,} chars")
    print(f"  model            {config.llm.model} (effort {config.llm.effort}, "
          f"thinking {config.llm.thinking})")
    print(f"  spend caps       ${config.llm.spend.per_run_usd_cap:.2f}/run, "
          f"${config.llm.spend.daily_usd_cap:.2f}/day")
    print(f"  duty stack       {config.india.duty_stack_fallback.effective_pct:.2f}% all-in "
          f"(as of {config.india.duty_stack_fallback.as_of})")

    enabled = [name for name, trigger in config.alerts.triggers.items() if trigger.enabled]
    disabled = [name for name, trigger in config.alerts.triggers.items() if not trigger.enabled]
    print(f"  triggers on      {', '.join(enabled)}")
    print(f"  triggers off     {', '.join(disabled) or 'none'}")

    print("\nDatabase")
    conn = db.connect(db_path)
    try:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        print(f"  path             {db_path}")
        print(f"  tables           {', '.join(tables)}")
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
            for table in ("readings", "headlines", "briefs", "alert_log", "user_levels")
        }
        print(f"  rows             {counts}")
        print(f"  spend today      ${db.spend_today(conn, config.tz):.5f}")
        muted = db.get_mute_until(conn)
        print(f"  muted until      {muted.astimezone(config.tz) if muted else 'not muted'}")
    finally:
        conn.close()

    print("\nSecrets (presence only, values never printed)")
    secrets = Secrets()
    for name, required in (
        ("ANTHROPIC_API_KEY", True),
        ("TELEGRAM_BOT_TOKEN", True),
        ("TELEGRAM_CHAT_ID", True),
        ("FRED_API_KEY", False),
    ):
        present = secrets.get(name) is not None
        mark = "set" if present else ("MISSING" if required else "not set (optional)")
        print(f"  {name:20s} {mark}")

    print("\nCollectors")
    import collectors

    print(f"  registered       {len(collectors.REGISTRY)}")
    print(f"  minimum to brief {config.data_quality.min_collectors_ok}")

    print("\nSelftest passed — no network calls were made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
