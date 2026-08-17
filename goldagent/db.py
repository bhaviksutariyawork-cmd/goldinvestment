"""SQLite state.

The file at `state/gold.db` is committed back to the repo after each run, so it is
the agent's whole memory: metric history for direction, headline first-seen times
for novelty detection, the alert ledger for cooldown and budget enforcement, the
Claude spend ledger for the cost guard, and the Telegram update offset.

Conventions
-----------
* Every timestamp is stored as an ISO-8601 string in UTC. Local-day questions
  ("how many alerts today?") convert with the investor timezone at query time.
* `readings` is append-only. `prune()` trims it on a retention window.
* Writes are small and synchronous. Concurrency between overlapping workflow runs
  is handled at the workflow level, not here — see DESIGN.md.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from goldagent.models import AlertEvent, Brief, Headline, Reading, Usage

log = logging.getLogger(__name__)

RETENTION_DAYS = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    metric  TEXT    NOT NULL,
    value   REAL,
    unit    TEXT    NOT NULL DEFAULT '',
    source  TEXT    NOT NULL DEFAULT '',
    ts      TEXT    NOT NULL,
    extra   TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_readings_metric_ts ON readings(metric, ts DESC);

CREATE TABLE IF NOT EXISTS headlines (
    url          TEXT PRIMARY KEY,
    domain       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    first_seen   TEXT NOT NULL,
    keywords     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_headlines_first_seen ON headlines(first_seen DESC);

CREATE TABLE IF NOT EXISTS briefs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mode          TEXT    NOT NULL,
    ts            TEXT    NOT NULL,
    text          TEXT    NOT NULL,
    model         TEXT    NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    degraded      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_briefs_mode_ts ON briefs(mode, ts DESC);

CREATE TABLE IF NOT EXISTS alert_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type TEXT NOT NULL,
    ts           TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_alert_log_type_ts ON alert_log(trigger_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_alert_log_ts ON alert_log(ts DESC);

CREATE TABLE IF NOT EXISTS spend (
    day      TEXT    PRIMARY KEY,
    cost_usd REAL    NOT NULL DEFAULT 0,
    calls    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS level_hits (
    level_value REAL NOT NULL,
    direction   TEXT NOT NULL,
    ts          TEXT NOT NULL,
    PRIMARY KEY (level_value, direction)
);

-- Levels added at runtime via the /set command.
--
-- These live here rather than in config/alerts.yaml on purpose: writing YAML
-- programmatically would strip the file's comments, and that file is meant to
-- stay hand-editable. Levels you commit by hand go in the YAML; levels you add
-- from your phone go here. `/levels` reads both.
CREATE TABLE IF NOT EXISTS user_levels (
    value     REAL NOT NULL PRIMARY KEY,
    direction TEXT NOT NULL DEFAULT 'either',
    note      TEXT NOT NULL DEFAULT '',
    added     TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the state database."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Journal mode DELETE rather than WAL: WAL leaves -wal/-shm sidecar files that
    # would either pollute the commit or be lost on checkout, and we have no
    # concurrent readers to benefit from it.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Readings
# --------------------------------------------------------------------------- #


def record_readings(conn: sqlite3.Connection, readings: Iterable[Reading]) -> int:
    """Append readings. Null-valued readings are skipped — they carry no history."""
    rows = [
        (r.metric, r.value, r.unit, r.source, _iso(r.ts), json.dumps(r.extra, default=str))
        for r in readings
        if r.value is not None
    ]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO readings (metric, value, unit, source, ts, extra) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def _row_to_reading(row: sqlite3.Row) -> Reading:
    try:
        extra = json.loads(row["extra"])
    except (json.JSONDecodeError, TypeError):
        extra = {}
    return Reading(
        metric=row["metric"],
        value=row["value"],
        unit=row["unit"],
        source=row["source"],
        ts=_parse(row["ts"]) or datetime.now(UTC),
        extra=extra,
    )


def latest(conn: sqlite3.Connection, metric: str) -> Reading | None:
    row = conn.execute(
        "SELECT * FROM readings WHERE metric = ? ORDER BY ts DESC LIMIT 1", (metric,)
    ).fetchone()
    return _row_to_reading(row) if row else None


def history(
    conn: sqlite3.Connection,
    metric: str,
    days: int,
    now: datetime | None = None,
    limit: int = 2000,
) -> list[Reading]:
    """Readings for one metric over the trailing `days`, oldest first."""
    now = now or datetime.now(UTC)
    cutoff = _iso(now - timedelta(days=days))
    rows = conn.execute(
        "SELECT * FROM readings WHERE metric = ? AND ts >= ? ORDER BY ts ASC LIMIT ?",
        (metric, cutoff, limit),
    ).fetchall()
    return [_row_to_reading(r) for r in rows]


def daily_series(
    conn: sqlite3.Connection,
    metric: str,
    days: int,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> list[tuple[date, float]]:
    """One value per local day — the last reading of each day, oldest first.

    This is what the model is shown for direction. Intraday noise is not useful
    to it and would burn context.
    """
    readings = history(conn, metric, days, now=now)
    by_day: dict[date, tuple[datetime, float]] = {}
    for r in readings:
        if r.value is None:
            continue
        local_day = r.ts.astimezone(tz).date()
        existing = by_day.get(local_day)
        if existing is None or r.ts > existing[0]:
            by_day[local_day] = (r.ts, r.value)
    return [(day, value) for day, (_, value) in sorted(by_day.items())]


def window_baseline(
    conn: sqlite3.Connection,
    metric: str,
    hours: float,
    now: datetime | None = None,
) -> Reading | None:
    """The comparison point for a rolling-window move.

    Prefers the most recent reading at or before the window start. Falls back to
    the oldest reading inside the window when we have no history that far back —
    which understates the move rather than inventing one, so a fresh database
    cannot manufacture an alert on its first run.
    """
    now = now or datetime.now(UTC)
    cutoff = _iso(now - timedelta(hours=hours))
    row = conn.execute(
        "SELECT * FROM readings WHERE metric = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (metric, cutoff),
    ).fetchone()
    if row:
        return _row_to_reading(row)
    row = conn.execute(
        "SELECT * FROM readings WHERE metric = ? AND ts > ? ORDER BY ts ASC LIMIT 1",
        (metric, cutoff),
    ).fetchone()
    return _row_to_reading(row) if row else None


# --------------------------------------------------------------------------- #
# Headlines
# --------------------------------------------------------------------------- #


def upsert_headlines(
    conn: sqlite3.Connection,
    headlines: Sequence[Headline],
    now: datetime | None = None,
) -> list[Headline]:
    """Record headlines and return only those not seen before.

    Mutates each headline's `first_seen` so callers can distinguish new from
    known without a second query.
    """
    now = now or datetime.now(UTC)
    if not headlines:
        return []

    urls = [h.url for h in headlines]
    placeholders = ",".join("?" * len(urls))
    known = {
        row["url"]: _parse(row["first_seen"])
        for row in conn.execute(
            f"SELECT url, first_seen FROM headlines WHERE url IN ({placeholders})", urls
        )
    }

    fresh: list[Headline] = []
    rows = []
    for h in headlines:
        seen_at = known.get(h.url)
        if seen_at is None:
            h.first_seen = None  # signals "new" to Headline.is_new
            fresh.append(h)
            stored_first_seen = now
        else:
            h.first_seen = seen_at
            stored_first_seen = seen_at
        rows.append(
            (
                h.url,
                h.domain,
                h.title,
                h.source,
                h.summary,
                _iso(h.published_at) if h.published_at else None,
                _iso(stored_first_seen),
                json.dumps(h.keywords),
            )
        )

    conn.executemany(
        """INSERT INTO headlines
               (url, domain, title, source, summary, published_at, first_seen, keywords)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
               title = excluded.title,
               summary = excluded.summary,
               keywords = excluded.keywords""",
        rows,
    )
    conn.commit()
    return fresh


def recent_headlines(
    conn: sqlite3.Connection, hours: float, now: datetime | None = None
) -> list[Headline]:
    """Headlines first seen within the trailing window, newest first."""
    now = now or datetime.now(UTC)
    cutoff = _iso(now - timedelta(hours=hours))
    rows = conn.execute(
        "SELECT * FROM headlines WHERE first_seen >= ? ORDER BY first_seen DESC", (cutoff,)
    ).fetchall()
    out = []
    for row in rows:
        try:
            keywords = json.loads(row["keywords"])
        except (json.JSONDecodeError, TypeError):
            keywords = []
        out.append(
            Headline(
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                source=row["source"],
                summary=row["summary"],
                published_at=_parse(row["published_at"]),
                keywords=keywords,
                first_seen=_parse(row["first_seen"]),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Briefs
# --------------------------------------------------------------------------- #


def record_brief(conn: sqlite3.Connection, brief: Brief) -> None:
    conn.execute(
        """INSERT INTO briefs
               (mode, ts, text, model, input_tokens, output_tokens, cost_usd, degraded)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            brief.mode,
            _iso(brief.ts),
            brief.text,
            brief.usage.model,
            brief.usage.input_tokens,
            brief.usage.output_tokens,
            brief.usage.cost_usd,
            int(brief.degraded),
        ),
    )
    conn.commit()


def last_brief(conn: sqlite3.Connection, mode: str | None = None) -> Brief | None:
    """Most recent brief, optionally restricted to one mode.

    The morning brief is shown the previous *scheduled* brief regardless of mode,
    so it can avoid repeating what the evening note already said.
    """
    if mode:
        row = conn.execute(
            "SELECT * FROM briefs WHERE mode = ? ORDER BY ts DESC LIMIT 1", (mode,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM briefs WHERE mode != 'alert' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return Brief(
        mode=row["mode"],
        text=row["text"],
        ts=_parse(row["ts"]) or datetime.now(UTC),
        degraded=bool(row["degraded"]),
        usage=Usage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            model=row["model"],
        ),
    )


# --------------------------------------------------------------------------- #
# Alert ledger
# --------------------------------------------------------------------------- #


def record_alert(conn: sqlite3.Connection, event: AlertEvent) -> None:
    conn.execute(
        "INSERT INTO alert_log (trigger_type, ts, summary, detail) VALUES (?,?,?,?)",
        (event.trigger_type, _iso(event.ts), event.summary, json.dumps(event.detail, default=str)),
    )
    conn.commit()


def last_fired(conn: sqlite3.Connection, trigger_type: str) -> datetime | None:
    row = conn.execute(
        "SELECT ts FROM alert_log WHERE trigger_type = ? ORDER BY ts DESC LIMIT 1",
        (trigger_type,),
    ).fetchone()
    return _parse(row["ts"]) if row else None


def alerts_on_local_day(
    conn: sqlite3.Connection, tz: ZoneInfo, now: datetime | None = None
) -> int:
    """Count of alerts fired during the investor's current calendar day.

    The day boundary is local, not UTC — the daily cap should reset at midnight
    IST, not at 05:30 IST.
    """
    now = (now or datetime.now(UTC)).astimezone(tz)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM alert_log WHERE ts >= ? AND ts < ?",
        (_iso(day_start), _iso(day_end)),
    ).fetchone()
    return int(row["n"])


def alerts_since(
    conn: sqlite3.Connection, hours: float, now: datetime | None = None
) -> list[AlertEvent]:
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT * FROM alert_log WHERE ts >= ? ORDER BY ts DESC",
        (_iso(now - timedelta(hours=hours)),),
    ).fetchall()
    out = []
    for row in rows:
        try:
            detail = json.loads(row["detail"])
        except (json.JSONDecodeError, TypeError):
            detail = {}
        out.append(
            AlertEvent(
                trigger_type=row["trigger_type"],
                summary=row["summary"],
                detail=detail,
                ts=_parse(row["ts"]) or now,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Price-level hit tracking
# --------------------------------------------------------------------------- #


def level_already_hit(conn: sqlite3.Connection, value: float, direction: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM level_hits WHERE level_value = ? AND direction = ?", (value, direction)
    ).fetchone()
    return row is not None


def mark_level_hit(
    conn: sqlite3.Connection, value: float, direction: str, now: datetime | None = None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO level_hits (level_value, direction, ts) VALUES (?,?,?)",
        (value, direction, _iso(now or datetime.now(UTC))),
    )
    conn.commit()


def clear_level_hit(conn: sqlite3.Connection, value: float) -> None:
    conn.execute("DELETE FROM level_hits WHERE level_value = ?", (value,))
    conn.commit()


# --------------------------------------------------------------------------- #
# User-added price levels (the /set command)
# --------------------------------------------------------------------------- #


def add_user_level(
    conn: sqlite3.Connection,
    value: float,
    direction: str = "either",
    note: str = "",
    now: datetime | None = None,
) -> bool:
    """Add a level. Returns False if that exact value was already set."""
    existing = conn.execute("SELECT 1 FROM user_levels WHERE value = ?", (value,)).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT INTO user_levels (value, direction, note, added) VALUES (?,?,?,?)",
        (value, direction, note, _iso(now or datetime.now(UTC))),
    )
    conn.commit()
    return True


def list_user_levels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM user_levels ORDER BY value ASC").fetchall()
    return [
        {
            "value": row["value"],
            "direction": row["direction"],
            "note": row["note"],
            "added": _parse(row["added"]),
        }
        for row in rows
    ]


def remove_user_level(conn: sqlite3.Connection, value: float) -> bool:
    cursor = conn.execute("DELETE FROM user_levels WHERE value = ?", (value,))
    conn.commit()
    if cursor.rowcount:
        clear_level_hit(conn, value)
        return True
    return False


# --------------------------------------------------------------------------- #
# Spend ledger
# --------------------------------------------------------------------------- #


def _local_day_key(tz: ZoneInfo, now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(tz).date().isoformat()


def spend_today(conn: sqlite3.Connection, tz: ZoneInfo, now: datetime | None = None) -> float:
    row = conn.execute(
        "SELECT cost_usd FROM spend WHERE day = ?", (_local_day_key(tz, now),)
    ).fetchone()
    return float(row["cost_usd"]) if row else 0.0


def add_spend(
    conn: sqlite3.Connection, tz: ZoneInfo, cost_usd: float, now: datetime | None = None
) -> float:
    """Add to today's spend and return the new total."""
    day = _local_day_key(tz, now)
    conn.execute(
        """INSERT INTO spend (day, cost_usd, calls) VALUES (?, ?, 1)
           ON CONFLICT(day) DO UPDATE SET
               cost_usd = cost_usd + excluded.cost_usd,
               calls = calls + 1""",
        (day, cost_usd),
    )
    conn.commit()
    return spend_today(conn, tz, now)


# --------------------------------------------------------------------------- #
# Key/value: mute state and Telegram offset
# --------------------------------------------------------------------------- #


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def kv_delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()


def get_mute_until(conn: sqlite3.Connection) -> datetime | None:
    return _parse(kv_get(conn, "mute_until"))


def set_mute_until(conn: sqlite3.Connection, until: datetime | None) -> None:
    if until is None:
        kv_delete(conn, "mute_until")
    else:
        kv_set(conn, "mute_until", _iso(until))


def is_muted(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    until = get_mute_until(conn)
    if until is None:
        return False
    return (now or datetime.now(UTC)) < until


def get_telegram_offset(conn: sqlite3.Connection) -> int:
    raw = kv_get(conn, "telegram_offset")
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def set_telegram_offset(conn: sqlite3.Connection, offset: int) -> None:
    kv_set(conn, "telegram_offset", str(offset))


# --------------------------------------------------------------------------- #
# Maintenance
# --------------------------------------------------------------------------- #


def prune(
    conn: sqlite3.Connection, days: int = RETENTION_DAYS, now: datetime | None = None
) -> dict[str, int]:
    """Trim history beyond the retention window and reclaim pages.

    Keeps the committed file small enough that a daily binary commit stays cheap.
    """
    cutoff = _iso((now or datetime.now(UTC)) - timedelta(days=days))
    counts = {}
    for table in ("readings", "headlines", "briefs", "alert_log"):
        column = "first_seen" if table == "headlines" else "ts"
        cursor = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))  # noqa: S608
        counts[table] = cursor.rowcount
    cursor = conn.execute("DELETE FROM spend WHERE day < ?", (cutoff[:10],))
    counts["spend"] = cursor.rowcount
    conn.commit()
    if any(counts.values()):
        conn.execute("VACUUM")
        log.info("pruned state", extra={"deleted": counts, "retention_days": days})
    return counts
