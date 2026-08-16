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


# ------------------------------------------------------- store enable/disable

def test_disabled_stores_is_validated_and_applied():
    """A CAPTCHA-walled store must be switchable off without editing code, and
    a typo must fail loudly rather than silently disabling nothing.

    Exercised through the function rather than by reloading app.config: a
    reload builds a fresh STORES dict, and every module that already imported
    the old one keeps using it, so two registries end up disagreeing about
    which stores exist.
    """
    from app.config import STORES, apply_disabled_stores

    registry = dict(STORES)
    applied = apply_disabled_stores(registry, "sharaf_dg, carrefour_ae")

    assert applied == {"sharaf_dg", "carrefour_ae"}
    assert registry["sharaf_dg"].enabled is False
    assert registry["carrefour_ae"].enabled is False
    assert registry["amazon_ae"].enabled is True
    # The live registry must be untouched by the copy.
    assert STORES["sharaf_dg"].enabled is True

    with pytest.raises(ValueError, match="unknown store"):
        apply_disabled_stores(dict(STORES), "not_a_store")

    assert apply_disabled_stores(dict(STORES), "") == set()


def test_store_deadline_covers_both_phases():
    """The per-store deadline must never be shorter than the phases it holds,
    or the browser fallback is killed before it starts."""
    import importlib

    import app.aggregator as agg
    from app.config import SETTINGS

    importlib.reload(agg)
    assert agg.STORE_DEADLINE >= (
        SETTINGS.http_phase_timeout + SETTINGS.browser_phase_timeout
    )


# ------------------------------------------------- honest contribution ----

@pytest.mark.asyncio
async def test_store_that_contributes_nothing_is_reported_as_such(monkeypatch):
    """A store can fetch six listings and have all six filtered out. Reporting
    only the fetched count makes it look like a success it was not."""
    from app.providers.ebay import EbayProvider

    async def only_accessories(self, query, limit):
        return [
            self.make_offer(title="Carrying Case for Sony WH-1000XM5",
                            url="https://www.ebay.com/itm/1", price=12.0),
            self.make_offer(title="Ear Pads for Sony WH-1000XM5",
                            url="https://www.ebay.com/itm/2", price=9.0),
        ]

    monkeypatch.setattr(EbayProvider, "search_http", only_accessories)

    result = await aggregator.search("sony wh-1000xm5")
    ebay = next(s for s in result.stores if s.store == "ebay")

    assert ebay.offer_count > 0, "it did fetch listings"
    assert ebay.kept_count == 0, "but contributed none"
    assert any("none matched" in note for note in result.notes)


@pytest.mark.asyncio
async def test_kept_count_matches_the_ranked_table():
    """Per-store counts must add up to what the user actually sees."""
    result = await aggregator.search("sony wh-1000xm5")

    from collections import Counter
    shown = Counter(o.store for o in result.offers)
    for status in result.stores:
        assert status.kept_count == shown.get(status.store, 0)


# ------------------------------------------------------- history endpoints ---

def test_search_is_tracked_and_listed(client):
    client.get("/api/search", params={"q": "sony wh-1000xm5"})
    body = client.get("/api/history").json()

    tracked = [s for s in body["searches"] if s["query"] == "sony wh-1000xm5"]
    assert tracked, "a search should appear in the history"
    assert tracked[0]["checks"] >= 1


def test_store_filtered_searches_are_not_tracked(client):
    """Limiting to one store is a diagnostic, not something worth watching."""
    client.get("/api/search", params={"q": "diagnostic only probe", "stores": "ebay"})
    body = client.get("/api/history").json()
    assert not [s for s in body["searches"] if s["query"] == "diagnostic only probe"]


def test_opting_out_of_tracking_is_honoured(client):
    client.get("/api/search", params={"q": "untracked probe", "save_history": "false"})
    body = client.get("/api/history").json()
    assert not [s for s in body["searches"] if s["query"] == "untracked probe"]


def test_recheck_reruns_the_search_and_reports_movement(client):
    client.get("/api/search", params={"q": "sony wh-1000xm5"})
    listed = client.get("/api/history").json()["searches"]
    search_id = next(s["id"] for s in listed if s["query"] == "sony wh-1000xm5")

    response = client.post(f"/api/history/{search_id}/recheck")
    assert response.status_code == 200

    body = response.json()
    assert body["result"]["offers"], "recheck must return fresh results"
    assert body["changes"]["summary"]
    assert body["changes"]["search_id"] == search_id


def test_history_detail_and_delete(client):
    client.get("/api/search", params={"q": "sony wh-1000xm5"})
    listed = client.get("/api/history").json()["searches"]
    search_id = next(s["id"] for s in listed if s["query"] == "sony wh-1000xm5")

    assert client.get(f"/api/history/{search_id}").status_code == 200
    assert client.delete(f"/api/history/{search_id}").status_code == 200
    assert client.get(f"/api/history/{search_id}").status_code == 404


def test_unknown_history_ids_are_404(client):
    assert client.get("/api/history/999999").status_code == 404
    assert client.post("/api/history/999999/recheck").status_code == 404
    assert client.delete("/api/history/999999").status_code == 404


# ------------------------------------------------------------------ proxy ---

# Hosts here must not collide with PROXY_PLACEHOLDERS — "example.com" and
# "user:pass" are exactly the strings an unfilled config contains, and are
# deliberately rejected elsewhere.
@pytest.mark.parametrize("raw,expected", [
    ("http://gate.myproxy.io:8080", {"server": "http://gate.myproxy.io:8080"}),
    ("http://alice:s3cret@gate.myproxy.io:7000",
     {"server": "http://gate.myproxy.io:7000", "username": "alice", "password": "s3cret"}),
    # Proxy passwords routinely contain @ or :, so they arrive percent-encoded.
    ("http://alice:s3%40cret@gate.myproxy.io:7000",
     {"server": "http://gate.myproxy.io:7000", "username": "alice", "password": "s3@cret"}),
    ("", None),
    ("not a url", None),
])
def test_scraper_proxy_is_translated_for_the_browser(raw, expected, monkeypatch):
    """The browser fallback must use the proxy too. Wiring it only into the
    HTTP client leaves the browser going out on the real IP, so the setting
    half-works in a way nothing reports.

    Settings are patched rather than reloaded: reloading app.net rebinds
    FetchError, and providers that imported the original class then stop
    recognising it, which silently misclassifies blocks in later tests.
    """
    from dataclasses import replace

    import app.browser as browser_module

    monkeypatch.setattr(
        browser_module, "SETTINGS", replace(browser_module.SETTINGS, proxy_url=raw or None)
    )
    assert browser_module.proxy_settings() == expected


def test_health_reports_proxy_state_without_leaking_credentials(client):
    body = client.get("/api/health").json()
    assert "proxy" in body
    assert set(body["proxy"]) == {"configured", "server", "authenticated", "used_by"}
    assert "password" not in str(body["proxy"]).lower()


@pytest.mark.parametrize("raw", [
    "http://USER:PASS@gateway.provider.com:7000",
    "http://<user>:<pass>@<your-proxy-host>:<port>",
    "http://username:password@proxy-host:8080",
])
def test_placeholder_proxies_are_ignored_rather_than_attempted(raw, monkeypatch):
    """Pasting the documented example verbatim points every request at a host
    that does not exist. That fails identically to having no internet, takes
    every store down at once, and reads as a catastrophic outage."""
    from dataclasses import replace

    import app.browser as browser_module
    import app.net as net_module

    patched = replace(browser_module.SETTINGS, proxy_url=raw or None)
    monkeypatch.setattr(browser_module, "SETTINGS", patched)
    monkeypatch.setattr(net_module, "SETTINGS", patched)

    assert browser_module.proxy_settings() is None
    assert net_module._usable_proxy() is None


def test_a_real_proxy_is_still_used(monkeypatch):
    from dataclasses import replace

    import app.browser as browser_module
    import app.net as net_module

    patched = replace(
        browser_module.SETTINGS, proxy_url="http://u:p@gate.brightdata.io:22225"
    )
    monkeypatch.setattr(browser_module, "SETTINGS", patched)
    monkeypatch.setattr(net_module, "SETTINGS", patched)

    assert browser_module.proxy_settings()["server"] == "http://gate.brightdata.io:22225"
    assert net_module._usable_proxy()


# --------------------------------------------------- skipping blocked stores ---

@pytest.mark.asyncio
async def test_a_blocked_store_is_recorded_and_then_skipped(monkeypatch):
    """A CAPTCHA is not a transient fault. Retrying it every search spends the
    full timeout budget to be refused again."""
    from app import blocklist
    from app.net import FetchError
    from app.providers.ebay import EbayProvider

    blocklist.clear()

    async def refuse(self, query, limit):
        raise FetchError("HTTP 403 from www.ebay.com", kind="blocked")

    monkeypatch.setattr(EbayProvider, "search_http", refuse)
    monkeypatch.setattr(EbayProvider, "search_browser", refuse)

    first = await aggregator.search("sony wh-1000xm5")
    assert any(s.store == "ebay" for s in first.stores), "tried on the first run"
    assert "ebay" in blocklist.blocked()

    second = await aggregator.search("sony wh-1000xm5")
    assert not any(s.store == "ebay" for s in second.stores), "skipped on the second"
    assert any("blocked us recently" in note for note in second.notes)

    blocklist.clear()


@pytest.mark.asyncio
async def test_naming_a_store_explicitly_overrides_the_skip():
    """Asking for a store by name is a request to try it regardless."""
    from app import blocklist

    blocklist.remember("ebay", "served a bot-detection interstitial")
    try:
        result = await aggregator.search("sony wh-1000xm5", stores=["ebay"])
        assert [s.store for s in result.stores] == ["ebay"]
    finally:
        blocklist.clear()


@pytest.mark.asyncio
async def test_skipping_never_empties_the_comparison():
    """If every store is on the list, an out-of-date record must not silence
    the app entirely — better to try them all than return nothing."""
    from app import blocklist
    from app.config import STORES

    for key in STORES:
        blocklist.remember(key, "blocked")
    try:
        result = await aggregator.search("sony wh-1000xm5")
        assert result.stores, "all stores skipped would leave nothing to compare"
    finally:
        blocklist.clear()


def test_impersonation_is_optional_and_reports_itself():
    """The app must run identically without curl_cffi installed."""
    from app import impersonate

    assert isinstance(impersonate.is_available(), bool)
    assert isinstance(impersonate.enabled(), bool)


def test_impersonated_response_exposes_what_providers_read():
    from app.impersonate import ImpersonatedResponse

    class Raw:
        status_code = 200
        text = "<html>ok</html>"
        headers = {"content-type": "text/html"}
        url = "https://www.noon.com/uae-en/search/"
        http_version = 2

        def json(self):
            return {"ok": True}

    wrapped = ImpersonatedResponse(Raw())
    assert wrapped.status_code == 200
    assert wrapped.text == "<html>ok</html>"
    assert wrapped.json() == {"ok": True}
    assert wrapped.http_version == "HTTP/2"


# --------------------------------------------- explaining an empty store set ---

@pytest.mark.asyncio
async def test_asking_for_a_disabled_store_says_why(monkeypatch):
    """"No stores match the requested filters" is true and useless. The usual
    cause is DISABLED_STORES set in a shell an hour ago and since forgotten.

    The registry entry is patched rather than reloading app.config: a reload
    builds a new STORES dict, and build_all keeps using the one it imported,
    so the test would pass against a registry nothing else can see.
    """
    from dataclasses import replace

    from app.config import STORES

    monkeypatch.setitem(STORES, "noon", replace(STORES["noon"], enabled=False))
    monkeypatch.setenv("DISABLED_STORES", "noon")

    with pytest.raises(ValueError, match="DISABLED_STORES"):
        await aggregator.search("rtx 4070", stores=["noon"])


@pytest.mark.asyncio
async def test_asking_for_an_unknown_store_lists_the_real_ones():
    with pytest.raises(ValueError, match="unknown store"):
        await aggregator.search("rtx 4070", stores=["nosuchstore"])


@pytest.mark.asyncio
async def test_a_store_outside_the_requested_market_is_explained():
    with pytest.raises(ValueError, match="not in the"):
        await aggregator.search("rtx 4070", stores=["newegg"], markets=[Market.LOCAL])


def test_startup_does_not_wait_for_the_fx_feed(monkeypatch):
    """uvicorn binds no socket until startup returns.

    Awaiting the FX fetch there meant a slow or unreachable rates feed held
    the port shut for the whole request timeout — while the launcher had
    already printed the URL. The browser said "connection refused" and the app
    looked dead when it was merely starting.
    """
    import asyncio
    import time

    from app import fx, main

    started = asyncio.Event()

    async def never_finishes():
        started.set()
        await asyncio.sleep(30)
        return {}, "live"

    monkeypatch.setattr(fx, "refresh_rates", never_finishes)

    async def exercise():
        begin = time.perf_counter()
        async with main.lifespan(main.app):
            elapsed = time.perf_counter() - begin
            # The warm-up must be running, but must not have been waited on.
            await asyncio.sleep(0)
            assert started.is_set(), "FX warm-up never started"
            assert elapsed < 1.0, f"startup blocked for {elapsed:.1f}s"

    asyncio.run(exercise())


def test_shutdown_cancels_the_fx_warmup(monkeypatch):
    """A warm-up left running past shutdown is a pending-task warning at best
    and a process that will not exit at worst."""
    import asyncio

    from app import fx, main

    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}, "live"

    monkeypatch.setattr(fx, "refresh_rates", slow)

    async def exercise():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
        assert cancelled.is_set(), "FX warm-up outlived shutdown"

    asyncio.run(exercise())


def test_a_disabled_browser_is_never_launched():
    """The health probe used to start a real Chromium even with the fallback
    switched off, then report it as available — seconds of startup and a
    process to clean up, for a browser nothing was allowed to use.

    The test suite runs with the fallback off (see conftest), so this asserts
    against the real configuration rather than a patched one."""
    import asyncio

    import app.browser as browser_module
    from app.browser import BrowserUnavailable

    assert browser_module.SETTINGS.use_browser_fallback is False

    with pytest.raises(BrowserUnavailable, match="disabled by configuration"):
        asyncio.run(browser_module._get_browser())

    # Nothing was started, so there is nothing to shut down.
    assert browser_module._browser is None
    assert browser_module._playwright is None


def test_health_reports_a_disabled_browser_honestly(client):
    body = client.get("/api/health").json()

    assert body["browser_fallback"]["enabled"] is False
    assert body["browser_fallback"]["ok"] is False
    assert "disabled" in body["browser_fallback"]["detail"]
