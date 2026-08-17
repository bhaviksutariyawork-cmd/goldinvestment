"""India-specific and news collectors.

These are the fragile ones: scraped HTML and third-party feeds. The tests care
less about exact numbers and more about two properties — a layout change degrades
with a readable reason, and no collector ever asserts something it did not read.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from collectors import gdelt, india_etf, india_policy, price_india, rss, sgb, us_calendar


class Response:
    def __init__(self, text: str = "", payload=None):
        self.text = text
        self.content = text.encode()
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_lines(self, decode_unicode=False):
        yield from self.text.splitlines()

    def raise_for_status(self):
        return None

    def close(self):
        return None


def feed(entries):
    return SimpleNamespace(entries=entries)


def entry(title, link, summary="", published=None):
    data = {"title": title, "link": link, "summary": summary}
    if published:
        data["published_parsed"] = published
    return data


# --------------------------------------------------------------------------- #
# IBJA + MCX
# --------------------------------------------------------------------------- #


class TestPriceIndia:
    def test_parses_the_requested_purity(self, monkeypatch, fixture_text):
        html = fixture_text("ibja.html")
        mcx = json.loads(fixture_text("mcx_marketwatch.json"))

        def fake_get(url, **kwargs):
            if "ibjarates" in url:
                return Response(text=html)
            return Response(payload=mcx)

        monkeypatch.setattr("collectors.price_india.get", fake_get)
        result = price_india.collect(purity=995)

        assert result.reading("ibja_inr_per_10g").value == 106420.0
        assert result.reading("ibja_inr_per_10g").extra["purity"] == 995
        assert result.reading("mcx_gold_inr_per_10g").value == 106880.0

    def test_falls_back_to_another_purity_and_says_so(self, monkeypatch):
        html = "<table><tr><td>Gold 999</td><td>1,06,850</td></tr></table>"
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(text=html))

        result = price_india.collect(purity=995)
        assert result.reading("ibja_inr_per_10g").value == 106850.0
        assert result.reading("ibja_inr_per_10g").extra["purity"] == 999
        assert any("995 rate not found" in note for note in result.notes)

    def test_layout_change_degrades_without_inventing_a_price(self, monkeypatch, fixture_text):
        broken = fixture_text("ibja_layout_changed.html")
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(text=broken))

        result = price_india.collect(purity=995)
        assert result.reading("ibja_inr_per_10g") is None
        assert result.reading("mcx_gold_inr_per_10g") is None
        assert any("IBJA scrape failed" in note for note in result.notes)
        # And it must tell the analyst the INR figure is the derived one.
        assert any("derived from USD spot" in note for note in result.notes)

    def test_purity_marker_is_not_mistaken_for_a_price(self, monkeypatch):
        # "995" appears in the row but is not a plausible per-10g rate; the parser
        # must skip it and take the real number.
        html = "<table><tr><td>995</td><td>1,06,420</td></tr></table>"
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(text=html))
        assert price_india.collect(purity=995).reading("ibja_inr_per_10g").value == 106420.0

    def test_implausible_values_are_skipped(self, monkeypatch):
        # A percentage change column must not be read as a rate.
        html = "<table><tr><td>Gold 995</td><td>0.42</td><td>1,06,420</td></tr></table>"
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(text=html))
        assert price_india.collect(purity=995).reading("ibja_inr_per_10g").value == 106420.0

    def test_mcx_picks_the_nearest_expiry_not_the_lexically_first(self, monkeypatch, fixture_text):
        # "05DEC2026" sorts before "05OCT2026" as a string, so a naive sort would
        # pick the December contract. Front-month means nearest expiry: October.
        mcx = json.loads(fixture_text("mcx_marketwatch.json"))
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(payload=mcx))
        reading = price_india.collect().reading("mcx_gold_inr_per_10g")

        assert reading.value == 106880.0
        assert reading.extra["contract"] == "05OCT2026"

    def test_mcx_ignores_silver_and_goldm_contracts(self, monkeypatch, fixture_text):
        mcx = json.loads(fixture_text("mcx_marketwatch.json"))
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(payload=mcx))
        value = price_india.collect().reading("mcx_gold_inr_per_10g").value
        assert value not in (128400.0, 106800.0)

    def test_unparseable_expiry_sorts_last_rather_than_crashing(self, monkeypatch):
        payload = {"d": [
            {"Symbol": "GOLD", "ExpiryDate": "TBD", "LTP": "999999.00"},
            {"Symbol": "GOLD", "ExpiryDate": "05OCT2026", "LTP": "106880.00"},
        ]}
        monkeypatch.setattr("collectors.price_india.get", lambda *a, **k: Response(payload=payload))
        reading = price_india.collect().reading("mcx_gold_inr_per_10g")
        assert reading.extra["contract"] == "05OCT2026"


# --------------------------------------------------------------------------- #
# AMFI gold ETF NAVs
# --------------------------------------------------------------------------- #


class TestIndiaEtf:
    def test_keeps_gold_etfs_and_excludes_fof_silver_and_equity(self, monkeypatch, fixture_text):
        nav = fixture_text("amfi_nav.txt")
        monkeypatch.setattr(
            "collectors.india_etf.session",
            lambda: SimpleNamespace(get=lambda *a, **k: Response(text=nav)),
        )
        result = india_etf.collect()
        reading = result.reading("india_gold_etf_median_nav")

        assert reading.extra["scheme_count"] == 4
        names = " ".join(s["scheme"] for s in reading.extra["sample"])
        assert "Silver" not in names
        assert "Fund of Fund" not in names
        assert "Equity" not in names
        # median of 88.7701, 89.4512, 91.2033, 92.0140
        assert reading.value == pytest.approx((89.4512 + 91.2033) / 2, abs=1e-4)

    def test_says_nav_is_previous_close_not_live(self, monkeypatch, fixture_text):
        nav = fixture_text("amfi_nav.txt")
        monkeypatch.setattr(
            "collectors.india_etf.session",
            lambda: SimpleNamespace(get=lambda *a, **k: Response(text=nav)),
        )
        result = india_etf.collect()
        assert any("timing artefact" in note for note in result.notes)

    def test_no_gold_schemes_degrades(self, monkeypatch):
        body = "Scheme Code;a;b;Some Equity Fund;100.00;15-Aug-2026\n"
        monkeypatch.setattr(
            "collectors.india_etf.session",
            lambda: SimpleNamespace(get=lambda *a, **k: Response(text=body)),
        )
        result = india_etf.collect()
        assert result.stale is True
        assert "no gold ETF schemes" in result.error


# --------------------------------------------------------------------------- #
# Sovereign Gold Bond — the "assume nothing" collector
# --------------------------------------------------------------------------- #


class TestSgb:
    def test_disabled_reports_nothing_and_asserts_nothing(self):
        result = sgb.collect(check_enabled=False)
        assert result.ok
        assert any("disabled" in note for note in result.notes)
        assert not result.headlines

    def test_no_mentions_forbids_claiming_a_tranche_is_coming(self, monkeypatch):
        monkeypatch.setattr("collectors.sgb.get_json", lambda *a, **k: {"articles": []})
        monkeypatch.setattr("collectors.sgb.get", lambda *a, **k: Response(text="<html></html>"))

        result = sgb.collect(check_enabled=True)
        joined = " ".join(result.notes)
        assert "no mentions found" in joined.lower()
        assert "Do NOT state that a tranche is upcoming" in joined
        assert "cadence" in joined

    def test_issuance_mentions_are_reported_without_asserting_a_schedule(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.sgb.get_json",
            lambda *a, **k: {
                "articles": [
                    {
                        "title": "Sovereign Gold Bond series II opens for subscription",
                        "url": "https://economictimes.indiatimes.com/x",
                        "domain": "indiatimes.com",
                    }
                ]
            },
        )
        monkeypatch.setattr("collectors.sgb.get", lambda *a, **k: Response(text="<html></html>"))

        result = sgb.collect(check_enabled=True)
        joined = " ".join(result.notes)
        assert "Read the linked sources" in joined
        assert "do not infer a schedule" in joined
        assert len(result.headlines) == 1

    def test_pause_signals_are_reported(self, monkeypatch):
        monkeypatch.setattr(
            "collectors.sgb.get_json",
            lambda *a, **k: {
                "articles": [
                    {
                        "title": "Sovereign Gold Bond scheme discontinued, says government",
                        "url": "https://reuters.com/x",
                        "domain": "reuters.com",
                    }
                ]
            },
        )
        monkeypatch.setattr("collectors.sgb.get", lambda *a, **k: Response(text="<html></html>"))
        result = sgb.collect(check_enabled=True)
        assert any("paused or discontinued" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# GDELT, RSS, India policy
# --------------------------------------------------------------------------- #


class TestGdelt:
    def test_parses_articles_and_tags_keywords(self, monkeypatch, fixture_text):
        payload = json.loads(fixture_text("gdelt_artlist.json"))
        monkeypatch.setattr("collectors.gdelt.get_json", lambda *a, **k: payload)

        result = gdelt.collect(keywords=["missile", "sanctions", "strike"])
        titles = [h.title for h in result.headlines]

        assert "Missile strike reported near border crossing" in titles
        assert "Malformed row with no url" not in titles  # no url, dropped
        missile = next(h for h in result.headlines if "Missile" in h.title)
        assert "missile" in missile.keywords
        assert missile.domain == "aljazeera.com"

    def test_deduplicates_across_queries(self, monkeypatch, fixture_text):
        payload = json.loads(fixture_text("gdelt_artlist.json"))
        monkeypatch.setattr("collectors.gdelt.get_json", lambda *a, **k: payload)
        result = gdelt.collect(keywords=[])
        urls = [h.url for h in result.headlines]
        assert len(urls) == len(set(urls))

    def test_all_queries_failing_degrades_with_reasons(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("gdelt 502")

        monkeypatch.setattr("collectors.gdelt.get_json", boom)
        result = gdelt.collect(keywords=[])
        assert any("GDELT queries failed" in note for note in result.notes)

    def test_parses_gdelt_timestamp_format(self):
        parsed = gdelt._parse_seendate("20260817T053000Z")
        assert parsed.year == 2026 and parsed.hour == 5 and parsed.minute == 30
        assert gdelt._parse_seendate("nonsense") is None
        assert gdelt._parse_seendate(None) is None


class TestRss:
    def test_keeps_relevant_items_and_drops_the_rest(self, monkeypatch, fixture_text):
        import feedparser

        xml = fixture_text("reuters_rss.xml")
        monkeypatch.setattr("collectors.rss.get", lambda *a, **k: Response(text=xml))
        monkeypatch.setattr(feedparser, "parse", lambda raw: feedparser.parse.__wrapped__(raw)
                            if hasattr(feedparser.parse, "__wrapped__") else _real_parse(raw))

        result = rss.collect(keywords=["strike"])
        titles = [h.title for h in result.headlines]

        assert any("Gold steadies" in t for t in titles)
        assert any("import duty" in t for t in titles)
        # Copper has no relevance term, so it is filtered out.
        assert not any("Copper" in t for t in titles)

    def test_a_dead_feed_does_not_lose_the_others(self, monkeypatch, fixture_text):
        import feedparser

        xml = fixture_text("reuters_rss.xml")
        calls = {"n": 0}

        def flaky_get(url, **kwargs):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("feed 404")
            return Response(text=xml)

        monkeypatch.setattr("collectors.rss.get", flaky_get)
        monkeypatch.setattr(feedparser, "parse", _real_parse)

        result = rss.collect(keywords=[])
        assert result.headlines  # some feeds worked
        assert any("no usable items" in note for note in result.notes)

    def test_every_feed_dead_says_news_is_unavailable_not_quiet(self, monkeypatch):
        import feedparser

        def boom(*args, **kwargs):
            raise RuntimeError("dns failure")

        monkeypatch.setattr("collectors.rss.get", boom)
        monkeypatch.setattr(feedparser, "parse", _real_parse)

        result = rss.collect(keywords=[])
        joined = " ".join(result.notes)
        assert "treat the news leg as unavailable" in joined
        assert "rather than concluding the news was quiet" in joined


class TestIndiaPolicy:
    def test_requires_both_a_gold_term_and_a_policy_term(self):
        assert india_policy._relevant("India raises import duty on gold") is True
        assert india_policy._relevant("India raises import duty on steel") is False
        assert india_policy._relevant("Gold hits a record high") is False
        assert india_policy._relevant("RBI adds to gold reserves") is True
        assert india_policy._relevant("Silver tariff cut announced") is False

    def test_flags_a_possible_duty_change_prominently(self, monkeypatch):
        import feedparser

        monkeypatch.setattr(
            "collectors.india_policy.get_json",
            lambda *a, **k: {
                "articles": [
                    {
                        "title": "Government cuts import duty on gold to 6%",
                        "url": "https://reuters.com/india/x",
                        "domain": "reuters.com",
                    }
                ]
            },
        )
        monkeypatch.setattr(
            "collectors.india_policy.get", lambda *a, **k: Response(text="<rss></rss>")
        )
        monkeypatch.setattr(feedparser, "parse", _real_parse)

        result = india_policy.collect()
        joined = " ".join(result.notes)
        assert "POSSIBLE DUTY/GST CHANGE" in joined
        assert "verify against the linked source" in joined


class TestUsCalendar:
    def test_reports_events_inside_the_horizon(self, monkeypatch, fixture_text):
        from datetime import date

        fomc = fixture_text("fomc_calendar.html")
        cpi = fixture_text("bls_cpi.html")

        def fake_get(url, **kwargs):
            return Response(text=fomc if "federalreserve" in url else cpi)

        monkeypatch.setattr("collectors.us_calendar.get", fake_get)
        result = us_calendar.collect(horizon_days=7, today=date(2026, 8, 17))

        events = [r.extra["event"] for r in result.readings]
        assert "FOMC decision" in events  # 19 Aug is 2 days out
        assert "CPI release" in events  # 19 Aug
        assert any("Next 7 days" in note for note in result.notes)

    def test_never_states_dates_from_memory_when_the_fetch_fails(self, monkeypatch):
        from datetime import date

        def boom(*args, **kwargs):
            raise RuntimeError("fed.gov down")

        monkeypatch.setattr("collectors.us_calendar.get", boom)
        result = us_calendar.collect(horizon_days=7, today=date(2026, 8, 17))

        assert not result.readings
        joined = " ".join(result.notes)
        assert "Do not state event dates from memory" in joined

    def test_no_events_in_horizon_is_stated_positively(self, monkeypatch):
        from datetime import date

        monkeypatch.setattr(
            "collectors.us_calendar.get", lambda *a, **k: Response(text="<html></html>")
        )
        result = us_calendar.collect(horizon_days=7, today=date(2026, 8, 17))
        assert any("No FOMC, CPI or payrolls release scheduled" in n for n in result.notes)


# Keep a reference to the real feedparser.parse, since conftest replaces it.
def _real_parse(raw):
    import feedparser

    return feedparser.FeedParserDict(
        entries=_parse_items(raw if isinstance(raw, bytes) else raw.encode())
    )


def _parse_items(raw: bytes):
    """Minimal RSS item extraction, so tests do not depend on feedparser internals."""
    import re

    text = raw.decode("utf-8", "replace")
    items = []
    for block in re.findall(r"<item>(.*?)</item>", text, re.S):
        # `block` is bound as a default so the closure captures this iteration's
        # value rather than the loop variable.
        def field(name, block=block):
            match = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
            return match.group(1).strip() if match else ""

        items.append(
            {
                "title": field("title"),
                "link": field("link"),
                "summary": field("description"),
            }
        )
    return items
