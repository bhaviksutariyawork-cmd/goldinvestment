# Creative Command Center

Tells a solo performance-marketing operator **which creative to kill, which to
scale, which to leave alone, and which category needs new creative** — across
several client Meta ad accounts, without opening Ads Manager.

React + TypeScript · FastAPI · MongoDB. Dark, dense, and built so no decision
is buried behind a click.

---

## Run it

```bash
# API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set CCC_TOKEN_ENCRYPTION_KEY before storing a token
uvicorn app.main:app --reload --port 8000

# a demo client with one of every problem in it
python -m app.seed --drop

# UI
cd ../frontend
npm install
npm run dev                   # http://localhost:5173, /api proxied to :8000
```

```bash
cd backend && pytest          # 95 tests, including the six acceptance criteria
cd frontend && npm run build  # typecheck + production bundle
```

`python -m app.seed` writes a synthetic 90-day account (`Aurelia Jewels`) that
contains a starved winner, a leaking landing page, a fatigued creative, a
confirmed CUT, thin-sample candidates, and 13% untagged spend — enough to drive
every screen before a real access token exists.

---

## The seven data rules, and where each one lives

These are field-tested constraints, not preferences. Each is enforced in
exactly one place, so there is one file to check when you doubt it.

| Rule | Enforced in |
|---|---|
| Revenue is `purchase_roas × amount_spent`. `omni_purchase_values` is never read — it carries a confirmed 100× decimal error. | `core/metrics.py::row_revenue` |
| Purchases gate verdicts, not spend. 30 purchases minimum for any ROAS verdict. | `core/status.py`, `core/constants.py` |
| Rows aggregate by `creative_id`, not `ad_id`. `ad_id` is kept for drill-down and for pause actions. | `core/metrics.py::group_by`, `core/build.py` |
| The trailing 3 days are attribution-incomplete. Every comparison is measured against the settled edge; rank movement compares D-3 with D-10. | `core/windows.py` |
| Snapshots are append-only. A revised day writes a new revision and demotes the old one; no stored measurement is ever overwritten. | `meta/sync.py::_append` |
| `outbound_clicks` is the click denominator, never `link_clicks` or `clicks (all)`. | `meta/normalize.py` |
| Nothing is auto-paused. Every action is a proposal the operator confirms, and `delete` is not a proposable action. | `routers/actions.py` |

`GET /api/rules` serves these — and every threshold — from the same constants
the engine uses, so a number on a card is never a copy of one in a React file.

---

## Screens

**Flag Center** (`/flags`) — the screen to open first thing. Every problem
across every client, grouped red / amber / blue / grey, ranked inside each
group by money at stake. Each card carries the number that fired it, the
threshold it crossed, why that threshold is where it is, and a one-click
proposal. Any flag can be snoozed per entity for 7/14/30 days with a required
reason, logged to `actions_log` — without suppression the screen is noise
inside a week.

**Hierarchy Explorer** (`/hierarchy`) — Campaign → Ad Set → Ads with the same
column set at every level, and a persistent **All Ads** flatten. Position lives
in the URL, so browser back works and a view can be pasted to someone else.
**Delivery share** is the column that matters: at ad level it is the ad's share
of its parent ad set's spend, and it is the only place intra-ad-set
misallocation is visible. Ad-set detail adds the delivery bar coloured by ROAS
against target, the 50-event learning line, and 7-day budget pacing.

**Leaderboard** (`/leaderboard`) — ranked by ROAS among creatives past the
verdict gate, aggregated by `creative_id` so one asset in three ad sets is one
row. Under 30 purchases there is no rank at all: those go to **Testing**,
sorted by cost per outbound click and showing upper-funnel metrics only. The
second tab, **Within Ad Set**, groups by the budget ads actually competed for —
ads in different ad sets never competed, and a global rank across them is
portfolio theatre.

**Tagging** (`/tagging`) — every untagged creative, highest spend first,
multi-select, one tag applied to all. `creative_meta` cannot be derived from
the API (most spend sits on numeric-only ad names like `112-4`), and every
Coverage answer depends on it. Untagged share of spend stays on the dashboard
until it drops under 10%.

**Coverage** (`/coverage`) — the category × angle grid, the concentration HHI
per AOV band, and the testing priority queue that answers what to brief next.
Cells under 5,000 cumulative impressions render as *untested*, visually
distinct from tested-and-failed — treating them alike is how live angles get
written off.

**Creative detail** (`/creative/:account/:id`) — the verdict, why it holds, the
flags against it, per-measure trend charts with the settling days banded rather
than plotted, and every ad set it competed in.

**Settings & sync** (`/settings`) — ad account and token (encrypted at rest,
never returned), per-band targets, manual sync, the sync log with API call
counts, the reconciliation check, and the full action log.

---

## Status cascade

First match wins, applied at `creative_id` level (`core/status.py`):

```
PAUSED        manual, or effective_status is paused
EXCLUDED      non-conversion objective, or zero delivery in the window
INSUFFICIENT  purchases < 30 → verdict from upper-funnel metrics only
LEAKING       LPV / outbound_clicks < 0.60
FATIGUED      frequency >= 2.5 AND 7d ROAS < 0.8 x lifetime ROAS
STARVED       best ROAS in ad set AND delivery share < 25%
CUT           ROAS < 0.7 x target AND purchases >= 30
HOLD          ROAS between 0.7x and 1.0x target
WIN           ROAS >= target AND purchases >= 30
```

Two things about this ordering are deliberate:

**LEAKING sits above CUT**, so a creative with a broken landing page is
diagnosed as a landing-page problem rather than killed as a bad creative.

**`INSUFFICIENT` is not "too early to judge."** It means judgeable on hook and
click, not on ROAS. Those creatives get an upper-funnel verdict — cost per
outbound click against the account's p50 and p75 — because killing a viable
category on a thin ROAS reading is the operator's stated primary risk.

**CTR is a diagnostic column, never a kill trigger.** In the reference account
the top three ROAS performers (4.34, 3.04, 2.00) all sat below the 25th
percentile for outbound CTR: low-CPM broad-reach creative where a low rate is
selectivity, not weakness. Anywhere CTR is tempting, the engine uses **cost per
outbound click** instead.

---

## Meta sync

Per-client settings screen: ad account id, access token, manual sync, last sync
time, error log, API call count. Scheduled every 4 hours; 90-day backfill on
first connect, then a rolling 7-day refresh because attribution keeps revising
recent days.

Insights is pulled at `campaign`, `adset` and `ad` level as separate calls with
`level` set explicitly every time — it does not inherit, and the wrong grain is
silent rather than an error. Ad-level pulls are filtered by explicit `adset.id`
values in an `IN` array, in batches of 25; filtering ad level by `adset.name` is
unreliable, and partial-name filters break on substring collisions ("Ring"
captures "Earring"). On timeout — or a "reduce the amount of data" error — the
client stops retrying and switches to the async report flow: schedule, poll,
fetch. Thumbnails are fetched once per `creative_id` and cached.

`GET /api/sync/{id}/reconcile` sums ad-level spend and purchases against the
campaign-level pull for the same window. They come from different Insights
calls, so agreement is real evidence the grain and the filters are right — run
it before trusting anything else. A gap over 1% usually means an ad-set batch
was missed or a `level` was wrong.

### One addition to the schema in the brief: `reach_windows`

Deduplicated reach **cannot be derived from daily rows**. Meta counts a person
once inside a window; a sum of daily reach counts them once per day. A
frequency built from daily rows is therefore roughly the average *daily*
frequency — it sits near 1.0, and every frequency threshold in the flag catalog
would be permanently unreachable.

So the sync makes a few extra ad-level Insights calls with no `time_increment`,
one per named window (`trailing_7`, `prior_7`, `trailing_30`, `lifetime`), and
stores what Meta returns (`core/reach.py`). Where that figure is unavailable the
engine falls back to the daily sum, which over-states reach and therefore makes
frequency a *lower bound* — the safe direction, since it can delay a fatigue
flag but never invent one. The UI renders those values as `1.35+` and says why
on hover.

---

## Layout

```
backend/app/
  core/          the verdict engine — pure functions, no database
    constants.py   every threshold, in one place
    windows.py     date windows and the settling lag
    metrics.py     aggregation; revenue and the click denominator
    reach.py       deduplicated reach and its fallbacks
    status.py      the status cascade
    flags.py       the flag catalogue and its evaluators
    hierarchy.py   Hierarchy Explorer rows, delivery share, Within Ad Set
    coverage.py    matrix, HHI, testing priority queue
    build.py       rows in, creative views with ranks and verdicts out
  meta/          Marketing API client, normalisation, sync orchestration
  routers/       one module per screen
  service.py     assembly and caching between Mongo and the routers
  sample_data.py the synthetic account
frontend/src/
  components/    primitives and the chart layer
  screens/       one file per screen
  lib/           API client, types, formatters, hooks
```

## Tests

```
tests/test_acceptance.py     the six criteria from the brief, in order
tests/test_status.py         the cascade, including its ordering
tests/test_flags.py          each flag pinned to its trigger
tests/test_metrics.py        the data rules that live in aggregation
tests/test_coverage.py       matrix, HHI, priority queue
tests/test_hierarchy.py      levels, delivery share, Within Ad Set
tests/test_meta_normalize.py ingestion, and the fields deliberately not read
tests/test_end_to_end.py     the whole engine over the demo account
tests/test_api_smoke.py      every endpoint, against an in-memory Mongo
```

Charts were built against the `dataviz` palette method: categorical slots for
series, a diverging blue↔red scale with a neutral midpoint for ROAS against
target, a single-hue blue ramp for the coverage heatmap. Each was checked with
the palette validator against the dark chart surface before use.
