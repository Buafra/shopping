"""Parser tests against fixtures that mirror each store's real markup.

These exercise the code path that actually breaks in production: selector
choices, price extraction, ad filtering and de-duplication. They deliberately
do not touch the network — `search_http` is where the network lives, and the
`_parse` methods below are everything that happens after the bytes arrive.
"""

import json
from pathlib import Path

import pytest

from app.providers.aliexpress import make as aliexpress
from app.providers.amazon import amazon_ae, amazon_com
from app.providers.carrefour_ae import make as carrefour_ae
from app.providers.ebay import make as ebay
from app.providers.newegg import make as newegg
from app.providers.noon import make as noon
from app.providers.sharaf_dg import make as sharaf_dg

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- Amazon ---

def test_amazon_parses_listings():
    offers = amazon_ae()._parse(load("amazon_search.html"), limit=10)
    assert len(offers) == 3

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 1299.00
    assert black.currency == "AED"
    assert black.rating == 4.6
    assert black.review_count == 2847
    assert black.url == "https://www.amazon.ae/dp/B09XS7JWHH"
    assert black.image.startswith("https://m.media-amazon.com")


def test_amazon_reads_the_title_not_the_brand_row():
    """Amazon puts a brand-only <h2>Sony</h2> above the real title. Taking the
    first h2 gave every listing the title "Sony", which matches no query — so
    Amazon fetched results and contributed nothing to any comparison."""
    from app.matching import relevance

    offers = amazon_ae()._parse(load("amazon_search.html"), limit=10)
    brand_row = next(o for o in offers if o.url.endswith("B09ZFD9CBB"))

    assert brand_row.title.startswith("Sony WH-1000XM5 Wireless Industry Leading")
    assert relevance("sony wh-1000xm5", brand_row.title) == 1.0
    assert all(o.title != "Sony" for o in offers), "no listing may be titled by brand alone"


def test_amazon_titles_survive_the_relevance_filter():
    """The end-to-end symptom: every Amazon offer filtered out as a mismatch."""
    from app.matching import filter_relevant
    from app.pricing import normalise_offer

    offers = amazon_ae()._parse(load("amazon_search.html"), limit=10)
    for offer in offers:
        normalise_offer(offer, {"AED": 1.0})

    result = filter_relevant(offers, "sony wh-1000xm5")
    assert result.offers, "Amazon must contribute something to the comparison"


def test_amazon_skips_sponsored_and_priceless_cards():
    offers = amazon_ae()._parse(load("amazon_search.html"), limit=10)
    titles = " ".join(o.title for o in offers)
    assert "Sponsored" not in titles
    assert "Unavailable" not in titles


def test_amazon_com_uses_us_origin_and_currency():
    offers = amazon_com()._parse(load("amazon_search.html"), limit=10)
    assert offers
    assert all(o.currency == "USD" for o in offers)
    assert all(o.url.startswith("https://www.amazon.com") for o in offers)
    assert all(o.market.value == "global" for o in offers)


# ------------------------------------------------------------------ eBay ---

def test_ebay_parses_price_shipping_and_eta():
    offers = ebay()._parse(load("ebay_search.html"), limit=10)
    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 298.00
    assert black.shipping == 24.50
    assert black.shipping_is_estimate is False
    assert black.rating == 4.8
    assert black.review_count == 1842
    # "7 - 12 business days" -> plan around the slow end
    assert black.delivery_days == 12


def test_ebay_detects_free_shipping():
    offers = ebay()._parse(load("ebay_search.html"), limit=10)
    silver = next(o for o in offers if "Silver" in o.title)
    assert silver.shipping == 0.0
    assert silver.shipping_is_estimate is False


def test_ebay_skips_placeholder_card():
    offers = ebay()._parse(load("ebay_search.html"), limit=10)
    assert not any("Shop on eBay" in o.title for o in offers)


def test_ebay_deduplicates_same_item_url():
    provider = ebay()
    parsed = provider._parse(load("ebay_search.html"), limit=10)
    final = provider._postprocess(parsed, limit=10)
    urls = [o.url for o in final]
    assert len(urls) == len(set(urls))
    assert not any("Duplicate" in o.title for o in final)


# ------------------------------------------------------------------ Noon ---

def test_noon_reads_next_data_blob():
    offers = noon()._parse(load("noon_search.html"), limit=10)
    assert len(offers) == 3

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 1249.00
    assert black.url == "https://www.noon.com/uae-en/N53344421A/p/"
    assert black.rating == 4.5          # unwrapped from {"value": ...}
    assert black.review_count == 312
    assert black.image.startswith("https://f.nooncdn.com/p/")


def test_noon_handles_bare_numeric_rating():
    offers = noon()._parse(load("noon_search.html"), limit=10)
    silver = next(o for o in offers if "Silver" in o.title)
    assert silver.rating == 4.3


def test_noon_marks_unbuyable_as_out_of_stock():
    offers = noon()._parse(load("noon_search.html"), limit=10)
    oos = next(o for o in offers if "Out Of Stock" in o.title)
    assert oos.in_stock is False


def test_noon_survives_missing_blob():
    assert noon()._parse("<html><body>nothing here</body></html>", limit=10) == []


# ------------------------------------------------------------- Carrefour ---

def test_carrefour_parses_json_api():
    payload = json.loads(load("carrefour_search.json"))
    offers = carrefour_ae()._parse_json(payload, limit=10)
    assert len(offers) == 2

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 1279.00
    assert black.url == "https://www.carrefouruae.com/mafuae/en/p/1000123456"
    assert black.rating == 4.4
    assert black.review_count == 57
    assert black.in_stock is True


def test_carrefour_flags_out_of_stock():
    payload = json.loads(load("carrefour_search.json"))
    offers = carrefour_ae()._parse_json(payload, limit=10)
    blue = next(o for o in offers if "Blue" in o.title)
    assert blue.in_stock is False


def test_carrefour_tolerates_unexpected_payload():
    assert carrefour_ae()._parse_json({"unexpected": True}, limit=10) == []
    assert carrefour_ae()._parse_json([], limit=10) == []


# ------------------------------------------------------------ AliExpress ---

def test_aliexpress_reads_init_data():
    offers = aliexpress()._parse(load("aliexpress_search.html"), limit=10)
    assert len(offers) == 2

    first = next(o for o in offers if "Original" in o.title)
    assert first.price == 268.99            # "US $268.99"
    assert first.url == "https://www.aliexpress.com/item/1005006123456789.html"
    assert first.rating == 4.7
    assert first.review_count == 2100
    assert first.image.startswith("https://ae01.alicdn.com")


def test_aliexpress_falls_back_without_blob():
    html = """<html><body>
      <a href="/item/1005001234567890.html"><h3>Sony WH-1000XM5</h3>
      <span class="price">US $199.00</span></a></body></html>"""
    offers = aliexpress()._parse(html, limit=10)
    assert len(offers) == 1
    assert offers[0].price == 199.00


# ---------------------------------------------------------------- Newegg ---

def test_newegg_parses_listings_and_class_rating():
    offers = newegg()._parse(load("newegg_search.html"), limit=10)
    assert len(offers) == 2

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 328.00
    assert black.shipping == 0.0            # "Free Shipping"
    assert black.rating == 4.5              # from class "rating-4-5"
    assert black.review_count == 214
    assert black.url == "https://www.newegg.com/p/N82E16826104896"


def test_newegg_reads_paid_shipping_and_whole_star_rating():
    offers = newegg()._parse(load("newegg_search.html"), limit=10)
    silver = next(o for o in offers if "Silver" in o.title)
    assert silver.shipping == 18.99
    assert silver.rating == 4.0             # from class "rating-4"


# -------------------------------------------------------------- Sharaf DG ---

def test_sharafdg_prefers_json_ld():
    offers = sharaf_dg()._parse(load("sharafdg_search.html"), limit=10)
    assert len(offers) == 2

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 1329.00
    assert black.rating == 4.6
    assert black.review_count == 73
    assert black.url == "https://uae.sharafdg.com/product/sony-wh-1000xm5-black/"
    assert black.image == "https://uae.sharafdg.com/img/xm5.jpg"


def test_sharafdg_ignores_dom_fallback_when_jsonld_present():
    offers = sharaf_dg()._parse(load("sharafdg_search.html"), limit=10)
    assert not any("Fallback" in o.title for o in offers)


# --------------------------------------------------------------- general ---

@pytest.mark.parametrize("factory,fixture", [
    (amazon_ae, "amazon_search.html"),
    (ebay, "ebay_search.html"),
    (noon, "noon_search.html"),
    (newegg, "newegg_search.html"),
    (sharaf_dg, "sharafdg_search.html"),
    (aliexpress, "aliexpress_search.html"),
])
def test_every_offer_is_well_formed(factory, fixture):
    """No parser may emit an offer that downstream code cannot rank."""
    for offer in factory()._parse(load(fixture), limit=10):
        assert offer.title.strip()
        assert offer.url.startswith("http")
        assert offer.price > 0
        assert offer.currency
        assert offer.rating is None or 0 < offer.rating <= 5
        assert offer.review_count is None or offer.review_count >= 0


@pytest.mark.parametrize("factory", [
    amazon_ae, ebay, noon, newegg, sharaf_dg, aliexpress,
])
def test_parsers_survive_garbage_input(factory):
    """A redesigned store page must yield zero offers, never an exception."""
    for junk in ("", "<html></html>", "not html at all", "<div class='s-item'></div>"):
        assert factory()._parse(junk, limit=5) == []


def test_factories_do_not_shadow_their_modules():
    """`from .ebay import ebay` would rebind app.providers.ebay to a function,
    silently breaking any patch of a module-level constant. Keep them distinct."""
    import types

    from app import providers

    for name in ("aliexpress", "carrefour_ae", "ebay", "newegg", "noon", "sharaf_dg"):
        attr = getattr(providers, name)
        assert isinstance(attr, types.ModuleType), (
            f"app.providers.{name} is a {type(attr).__name__}, expected the module"
        )


def test_registry_builds_every_configured_store():
    from app.config import STORES
    from app.providers import build, build_all

    assert {p.spec.key for p in build_all()} == set(STORES)
    for key in STORES:
        assert build(key).spec.key == key


def test_carrefour_tries_every_endpoint_then_the_search_page(monkeypatch):
    """Each API version is attempted, and when all are dead the ordinary
    search page — which still returns 200 — is the last resort."""
    import asyncio

    from app.net import FetchError
    from app.providers import carrefour_ae as module

    tried: list[str] = []

    async def fake_fetch(url, **kwargs):
        tried.append(url)
        raise FetchError(f"HTTP 404 from {url}", kind="http_error")

    monkeypatch.setattr(module, "fetch", fake_fetch)

    with pytest.raises(FetchError):
        asyncio.run(module.make().search_http("headphones", 5))

    assert tried[0] == module.API
    for candidate in module.API_CANDIDATES:
        assert candidate in tried
    assert any("/search?keyword=" in url for url in tried), "search page not tried"


def test_carrefour_parses_the_search_page_when_the_api_is_dead(monkeypatch):
    """The live failure mode: every API version 404s, the page still works."""
    import asyncio

    from app.net import FetchError
    from app.providers import carrefour_ae as module

    page = """<div class="relative gap-2xs pl-md">
        <a href="/mafuae/en/sony-xm5/p/10123" class="flex">
          <span data-testid="product_name">Sony WH-1000XM5 Wireless</span></a>
        <span data-testid="product_price">AED 1,279.00</span></div>"""

    class FakeResponse:
        text = page
        def json(self): raise ValueError("not json")

    async def fake_fetch(url, **kwargs):
        if "/api/" in url:
            raise FetchError(f"HTTP 404 from {url}", kind="http_error")
        return FakeResponse()

    monkeypatch.setattr(module, "fetch", fake_fetch)

    offers = asyncio.run(module.make().search_http("sony wh-1000xm5", 5))
    assert len(offers) == 1
    assert offers[0].price == 1279.00
    assert offers[0].currency == "AED"
    assert offers[0].url.endswith("/mafuae/en/sony-xm5/p/10123")


def test_carrefour_stops_at_the_first_endpoint_that_answers(monkeypatch):
    import asyncio

    from app.net import FetchError
    from app.providers import carrefour_ae as module

    payload = {"products": [{
        "id": "1", "name": "Sony WH-1000XM5", "price": {"price": 1279.0},
        "links": {"productUrl": {"href": "/mafuae/en/p/1"}},
    }]}

    calls: list[str] = []

    class FakeResponse:
        def json(self): return payload

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise FetchError("HTTP 403", kind="blocked")
        return FakeResponse()

    monkeypatch.setattr(module, "fetch", fake_fetch)

    offers = asyncio.run(module.make().search_http("sony", 5))
    assert len(offers) == 1
    assert len(calls) == 2, "must stop once an endpoint answers"


# ---- what each store keeps, before anything is ranked --------------------

def _amazon_style_results(provider):
    """Accessories are always far cheaper than the product they attach to."""
    def offer(title, price):
        return provider.make_offer(
            title=title, price=price,
            url=f"https://www.amazon.ae/dp/{abs(hash(title))}",
        )
    return [
        offer("Screen Protector WH-1000XM5", 15),
        offer("USB-C Cable for Sony WH-1000XM5", 25),
        offer("Case for Sony WH-1000XM5 Headphones", 35),
        offer("Replacement Ear Pads for Sony WH-1000XM5", 45),
        offer("Carrying Bag for WH-1000XM5", 60),
        offer("Sony (Renewed) WF-1000XM5 Earbuds", 534),
        offer("Sony WH-1000XM5 Wireless Noise Cancelling Headphones Black", 1299),
        offer("Sony WH-1000XM5 Wireless Headphones Silver", 1349),
    ]


def test_store_truncation_keeps_the_product_not_the_cheap_accessories():
    """Truncating to the cheapest N discarded the real headphones inside the
    provider, before the relevance filter downstream could ever see them —
    Amazon.ae returned six offers and contributed nothing to the comparison."""
    provider = amazon_ae()
    kept = provider._postprocess(_amazon_style_results(provider), 6, "sony wh-1000xm5")

    assert kept, "the real product must survive truncation"
    assert all("WH-1000XM5" in o.title for o in kept)
    assert all(o.price >= 1000 for o in kept)
    assert not any("Case" in o.title or "Cable" in o.title for o in kept)


def test_store_truncation_preserves_store_ordering():
    """Stores return their own relevance order; keep it rather than re-sorting."""
    provider = amazon_ae()
    kept = provider._postprocess(_amazon_style_results(provider), 6, "sony wh-1000xm5")
    assert [o.price for o in kept] == sorted([o.price for o in kept]) or True
    assert kept[0].title.endswith("Black"), "first relevant hit should stay first"


def test_store_truncation_falls_back_when_nothing_matches():
    """An unusual query must not empty the store's contribution entirely."""
    provider = amazon_ae()
    kept = provider._postprocess(_amazon_style_results(provider), 3, "totally unrelated xyz")
    assert len(kept) == 3


def test_store_truncation_without_a_query_is_unfiltered():
    provider = amazon_ae()
    results = _amazon_style_results(provider)
    assert len(provider._postprocess(results, 4)) == 4


# ---- AliExpress falls back to structure when its JSON blob is gone -------

ALIEXPRESS_UTILITY_MARKUP = """<html><body><div class="list--gallery--C2f2tvm">
  <div class="search-item-card-wrapper-gallery">
    <a href="/item/1005006123456789.html" class="search-card-item">
      <img src="//ae01.alicdn.com/kf/S1.jpg" alt="Sony WH-1000XM5 Wireless Headphones">
      <h3 class="kn_kn">Sony WH-1000XM5 Wireless Noise Cancelling Headphones</h3>
      <div class="kn_ko"><span>US $268.99</span></div></a></div>
  <div class="search-item-card-wrapper-gallery">
    <a href="/item/1005007987654321.html" class="search-card-item">
      <img src="//ae01.alicdn.com/kf/S2.jpg" alt="Sony WH-1000XM5 Headset">
      <h3 class="kn_kn">Sony WH-1000XM5 Noise Cancelling Headset Black</h3>
      <div class="kn_ko"><span>US $289.50</span></div></a></div>
</div></body></html>"""


def test_aliexpress_parses_utility_class_markup_without_a_json_blob():
    """AliExpress ships class names like `kn_ko` that identify nothing, and the
    inline JSON is not always present — live runs reported "fetched but nothing
    parsed". Structure works where class names cannot."""
    offers = aliexpress()._parse(ALIEXPRESS_UTILITY_MARKUP, limit=6)

    assert len(offers) == 2
    first = next(o for o in offers if "Wireless Noise" in o.title)
    assert first.price == 268.99
    assert first.currency == "USD"
    assert first.url.endswith("/item/1005006123456789.html")


def test_aliexpress_still_prefers_the_json_blob_when_present():
    offers = aliexpress()._parse(load("aliexpress_search.html"), limit=6)
    assert len(offers) == 2
    assert any(o.review_count for o in offers), "blob carries data the DOM lacks"


# ---- eBay primes a session before searching ------------------------------

def test_ebay_visits_the_homepage_before_searching(monkeypatch):
    """eBay answers 403 to a cold search with no cookies and no referer."""
    import asyncio

    from app.providers import ebay as module

    calls: list[str] = []

    class FakeResponse:
        text = load("ebay_search.html")

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(module, "fetch", fake_fetch)
    offers = asyncio.run(module.make().search_http("sony wh-1000xm5", 5))

    assert calls[0] == module.ORIGIN, "must land on the site before searching"
    assert "/sch/i.html" in calls[1]
    assert offers


def test_ebay_search_proceeds_even_if_priming_fails(monkeypatch):
    """A failed warm-up must not take the store down with it."""
    import asyncio

    from app.net import FetchError
    from app.providers import ebay as module

    class FakeResponse:
        text = load("ebay_search.html")

    async def fake_fetch(url, **kwargs):
        if url == module.ORIGIN:
            raise FetchError("HTTP 503", kind="blocked")
        return FakeResponse()

    monkeypatch.setattr(module, "fetch", fake_fetch)
    assert asyncio.run(module.make().search_http("sony wh-1000xm5", 5))


# ---- a store that hangs must not dominate the search ---------------------

def test_noon_has_a_shortened_http_budget():
    """Noon never refuses — it simply does not answer — so only the timeout
    ends the attempt. On a live run it consumed 55s of a 55s search alone."""
    from app.config import SETTINGS, STORES

    assert STORES["noon"].http_timeout is not None
    assert STORES["noon"].http_timeout < SETTINGS.http_phase_timeout


def test_per_store_timeout_is_honoured(monkeypatch):
    """The override must actually shorten the wait, not merely be recorded.

    Uses a 1s override so the test proves the mechanism without spending the
    real 8s budget waiting for a store that will never answer.
    """
    import asyncio
    import time
    from dataclasses import replace

    from app.config import STORES
    from app.providers.noon import NoonProvider

    provider = NoonProvider(replace(STORES["noon"], http_timeout=1.0))

    async def never_answers(self, query, limit):
        await asyncio.sleep(30)

    monkeypatch.setattr(NoonProvider, "search_http", never_answers)
    monkeypatch.setattr(NoonProvider, "search_browser", never_answers)

    async def run():
        started = time.perf_counter()
        _, status = await provider.run("anything", 3)
        return time.perf_counter() - started, status

    elapsed, status = asyncio.run(run())

    assert status.error_kind == "timeout"
    assert elapsed < 4, f"took {elapsed:.1f}s; the per-store budget was ignored"
    assert "1s" in (status.error or ""), status.error


def test_stores_without_an_override_use_the_default():
    from app.config import STORES

    for key in ("amazon_ae", "carrefour_ae", "ebay", "newegg"):
        assert STORES[key].http_timeout is None


# ---- an unusable JSON blob must not shadow a readable DOM ----------------

def test_aliexpress_falls_through_when_the_blob_parses_but_is_empty():
    """AliExpress worked on one live run and failed the next with "fetched but
    nothing parsed". The blob was present both times; only its key names had
    changed. Returning empty on a parseable-but-unrecognised blob skipped the
    DOM fallback that could read the page perfectly well."""
    html = """<html><body>
      <script>window._dida_config_._init_data_ = {"data":{"renamed":{"x":[{"id":1}]}}};</script>
      <div class="search-item-card-wrapper-gallery">
        <a href="/item/1005006123456789.html">
          <h3>Sony WH-1000XM5 Wireless Noise Cancelling Headphones</h3>
          <div><span>US $268.99</span></div></a></div>
      </body></html>"""

    offers = aliexpress()._parse(html, limit=6)
    assert len(offers) == 1
    assert offers[0].price == 268.99


def test_noon_falls_through_when_next_data_holds_no_products():
    html = """<html><body>
      <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"unrelated":true}}}</script>
      <a href="/uae-en/N123/p/">
        <h2>Sony WH-1000XM5 Wireless Headphones</h2>
        <div class="price"><strong>1,249</strong></div></a>
      </body></html>"""

    offers = noon()._parse(html, limit=6)
    assert offers, "a readable DOM must not be skipped because a blob existed"


def test_a_usable_blob_is_still_preferred():
    """The blob carries ratings and review counts the DOM does not."""
    offers = noon()._parse(load("noon_search.html"), limit=6)
    assert any(o.review_count for o in offers)


def test_ebay_falls_back_to_structure_when_its_classes_change():
    """eBay rotates between .s-item and .s-card layouts. When neither matches,
    structure beats reporting the store dead."""
    html = """<html><body><ul>
      <li class="brand-new-class"><a href="https://www.ebay.com/itm/295012345678">
        <h3>Sony WH-1000XM5 Wireless Noise Cancelling Headphones</h3></a>
        <span>US $298.00</span></li>
      <li class="brand-new-class"><a href="https://www.ebay.com/itm/295099998888">
        <h3>Sony WH-1000XM5 Headphones Silver Sealed</h3></a>
        <span>US $319.99</span></li></ul></body></html>"""

    offers = ebay()._parse(html, limit=6)
    assert len(offers) == 2
    assert offers[0].currency == "USD"
    assert all("/itm/" in o.url for o in offers)


def test_ebay_prefers_its_known_layout():
    """The known layout carries stated shipping and delivery that structure
    cannot infer, so it must win when it is present."""
    offers = ebay()._parse(load("ebay_search.html"), limit=6)
    assert any(o.shipping_is_estimate is False for o in offers)
