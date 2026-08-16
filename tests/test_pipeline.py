"""End-to-end tests: fixtures in at the network boundary, ranked answer out.

Everything between `fetch()` and the JSON response is exercised here —
aggregation across stores, FX, landed cost, relevance filtering, ranking,
recommendation and API serialisation.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import aggregator, fx
from app.models import Market
from app.providers import build_all

FIXTURES = Path(__file__).parent / "fixtures"
RATES = {"AED": 1.0, "USD": 3.6725, "EUR": 3.99}

FIXTURE_FOR_STORE = {
    "amazon_ae": "amazon_search.html",
    "amazon_com": "amazon_search.html",
    "ebay": "ebay_search.html",
    "noon": "noon_search.html",
    "newegg": "newegg_search.html",
    "sharaf_dg": "sharafdg_search.html",
    "aliexpress": "aliexpress_search.html",
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Serve every provider from fixtures and pin FX, so no test touches the network."""

    async def fake_rates(force: bool = False):
        return dict(RATES), "live"

    monkeypatch.setattr(fx, "refresh_rates", fake_rates)
    monkeypatch.setattr(aggregator.fx, "refresh_rates", fake_rates)

    for provider in build_all():
        key = provider.spec.key

        if key == "carrefour_ae":
            payload = json.loads((FIXTURES / "carrefour_search.json").read_text())

            async def carrefour_http(query, limit, _p=provider, _d=payload):
                return _p._parse_json(_d, limit)

            monkeypatch.setattr(type(provider), "search_http", carrefour_http)
            continue

        fixture = FIXTURE_FOR_STORE.get(key)
        if not fixture:
            continue
        html = (FIXTURES / fixture).read_text(encoding="utf-8")

        async def fake_http(self, query, limit, _html=html):
            return self._parse(_html, limit)

        monkeypatch.setattr(type(provider), "search_http", fake_http)


@pytest.mark.asyncio
async def test_search_aggregates_across_stores():
    result = await aggregator.search("sony wh-1000xm5")

    assert result.query == "sony wh-1000xm5"
    assert result.offers
    assert len({o.store for o in result.offers}) > 1, "should span multiple stores"
    assert len(result.stores) == 8


@pytest.mark.asyncio
async def test_every_offer_is_normalised_to_aed():
    result = await aggregator.search("sony wh-1000xm5")
    for offer in result.offers:
        assert offer.price_aed and offer.price_aed > 0
        assert offer.landed_aed and offer.landed_aed >= offer.price_aed
        assert offer.score is not None


@pytest.mark.asyncio
async def test_usd_stores_are_converted_and_taxed():
    """A USD listing must land dearer than its sticker price suggests."""
    result = await aggregator.search("sony wh-1000xm5")
    globals_ = [o for o in result.offers if o.market == Market.GLOBAL]
    assert globals_, "expected global offers in the fixture set"

    for offer in globals_:
        assert offer.currency == "USD"
        assert offer.price_aed == pytest.approx(offer.price * 3.6725, rel=1e-3)
        assert offer.import_fees_aed > 0     # all fixtures exceed AED 300
        assert offer.landed_aed > offer.price_aed


@pytest.mark.asyncio
async def test_local_offers_carry_no_import_fees():
    result = await aggregator.search("sony wh-1000xm5")
    for offer in [o for o in result.offers if o.market == Market.LOCAL]:
        assert offer.import_fees_aed == 0


@pytest.mark.asyncio
async def test_results_are_ranked_best_first():
    result = await aggregator.search("sony wh-1000xm5")
    scores = [o.score for o in result.offers]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_recommendation_is_present_and_explained():
    result = await aggregator.search("sony wh-1000xm5")
    rec = result.recommendation

    assert rec is not None
    assert rec.offer.score == result.offers[0].score
    assert len(rec.rationale) >= 3
    assert rec.confidence in {"high", "medium", "low"}
    assert "Best overall" in rec.offer.badges


@pytest.mark.asyncio
async def test_market_filter_limits_stores():
    local = await aggregator.search("sony wh-1000xm5", markets=[Market.LOCAL])
    assert all(o.market == Market.LOCAL for o in local.offers)
    assert all(s.market == Market.LOCAL for s in local.stores)


@pytest.mark.asyncio
async def test_store_filter_limits_stores():
    result = await aggregator.search("sony wh-1000xm5", stores=["ebay"])
    assert {s.store for s in result.stores} == {"ebay"}


@pytest.mark.asyncio
async def test_empty_query_rejected():
    with pytest.raises(ValueError):
        await aggregator.search("   ")


@pytest.mark.asyncio
async def test_failing_store_is_reported_not_hidden(monkeypatch):
    """A store that breaks must show up as a failure, not vanish silently."""
    from app.providers.ebay import EbayProvider

    async def boom(self, query, limit):
        raise RuntimeError("store returned 503")

    monkeypatch.setattr(EbayProvider, "search_http", boom)
    monkeypatch.setattr(EbayProvider, "search_browser", boom)

    result = await aggregator.search("sony wh-1000xm5")
    ebay_status = next(s for s in result.stores if s.store == "ebay")

    assert ebay_status.ok is False
    assert "503" in (ebay_status.error or "")
    assert result.offers, "other stores must still produce a comparison"
    assert any("returned nothing" in note for note in result.notes)


# ------------------------------------------------------------------ API ----

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_api_search_returns_ranked_json(client):
    response = client.get("/api/search", params={"q": "sony wh-1000xm5"})
    assert response.status_code == 200

    body = response.json()
    assert body["offers"]
    assert body["recommendation"]["offer"]["url"].startswith("http")
    assert body["recommendation"]["rationale"]
    assert body["currency"] == "AED"


def test_api_rejects_short_query(client):
    assert client.get("/api/search", params={"q": "a"}).status_code == 422


def test_api_rejects_bad_market(client):
    assert client.get(
        "/api/search", params={"q": "headphones", "market": "mars"}
    ).status_code == 422


def test_api_stores_lists_registry(client):
    body = client.get("/api/stores").json()
    assert len(body["stores"]) == 8
    assert {s["market"] for s in body["stores"]} == {"local", "global"}
    assert sum(body["weights"].values()) == pytest.approx(1.0)


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Shopping Scout" in response.text


# -------------------------------------------------------------- browser ----

def test_chromium_discovery_returns_an_existing_binary():
    """The browser fallback must survive Playwright's pinned build going missing."""
    import os

    from app.browser import find_chromium

    found = find_chromium()
    if found is not None:
        assert os.path.exists(found), f"discovered non-existent binary {found}"


def test_chromium_discovery_honours_explicit_path(monkeypatch, tmp_path):
    from app.browser import find_chromium

    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("CHROMIUM_PATH", str(fake))
    assert find_chromium() == str(fake)


def test_browser_render_disabled_raises_not_hangs():
    """With the fallback switched off, render() must fail fast."""
    import asyncio

    from app.browser import BrowserUnavailable, render

    with pytest.raises(BrowserUnavailable):
        asyncio.run(render("https://example.com"))


# ------------------------------------------------------------ diagnosis ----

def _status(kind):
    from app.models import Market, StoreStatus
    return StoreStatus(store="s", store_label="S", market=Market.LOCAL, ok=False,
                       error="x", error_kind=kind)


def test_total_failure_blames_the_network_when_nothing_connects():
    from app.aggregator import _diagnose_total_failure

    note = _diagnose_total_failure([_status("unreachable")] * 4)
    assert "network" in note.lower()
    assert "block" not in note.lower(), "must not blame the stores for a local fault"


def test_total_failure_blames_bot_blocking_when_stores_refuse():
    from app.aggregator import _diagnose_total_failure

    note = _diagnose_total_failure([_status("blocked")] * 4)
    assert "SCRAPER_PROXY" in note


def test_total_failure_blames_parsers_when_pages_loaded_fine():
    from app.aggregator import _diagnose_total_failure

    note = _diagnose_total_failure([_status("parse")] * 3)
    assert "parser" in note.lower() and "network" in note.lower()


def test_total_failure_handles_no_recorded_kinds():
    from app.aggregator import _diagnose_total_failure

    assert _diagnose_total_failure([]) == "No store returned results."


@pytest.mark.asyncio
async def test_parse_failure_is_reported_as_such(monkeypatch):
    """A store that answers but yields nothing must be blamed on the parser,
    not reported as a network problem."""
    from app.providers.ebay import EbayProvider

    async def empty(self, query, limit):
        return []

    monkeypatch.setattr(EbayProvider, "search_http", empty)

    result = await aggregator.search("sony wh-1000xm5", stores=["ebay"])
    status = result.stores[0]
    assert status.error_kind == "parse"
    assert "markup" in (status.error or "")
