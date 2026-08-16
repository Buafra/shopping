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
    assert len(offers) == 2

    black = next(o for o in offers if "Black" in o.title)
    assert black.price == 1299.00
    assert black.currency == "AED"
    assert black.rating == 4.6
    assert black.review_count == 2847
    assert black.url == "https://www.amazon.ae/dp/B09XS7JWHH"
    assert black.image.startswith("https://m.media-amazon.com")


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
