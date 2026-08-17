# Gold Market Intelligence Agent — design

Information system only. No trading automation, no order placement, no broker
connectivity. Anything that would place or route an order is out of scope by
design, not by omission.

## Runtime shape

No server. Five GitHub Actions workflows on cron; state lives in a SQLite file
committed back to the repo after each run.

| Workflow | Cron (UTC) | IST | Purpose |
|---|---|---|---|
| `brief-morning.yml` | `7 2 * * *` | 07:30 | Overnight brief — the main one. US session closed. |
| `brief-evening.yml` | `7 13 * * 1-5` | 18:30 | Pre-US-open delta since morning. Shorter. |
| `brief-weekly.yml` | `37 4 * * 0` | 10:00 Sun | COT, ETF flows, central banks, week ahead. |
| `watch.yml` | `*/30 0-20 * * *` | 06:00–02:00 | Event alerts **and** Telegram command drain. |
| `tests.yml` | on push / PR | — | pytest + ruff. |

Crons sit at odd minutes because GitHub's scheduler runs 5–20 minutes late,
worst at `:00`. Even so a 07:30 brief can land at 07:40 IST. Platform limit.

`watch.yml` gates on the IST active window in code, so the UTC cron can be
coarse. It also drains `getUpdates` — GitHub Actions has no persistent process,
so Telegram webhooks are impossible and polling is the only option. Worst-case
`/now` latency equals the watch interval.

**Actions budget (private repo, 2,000 free min/month):** 42 watch + 3 brief
runs/day. GitHub rounds every job up to a whole minute, so run *count* is the
cost driver, not runtime — ~48 billed min/day, ~1,460/month. Fits with ~25%
headroom and no room for a second polling workflow. Raise
`alerts.check_interval_minutes` to buy slack.

## Concurrency

The evening brief and a watch tick can overlap and both write `state/gold.db`.
Every workflow shares one `concurrency` group so runs serialise, and the state
commit does fetch → rebase → retry (4 attempts, exponential backoff) before
giving up. On give-up the run still delivers its brief and logs the dropped
write — a lost state commit degrades tomorrow's direction data but never blocks
today's briefing.

State is committed as the binary `.db` with 400-day pruning, keeping it to a few
hundred KB.

## Collector contract

Every module in `collectors/` exposes `collect() -> dict` and **never raises**.
`base.py` supplies a decorator that wraps the body in try/except, applies a
per-call timeout, logs the failure with structured context, and returns
`{"stale": True, "error": "...", "value": None}` on any fault. A dead API
degrades one line of the brief; it cannot kill the run.

The pipeline tolerates partial failure down to `data_quality.min_collectors_ok`
(6). Below that it sends a degraded-brief warning instead of pretending to
have a view.

Two-source cross-checks where they exist: `GC=F` vs `XAUUSD=X` for spot, flagged
above `data_quality.spot_divergence_pct`.

## Synthesis

One Claude call per run (`claude-sonnet-4-6`), given: today's readings for every
metric, the previous 7 days from SQLite so direction is visible, yesterday's
brief text so it can avoid repeating itself, and headlines marked new since the
last run. System prompt is `prompts/analyst.md`.

Cost guard runs *before* the call: the spend ledger in SQLite is checked against
`llm.spend.daily_usd_cap` and `per_run_usd_cap`, and the call is skipped with a
Telegram notice if either would be breached. Token usage and computed cost are
written to `briefs` on every run.

`python -m goldagent.run --dry-run` prints to stdout and sends nothing, so
prompt iteration costs only the API call.

## Alerting

`alerts.py` is a state machine over `alert_log`: evaluate each enabled trigger,
then apply per-type cooldown → daily budget → quiet hours → `/mute` before
sending. Scheduled briefs bypass all four. Every threshold is read from
`config/alerts.yaml`; none are hardcoded.

Conflict escalation requires N distinct *domains* rather than N articles, since
wire copy syndicates and would otherwise self-confirm.

## Failure visibility

Structured JSON logs to stdout. Each workflow has an `if: failure()` step that
posts a short notice to Telegram, so a broken run is visible rather than silent.

## Explicitly not built

Trading automation, order placement, broker connectivity. This is an
information system.
