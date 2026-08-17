"""Shared fixtures.

Every test in this suite is offline. `no_network` is autouse and replaces the
collectors' HTTP seam with a function that raises, so a test that forgets to mock
fails loudly instead of silently reaching the internet (and passing on a laptop
while failing in CI, or vice versa).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from goldagent import config as config_module
from goldagent import db as db_module
from goldagent.models import CollectorResult, Reading, Snapshot

FIXTURES = Path(__file__).parent / "fixtures"

# A fixed instant for every test: 2026-08-17 11:30 IST, which is inside the alert
# window and outside quiet hours, so gating tests are not accidentally suppressed.
NOW = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def config():
    """The real config/alerts.yaml — tests should catch it drifting from the code."""
    return config_module.load()


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    connection = db_module.connect(tmp_path / "state.db")
    yield connection
    connection.close()


# Names that perform I/O. Collectors do `from collectors.base import get_json`,
# which binds a new name in the collector's own module — so patching
# `collectors.base.get_json` alone does NOT intercept it. Every module that
# imported one has to be patched individually.
IO_NAMES = ("get", "get_json", "session", "quote")


def _io_bearing_modules():
    """Every module under collectors/ plus goldagent modules that reach the network."""
    import importlib
    import pkgutil

    import collectors as collectors_pkg

    modules = [collectors_pkg]
    for info in pkgutil.iter_modules(collectors_pkg.__path__):
        modules.append(importlib.import_module(f"collectors.{info.name}"))
    return modules


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any unmocked outbound HTTP.

    Patches the bound name in every collector module, not just in
    `collectors.base`, so a test that forgets to mock fails loudly instead of
    quietly reaching the internet — which would make the suite pass or fail
    depending on the machine's egress rather than on the code.
    """

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "unmocked network call in a test. Patch the name in the collector's own "
            "module, e.g. monkeypatch.setattr('collectors.fred.get_json', fake)."
        )

    for module in _io_bearing_modules():
        for name in IO_NAMES:
            if hasattr(module, name):
                monkeypatch.setattr(f"{module.__name__}.{name}", forbidden)

    # feedparser fetches on its own if handed a URL rather than bytes.
    import feedparser

    monkeypatch.setattr(feedparser, "parse", lambda *a, **k: forbidden())

    # requests itself, as a backstop for anything the loop above missed.
    import requests

    for name in ("get", "post", "request"):
        monkeypatch.setattr(requests, name, forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)


@pytest.fixture
def fixture_text():
    """Read a captured response body from tests/fixtures/."""

    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return read


# --------------------------------------------------------------------------- #
# Snapshot builders
# --------------------------------------------------------------------------- #


def reading(metric: str, value: float, unit: str = "u", **extra) -> Reading:
    return Reading(metric=metric, value=value, unit=unit, source="test", ts=NOW, extra=extra)


def snapshot_of(**metrics: float) -> Snapshot:
    """Snapshot with one collector holding the given metrics."""
    snap = Snapshot(collected_at=NOW)
    snap.results["test"] = CollectorResult(
        name="test", readings=[reading(m, v) for m, v in metrics.items()]
    )
    return snap


@pytest.fixture
def make_snapshot():
    return snapshot_of


@pytest.fixture
def full_snapshot():
    """A snapshot with enough collectors carrying data to clear the degraded gate."""

    def build(count: int = 8, **metrics: float) -> Snapshot:
        snap = Snapshot(collected_at=NOW)
        items = list(metrics.items()) or [("gold_usd_oz", 3400.0)]
        for index in range(count):
            metric, value = items[index % len(items)]
            snap.results[f"c{index}"] = CollectorResult(
                name=f"c{index}",
                readings=[reading(metric if index == 0 else f"{metric}_{index}", value)],
            )
        # Ensure the primary metrics are present under their real names.
        snap.results["primary"] = CollectorResult(
            name="primary", readings=[reading(m, v) for m, v in items]
        )
        return snap

    return build
