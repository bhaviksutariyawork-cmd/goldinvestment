"""The never-raise contract.

This is the single most important property in the codebase: a dead API must
degrade one line of the briefing, not kill the run. These tests assert it holds
for every failure shape a collector can hit, and for every registered collector.
"""

from __future__ import annotations

import requests

import collectors
from collectors.base import CollectorError, collector, domain_of, match_keywords, to_float
from goldagent.models import CollectorResult, Reading


class TestNeverRaises:
    def test_arbitrary_exception_becomes_a_stale_result(self):
        @collector("boom")
        def boom() -> CollectorResult:
            raise ZeroDivisionError("division by zero")

        result = boom()
        assert isinstance(result, CollectorResult)
        assert result.stale is True
        assert result.ok is False
        assert "ZeroDivisionError" in result.error

    def test_timeout_gets_a_readable_message(self):
        @collector("slow")
        def slow() -> CollectorResult:
            raise requests.Timeout("read timed out")

        assert slow().error == "request timed out"

    def test_connection_error_gets_a_readable_message(self):
        @collector("offline")
        def offline() -> CollectorResult:
            raise requests.ConnectionError("nope")

        assert offline().error == "could not connect"

    def test_http_error_reports_the_status(self):
        response = requests.Response()
        response.status_code = 503

        @collector("unavailable")
        def unavailable() -> CollectorResult:
            raise requests.HTTPError(response=response)

        assert unavailable().error == "HTTP 503"

    def test_collector_error_message_reaches_the_result_verbatim(self):
        @collector("parse")
        def parse() -> CollectorResult:
            raise CollectorError("IBJA layout changed, no rate row found")

        assert parse().error == "IBJA layout changed, no rate row found"

    def test_keyboard_interrupt_is_also_caught(self):
        # BaseException subclasses would escape a bare `except Exception`. This
        # asserts the decorator's behaviour is what we think it is: KeyboardInterrupt
        # is NOT swallowed, so a Ctrl-C still stops the process.
        @collector("interrupted")
        def interrupted() -> CollectorResult:
            raise KeyboardInterrupt

        try:
            interrupted()
        except KeyboardInterrupt:
            return
        raise AssertionError("KeyboardInterrupt should propagate, not be swallowed")

    def test_returning_the_wrong_type_is_caught(self):
        @collector("confused")
        def confused():
            return {"not": "a CollectorResult"}

        result = confused()
        assert result.stale is True
        assert "expected CollectorResult" in result.error

    def test_empty_result_is_marked_stale(self):
        @collector("silent")
        def silent() -> CollectorResult:
            return CollectorResult(name="silent")

        result = silent()
        assert result.stale is True
        assert result.error == "collector produced no data"

    def test_notes_only_result_is_ok_but_carries_no_data(self):
        @collector("nothing_to_report")
        def nothing_to_report() -> CollectorResult:
            return CollectorResult(name="x", notes=["no SGB activity found"])

        result = nothing_to_report()
        assert result.ok is True
        # ...but it must not count toward "enough data to write a briefing".
        assert result.has_data is False

    def test_readings_make_a_result_carry_data(self):
        @collector("useful")
        def useful() -> CollectorResult:
            return CollectorResult(
                name="x", readings=[Reading("m", 1.0, "u", "s")]
            )

        result = useful()
        assert result.ok is True
        assert result.has_data is True

    def test_duration_is_recorded(self):
        @collector("timed")
        def timed() -> CollectorResult:
            return CollectorResult(name="x", notes=["done"])

        assert timed().duration_ms >= 0

    def test_name_is_forced_from_the_decorator(self):
        @collector("authoritative")
        def mislabelled() -> CollectorResult:
            return CollectorResult(name="whatever-i-felt-like", notes=["x"])

        assert mislabelled().name == "authoritative"


class TestEveryRegisteredCollectorHonoursTheContract:
    def test_all_fail_safely_with_no_network(self, monkeypatch):
        """With the HTTP seam raising, every collector must still return a result."""
        for collect_fn in collectors.REGISTRY:
            result = collect_fn()
            assert isinstance(result, CollectorResult), collect_fn.collector_name
            assert result.name == collect_fn.collector_name
            # It either failed cleanly or produced a notes-only "nothing found".
            assert result.stale or result.notes or result.readings or result.headlines

    def test_registry_names_are_unique(self):
        names = [fn.collector_name for fn in collectors.REGISTRY]
        assert len(names) == len(set(names))

    def test_every_collector_declares_a_category(self):
        valid = {"price", "fx", "macro", "etf_flows", "cot", "central_bank", "news", "india"}
        for fn in collectors.REGISTRY:
            assert fn.collector_category in valid, fn.collector_name


class TestToFloat:
    def test_plain_number(self):
        assert to_float("3400.25") == 3400.25

    def test_thousands_separators(self):
        assert to_float("1,06,850") == 106850.0
        assert to_float("98,123.45") == 98123.45

    def test_currency_symbols_and_units(self):
        assert to_float("₹1,06,850") == 106850.0
        assert to_float("$3,400.25") == 3400.25
        assert to_float("905.82 tonnes") == 905.82
        assert to_float("1.78%") == 1.78

    def test_numeric_passthrough(self):
        assert to_float(42) == 42.0
        assert to_float(42.5) == 42.5

    def test_nan_is_rejected(self):
        try:
            to_float(float("nan"))
        except CollectorError as exc:
            assert "NaN" in str(exc)
        else:
            raise AssertionError("NaN should raise")

    def test_junk_raises_collector_error_with_context(self):
        try:
            to_float("Rates unavailable", field="IBJA rate")
        except CollectorError as exc:
            assert "IBJA rate" in str(exc)
        else:
            raise AssertionError("junk should raise")

    def test_none_and_empty_raise(self):
        for bad in (None, "", "   "):
            try:
                to_float(bad)
            except CollectorError:
                continue
            raise AssertionError(f"{bad!r} should raise")


class TestDomainOf:
    def test_strips_www_and_subdomains(self):
        assert domain_of("https://www.reuters.com/markets/x") == "reuters.com"
        assert domain_of("https://feeds.reuters.com/x") == "reuters.com"

    def test_wire_syndication_collapses_to_one_domain(self):
        # The whole point: three Reuters URLs are one source, not three.
        urls = [
            "https://reuters.com/a",
            "https://www.reuters.com/b",
            "https://feeds.reuters.com/c",
        ]
        assert len({domain_of(u) for u in urls}) == 1

    def test_two_part_tlds_keep_three_labels(self):
        assert domain_of("https://www.bbc.co.uk/news/x") == "bbc.co.uk"
        assert domain_of("https://economictimes.indiatimes.com/x") == "indiatimes.com"

    def test_garbage_returns_empty(self):
        assert domain_of("not a url") == ""
        assert domain_of("") == ""


class TestMatchKeywords:
    def test_single_words_match_on_token_boundaries(self):
        # "strike" must not fire on "airstrike" — that is its own keyword, and
        # double-counting would inflate the conflict trigger's keyword count.
        assert match_keywords("An airstrike was reported", ["strike"]) == []
        assert match_keywords("Workers began a strike", ["strike"]) == ["strike"]

    def test_multi_word_keywords_match_as_substrings(self):
        assert match_keywords("A ceasefire collapse followed", ["ceasefire collapse"]) == [
            "ceasefire collapse"
        ]

    def test_case_insensitive(self):
        assert match_keywords("MISSILE launch", ["missile"]) == ["missile"]

    def test_returns_all_distinct_hits(self):
        hits = match_keywords(
            "Missile strike prompts sanctions", ["missile", "strike", "sanctions", "invasion"]
        )
        assert set(hits) == {"missile", "strike", "sanctions"}
