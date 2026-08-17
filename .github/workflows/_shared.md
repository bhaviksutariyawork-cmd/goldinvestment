Workflows in this directory all share:

* `concurrency.group: goldagent-state` with `cancel-in-progress: false`, so runs
  queue instead of racing on `state/gold.db`. Cancelling would drop a briefing.
* `permissions.contents: write`, needed to commit the state file back.
* Odd-minute cron offsets. GitHub's scheduler is busiest on the hour and runs
  5-20 minutes late there; offsetting reduces (but does not eliminate) the drift.
* `workflow_dispatch`, so any mode can be triggered by hand from the Actions tab.

Cron is UTC. IST is UTC+5:30.
