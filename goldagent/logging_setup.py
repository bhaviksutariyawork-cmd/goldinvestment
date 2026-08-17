"""Structured JSON logging to stdout.

GitHub Actions captures stdout, so JSON lines give us greppable run logs without
any external log sink. Every record carries the run mode and a run id so lines
from overlapping workflows can be told apart.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime

RUN_ID = os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex[:12]

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "mode": self.mode,
            "run_id": RUN_ID,
            "msg": record.getMessage(),
        }
        # Anything passed via logger.info("...", extra={...}) rides along.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _safe(value: object) -> object:
    """Keep the formatter from raising on an unserialisable extra field."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return str(value)


def setup(mode: str, level: str | None = None) -> None:
    """Install the JSON handler. Idempotent — safe to call more than once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(mode))
    root.addHandler(handler)
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))

    # These libraries log a great deal at INFO and none of it is actionable.
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
