"""Thresholds in one place.

Every number the verdict engine leans on lives here so the API can hand the
same values to the UI. A flag card that shows "frequency 2.71 vs 2.50" is
reading its threshold from this module, not from a string in a React file.
"""

# --- Data rules (section 0 of the brief) ------------------------------------

# The trailing 3 days are attribution-incomplete. Nothing that compares ROAS or
# CPA may include them.
SETTLING_DAYS = 3

# Purchases gate verdicts, not spend. A spend threshold is meaningless across
# AOV bands: the same rupees buy four purchases at a 565 AOV and one at 2,800.
MIN_PURCHASES_FOR_VERDICT = 30

# Rank movement compares these two cutoffs, both clear of the settling window.
RANK_MOVEMENT_RECENT_LAG = 3
RANK_MOVEMENT_PRIOR_LAG = 10

# Trailing window used by every "7d" trigger, measured back from the settled edge.
TRAILING_WINDOW_DAYS = 7

# --- Status cascade ---------------------------------------------------------

LEAK_TRANSFER_RATIO = 0.60
FATIGUE_FREQUENCY = 2.5
FATIGUE_ROAS_RATIO = 0.8
STARVED_DELIVERY_SHARE = 0.25
STARVED_MIN_PURCHASES = 20
CUT_ROAS_RATIO = 0.7
HOLD_ROAS_RATIO = 1.0

# --- Flags ------------------------------------------------------------------

SATURATION_REACH_GROWTH = 0.05
SEVERE_FREQUENCY = 4.0
LEAK_MIN_SPEND = 3_000.0
HIGH_CAC_RATIO = 1.5

FREQUENCY_WARN_LOW = 2.0
FREQUENCY_WARN_HIGH = 2.5
CTR_DECAY_RATIO = 0.75
CPM_INFLATION_RATIO = 1.20
UNDERSPEND_RATIO = 0.75
LEARNING_THRESHOLD_EVENTS = 50
HOOK_WORKS_MIN_SPEND = 5_000.0
HOOK_WORKS_MAX_ROAS = 1.0

SCALE_ROAS_RATIO = 1.5
SCALE_MAX_FREQUENCY = 2.0
COVERAGE_CELL_IMPRESSIONS = 5_000
HHI_CONCENTRATION = 0.25

ZERO_DELIVERY_WINDOW_DAYS = 7
SYNC_STALE_HOURS = 8

# Transfer-rate benchmarks by placement, for the Transfer Leak card copy.
TRANSFER_BENCHMARKS = {
    "instagram_feed": 0.92,
    "instagram_reels": 0.82,
    "instagram_stories": 0.83,
}

# Objectives that count as conversion objectives. Anything else is EXCLUDED
# from ROAS verdicts and raises Mixed Objective.
CONVERSION_OBJECTIVES = {
    "OUTCOME_SALES",
    "CONVERSIONS",
    "PRODUCT_CATALOG_SALES",
    "OUTCOME_APP_PROMOTION",
}

# Severity ordering used by the Flag Center.
SEVERITIES = ("red", "amber", "blue", "grey")
