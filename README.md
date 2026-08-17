# Gold market intelligence agent

High-signal gold briefings delivered to Telegram, for an investor in Ahmedabad
holding gold as a long-horizon portfolio allocation.

Runs entirely on GitHub Actions cron — no server. State lives in a SQLite file
committed back to this repo after each run.

**This is an information system.** It places no orders, connects to no broker, and
issues no recommendations. That boundary is deliberate and enforced in the analyst
prompt (`prompts/analyst.md`), which forbids buy/sell advice, price targets, and
allocation guidance.

---

## What you get

| When | What |
|---|---|
| **07:30 IST daily** | Overnight briefing. The main one — the US session has closed. ≤450 words. |
| **18:30 IST weekdays** | Pre-US-open note, only what changed since morning. Skipped entirely on a flat day. |
| **Sunday 10:00 IST** | Weekly: COT positioning, ETF flows, central bank data, the week's calendar. |
| **Every 30 min, 06:00–02:00 IST** | Event alerts — max 3/day, 6h cooldown per trigger type. Also drains your commands. |

Every briefing reports **INR per 10g alongside USD per oz**, and leads with the
divergence when the two move in opposite directions — which happens whenever the
rupee moves more than the dollar gold price does.

### Commands

Send these to the bot. They are answered on the next scheduled check, so a reply
can take up to 30 minutes.

| Command | Cost | What it does |
|---|---|---|
| `/now` | 1 short model call | Current prices, INR and USD, plus a one-line read |
| `/why` | 1 model call | What drove the last 24 hours |
| `/levels` | free | Recent ranges, round numbers, your alert levels |
| `/set 118000` | free | Add an INR/10g alert level |
| `/unset 118000` | free | Remove a level you added |
| `/mute 4h` | free | Suppress event alerts (briefings still arrive) |
| `/unmute` | free | Clear a mute |
| `/deep <topic>` | 1 longer call | Extended analysis on one driver |
| `/help` | free | The list above |

`/levels` and `/set` are computed locally on purpose — ranges and round numbers are
arithmetic, and paying for a model call to read numbers off a table would be waste.

---

## Setup

Five steps. Budget about fifteen minutes.

### 1. Create the Telegram bot

1. Open Telegram and message [**@BotFather**](https://t.me/BotFather).
2. Send `/newbot`.
3. Give it a display name (anything, e.g. `Gold Desk`).
4. Give it a username ending in `bot` (e.g. `ahmedabad_gold_desk_bot`) — this must
   be globally unique.
5. BotFather replies with a token that looks like
   `8123456789:AAH7x-Kd9fQ2mLpR3sTuVwXyZ0123456789`.

**That token is a password.** Anyone holding it can send messages as your bot and
read anything sent to it. Keep it in GitHub Secrets only — never in a file, never
in a commit.

Optional but worth doing, so the command menu autocompletes in Telegram: send
BotFather `/setcommands`, pick your bot, and paste:

```
now - Current prices, INR and USD, with a one-line read
why - What drove the last 24 hours
levels - Key technical and psychological levels
set - Add an INR/10g alert level
unset - Remove a level you added
mute - Suppress event alerts for a while
unmute - Clear an active mute
deep - Longer analysis on one driver
help - List commands
```

### 2. Get your chat ID

The bot can only message a chat that has messaged it first.

1. Find your new bot in Telegram and send it any message — `hello` will do.
2. Open this URL in a browser, substituting your token:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
3. Find `"chat":{"id":123456789,...}` in the JSON. That number is your chat ID.
   It is positive for a direct message and negative for a group.

If `getUpdates` returns `{"ok":true,"result":[]}`, you have not messaged the bot
yet — do step 1 and reload.

### 3. Get the API keys

| Secret | Where | Cost | Required? |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | [platform.claude.com](https://platform.claude.com/settings/keys) → API keys | Usage-based, capped in config (see below) | **Yes** |
| `TELEGRAM_BOT_TOKEN` | BotFather, step 1 | Free | **Yes** |
| `TELEGRAM_CHAT_ID` | `getUpdates`, step 2 | Free | **Yes** |
| `FRED_API_KEY` | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) | Free | Strongly recommended |

Without `FRED_API_KEY` the agent still runs, but you lose real yields, breakeven
inflation and the fed funds rate — and real yields are the single most important
gold driver. The briefing will say so explicitly rather than guess. Get the key;
it takes two minutes and costs nothing.

No key is needed for Yahoo Finance, GDELT, CFTC, SPDR, AMFI, IBJA or MCX.

### 4. Add the GitHub secrets

In this repository: **Settings → Secrets and variables → Actions → New repository
secret**. Add each of the four above.

Then check **Settings → Actions → General → Workflow permissions** is set to
**Read and write permissions**. The workflows commit the state database back, and
they cannot do that with read-only permissions.

### 5. Verify

Run the offline checks first — they need no secrets and touch no network:

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 231 tests
python -m goldagent.run --mode selftest  # config, prompt, DB, secret presence
```

Then trigger a real run from **Actions → Morning brief → Run workflow**, with
**dry run** ticked. That collects live data and prints the briefing to the workflow
log without sending it or writing state. Read the log, confirm the numbers look
sane, then run it again unticked to get it on your phone.

---

## Tuning

Everything tunable is in [`config/alerts.yaml`](config/alerts.yaml). Edit, commit,
push — the next scheduled run picks it up. No code changes.

The things you are most likely to want to change:

```yaml
alerts:
  budget:
    max_per_day: 3          # hard cap on event alerts per IST day
  triggers:
    gold_move:
      threshold_pct: 1.2    # 4h rolling move that fires an alert
    gold_silver_ratio:
      enabled: false        # off by default — useful signal, noisy alert

llm:
  spend:
    daily_usd_cap: 1.50     # run aborts before calling if this would break
```

`python -m goldagent.run --mode selftest` validates your edit before a scheduled
run does. The test suite also runs it in CI, so a broken config fails the push
rather than a briefing.

### Iterating on the prompt

`prompts/analyst.md` is the system prompt. To change the voice or priorities and
see the effect without paying for it:

```bash
# Dump the exact prompt the model would receive; makes no API call at all.
python -m goldagent.run --mode morning --no-llm

# Make the real call but print instead of sending, and write no state.
python -m goldagent.run --mode morning --dry-run
```

### Cost

At the shipped settings — Sonnet, medium effort, ≤2000 output tokens — a briefing
costs roughly $0.03–0.06. Three scheduled briefings plus a few alerts and commands
lands around $0.20–0.40/day, against a $1.50 cap.

The cap is enforced **before** the request: input tokens are counted, priced at the
rates in config, and checked against both the per-run and daily limits. If a call
would breach either, no call is made. When an alert trips the cap it is still sent
— just as the raw trigger without commentary, because the number matters more than
the analysis.

Token usage and computed cost are written to the `briefs` table on every run.

### GitHub Actions minutes

This repo is private, so Actions bills against the 2,000 free minutes/month. Each
job is rounded **up to a whole minute**, so run count is the cost driver, not
runtime:

| Workflow | Runs/day | ≈ billed min/month |
|---|---|---|
| Watch (`*/30`, 21h window) | 42 | ~1,260 |
| Morning + evening + weekly | ~2.3 | ~140 |
| **Total** | | **~1,400 of 2,000** |

That leaves about 30% headroom and no room for a second polling workflow. If you
want more slack, raise `alerts.check_interval_minutes` and change the cron in
`.github/workflows/watch.yml` to match — the config value is documentation, the
cron is what GitHub actually obeys.

---

## Architecture

```
.github/
  actions/agent/        composite action: setup, run, commit state with retry
  workflows/            five workflows (3 briefs, watch, tests)
collectors/             16 independent collectors + the never-raise base
goldagent/
  run.py                entrypoint and mode dispatch
  collect.py            concurrent collection + derived metrics
  synthesize.py         prompt assembly, Claude call, cost guard
  alerts.py             trigger evaluation and suppression
  commands.py           /now /why /levels /set /mute /deep
  telegram.py           send, chunk, poll
  db.py, config.py, models.py, logging_setup.py
config/alerts.yaml      every threshold
prompts/analyst.md      the system prompt
state/gold.db           committed after each run
tests/                  231 offline tests + captured fixtures
```

`DESIGN.md` covers the runtime shape, the cron/IST mapping, and the concurrency
handling in more detail.

### The collector contract

Every collector returns a `CollectorResult` and **never raises**. A dead API, a
changed page layout, a TLS error — all of it becomes a result flagged stale with
the reason attached, which the briefing states explicitly. A single dead source
degrades one line; it cannot kill the run.

Below `data_quality.min_collectors_ok` (default 6) collectors returning actual
data, the agent sends a short degraded notice instead of a briefing. It does not
spend a model call to speculate past its data.

### Things that are derived, not quoted

The briefing distinguishes these, and so should you when reading it:

- **INR per 10g** is computed from USD spot × USD/INR × the duty stack in config.
  It is not a dealer quote. It is available whenever the price and FX legs work,
  which is why it does not depend on the IBJA scraper.
- **Rate-cut odds** are derived from front-month Fed Funds futures (`ZQ=F`), the
  same instrument CME FedWatch is built on. FedWatch itself is JavaScript-only
  with no free API. The figure tracks FedWatch closely but is not identical, and
  the briefing is instructed to call it futures-implied, never a CME figure.
- **The duty stack compounds**: GST applies to the value *plus* duty and cess, so
  6% + 5% + 3% is 14.33%, not 14%.
- **The physical premium** compares IBJA against the *ex-GST* landed cost, because
  IBJA publishes on an ex-GST basis. A gap wider than 4% is reported as a probably
  stale duty stack rather than a real local premium.

---

## Maintenance

**The scrapers will break.** IBJA, MCX, the Fed calendar and the BLS schedule have
no documented APIs and change layout without notice. When one breaks you will see
it as a named gap in the briefing, not as a wrong number — that is the design — but
you will need to fix the selector. `collectors/price_india.py` and
`collectors/us_calendar.py` are the likely candidates.

The documented JSON endpoints (FRED, CFTC, GDELT, Yahoo, AMFI) should be stable.

**Keep the duty stack current.** `india.duty_stack_fallback` in config is the
arithmetic basis for every INR figure. When the policy collector flags a duty or
GST change, verify it against the linked source and update the config — the
briefing will keep saying "as of \<date\>" until you do, and a wide IBJA premium
is your second warning.

**Sovereign Gold Bonds.** The SGB collector assumes nothing about issuance. It
reports what it finds and, when it finds nothing, says so — it will never tell you
a tranche is coming because a schedule was hardcoded. SGB issuance has been paused
for long stretches; treat any tranche claim as needing verification against RBI
directly.

---

## Out of scope

No trading automation. No order placement. No broker connectivity. Not built, not
stubbed, not planned.
