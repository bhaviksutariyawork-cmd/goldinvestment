# State

`gold.db` is the agent's whole memory. Every workflow run commits it back here.

It holds:

| Table | Purpose |
|---|---|
| `readings` | Metric history. Feeds direction in the briefing and the rolling-window alert baselines. Pruned at 400 days. |
| `headlines` | URL → first-seen time. This is how "new since the last run" is determined. |
| `briefs` | Every briefing sent, with token usage and computed cost. |
| `alert_log` | Fired alerts. Enforces the per-trigger cooldown and the daily cap. |
| `spend` | Claude spend per IST day, for the pre-call cost guard. |
| `level_hits` | Which price levels have fired, so a crossing alerts once and re-arms after a pullback. |
| `user_levels` | Levels added with `/set`. They live here rather than in `alerts.yaml` so that file keeps its comments. |
| `kv` | Mute expiry and the Telegram update offset. |

## Why it is committed

There is no server and no external database, so the repository is the only durable
store. The commit is what makes tomorrow's briefing able to say "gold is up 2% on
the week".

`journal_mode=DELETE` rather than WAL: WAL leaves `-wal`/`-shm` sidecar files that
would either pollute the commit or be lost on checkout, and there are no concurrent
readers to benefit from it.

## If it gets corrupted or you want a clean slate

Delete it and let the next run recreate it:

```bash
rm state/gold.db && git commit -am "state: reset" && git push
```

You lose history, so the next few briefings will describe levels without direction
and no rolling-window alert can fire until enough readings accumulate. Nothing
breaks — `window_baseline` returns `None` on an empty database rather than
manufacturing a move from a single data point.
