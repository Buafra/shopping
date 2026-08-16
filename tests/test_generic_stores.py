"""Config-driven stores.

The point of these is that adding a shop costs an entry in `config.STORES` and
no code at all — so the machinery that makes that true is what gets tested:
finding the search page, recognising product links, and reading cards by shape.
"""

import pytest

from app.category import looks_like_pc_part, store_matches_query
from app.config import STORES
from app.providers import FACTORIES, build
from app.providers.generic import GenericProvider
from app.structural import guess_product_path

# A storefront in the style most shops actually use: utility CSS, no semantic
# class names, product links repeating under a price.
SHOP_PAGE = """
<html><head><title>Search: rtx 4070</title></head><body>
  <div class="grid">
    <div class="rounded p-2">
      <a href="/products/gigabyte-rtx-4070-gaming-oc">
        <img src="/img/1.jpg" alt="Gigabyte RTX 4070 Gaming OC 12G">
      </a>
      <h3>Gigabyte GeForce RTX 4070 Gaming OC 12G</h3>
      <span class="text-lg">AED 2,099.00</span>
    </div>
    <div class="rounded p-2">
      <a href="/products/msi-rtx-4070-ventus-2x">
        <img src="/img/2.jpg" alt="MSI RTX 4070 VENTUS 2X">
      </a>
      <h3>MSI GeForce RTX 4070 VENTUS 2X E 12G OC</h3>
      <span class="text-lg">AED 1,949.00</span>
    </div>
    <div class="rounded p-2">
      <a href="/products/asus-dual-rtx-4070-evo">
        <img src="/img/3.jpg" alt="ASUS Dual RTX 4070 EVO">
      </a>
      <h3>ASUS Dual GeForce RTX 4070 EVO OC 12GB</h3>
      <span class="text-lg">AED 2,249.00</span>
    </div>
  </div>
</body></html>
"""


def provider(key="microless"):
    return build(key)


# ---- finding the product links -------------------------------------------

def test_product_links_are_discovered_without_being_configured():
    """A new store should not require reading its HTML first."""
    assert guess_product_path(SHOP_PAGE) == "/products/"


def test_navigation_links_do_not_look_like_products():
    """Only links sitting next to a price count, so menus and footers are out."""
    page = """
    <html><body>
      <nav><a href="/about/us">About</a><a href="/help/faq">Help</a></nav>
      <div><a href="/p/thing-one">Thing One Widget</a><span>AED 99.00</span></div>
      <div><a href="/p/thing-two">Thing Two Widget</a><span>AED 89.00</span></div>
    </body></html>
    """
    assert guess_product_path(page) == "/p/"


def test_a_page_with_no_prices_yields_no_pattern():
    """A bot wall or a JS shell has links but no prices — say so, rather than
    inventing a pattern and reporting zero products as a parser fault."""
    assert guess_product_path("<html><body><a href='/x/y'>Hi</a></body></html>") is None
    assert guess_product_path("") is None


# ---- parsing --------------------------------------------------------------

def test_a_configured_store_parses_a_generic_shop_page():
    offers = provider()._parse(SHOP_PAGE, limit=10)

    assert len(offers) == 3
    cheapest = min(offers, key=lambda o: o.price)
    assert cheapest.price == 1949.00
    assert "VENTUS" in cheapest.title
    assert cheapest.url.startswith("https://uae.microless.com/products/")
    assert cheapest.store == "microless"
    assert cheapest.currency == "AED"


def test_the_page_currency_wins_over_the_configured_default():
    """A UK store quoting GBP must not be read as its configured currency by
    accident — and a store that localises by IP quotes whatever it likes."""
    page = SHOP_PAGE.replace("AED", "£").replace("£ ", "£")
    offers = provider("scan_uk")._parse(page, limit=10)

    assert offers, "structural parse found nothing"
    assert {o.currency for o in offers} == {"GBP"}


def test_an_empty_page_parses_to_nothing_rather_than_raising():
    assert provider()._parse("<html><body></body></html>", limit=10) == []


# ---- search URLs ----------------------------------------------------------

def test_a_store_without_its_own_search_url_tries_the_common_ones():
    """Most shops run Shopify, Magento or WooCommerce; between them the
    conventional search paths cover the long tail without configuration."""
    urls = provider("emax").search_urls("rtx 4070")

    assert urls, "no candidate search URLs generated"
    assert all("rtx+4070" in u for u in urls)
    assert len({u.split("?")[0] for u in urls}) > 1, "candidates must differ"


def test_a_configured_search_url_is_used_verbatim():
    urls = provider("overclockers_uk").search_urls("rtx 4070")
    assert urls[0] == "https://www.overclockers.co.uk/search?sSearch=rtx+4070"


def test_candidate_urls_are_capped():
    """Probing is not free: each miss costs a round trip out of the store's
    budget, and a store that answers nothing must not consume the whole run."""
    from app.providers.generic import MAX_CANDIDATE_URLS

    assert len(provider("emax").search_urls("x")) <= MAX_CANDIDATE_URLS


def test_a_store_with_no_origin_is_rejected_at_construction():
    from dataclasses import replace

    with pytest.raises(ValueError, match="origin or a search URL"):
        GenericProvider(replace(STORES["microless"], origin=None, search_urls=()))


# ---- the registry ---------------------------------------------------------

def test_every_configured_store_can_be_built():
    for key in STORES:
        assert key in FACTORIES, f"{key} has no provider"
        assert build(key).spec.key == key


def test_config_driven_stores_need_no_module():
    """The whole point: these have configuration and nothing else."""
    for key in ("microless", "emax", "jumbo_ae", "bhphoto",
                "overclockers_uk", "scan_uk", "alternate_de"):
        assert isinstance(build(key), GenericProvider)


# ---- when specialists join a search ---------------------------------------

@pytest.mark.parametrize("query", [
    "rtx 4070", "RTX4070", "ryzen 7 7800x3d", "core i5-12400f",
    "b650 motherboard am5", "samsung 990 pro 2tb nvme", "graphics card",
    "corsair ddr5 32gb", "850w power supply", "noctua cpu cooler",
    "radeon rx 7900 xtx", "intel arc a770",
])
def test_component_searches_are_recognised(query):
    assert looks_like_pc_part(query)


@pytest.mark.parametrize("query", [
    "sony wh-1000xm5", "air fryer", "iphone 15 pro", "nike running shoes",
    "kettle", "ramen noodles", "phone case", "",
])
def test_ordinary_shopping_is_not_mistaken_for_a_component(query):
    assert not looks_like_pc_part(query)


def test_untagged_stores_take_part_in_every_search():
    """Existing stores must behave exactly as they did before tagging existed."""
    for key in ("amazon_ae", "noon", "newegg", "carrefour_ae"):
        assert STORES[key].tags == ()
        assert store_matches_query(STORES[key].tags, "air fryer")
        assert store_matches_query(STORES[key].tags, "rtx 4070")


def test_specialists_sit_out_searches_they_cannot_answer():
    assert store_matches_query(("pc_parts",), "rtx 4070")
    assert not store_matches_query(("pc_parts",), "air fryer")


# ---- currencies the UK and EU stores actually quote in --------------------

@pytest.mark.parametrize("price_text,expected_value,expected_currency", [
    ("£549.99", 549.99, "GBP"),
    ("GBP 549.99", 549.99, "GBP"),
    ("€1,299.00", 1299.00, "EUR"),
    # Germany writes the separators the other way round. Reading this as
    # 2.199 would price a graphics card at under three euros.
    ("2.199,00 €", 2199.00, "EUR"),
    ("AED 2,099.00", 2099.00, "AED"),
])
def test_european_prices_are_read_correctly(price_text, expected_value, expected_currency):
    """£ and € were missing from the price pattern, so a UK or German shop had
    no prices at all as far as the parser was concerned — every card discarded
    for want of one, and the store blamed for markup it never managed to read."""
    from app.net import parse_price
    from app.structural import PRICE_TEXT, detect_currency

    match = PRICE_TEXT.search(price_text)
    assert match, f"{price_text!r} was not recognised as a price"
    assert parse_price(match.group(0)) == pytest.approx(expected_value)
    assert detect_currency(price_text) == expected_currency


def test_the_woocommerce_search_path_is_inside_the_probe_budget():
    """Only the first MAX_CANDIDATE_URLS patterns are ever tried, so the order
    of DEFAULT_SEARCH_PATTERNS decides which platforms are reachable at all.
    WooCommerce powers most independent shops; it sat fourth and was cut."""
    urls = provider("pcdubai").search_urls("rtx 4070")

    assert any("post_type=product" in u for u in urls), "WooCommerce fell outside the cap"
    assert any(u.endswith("/search?q=rtx+4070") for u in urls), "Shopify path missing"


def test_the_uae_storefront_is_used_for_microless():
    """www.microless.com is the group site and carries no UAE pricing."""
    assert build("microless").origin == "https://uae.microless.com"


# ---- saying why a listing was dropped ------------------------------------
#
# A live run had five stores report "N offers, 0 matched". That is a dead end:
# a store with no stock and a filter that is too strict look identical, and
# they need opposite fixes.

@pytest.mark.parametrize("title,expected", [
    ("PNY GeForce RTX 4070 Ti 12GB XLR8", "different variant"),
    ("ASUS TUF Gaming Laptop RTX 4070", "complete system or laptop"),
    ("1STPLAYER PC Gaming Pro, Core i5-12400F, RTX 4070", "complete system"),
    ("GIGABYTE RTX 4060 Ti Windforce", "missing the model number"),
    # "FE" is itself a variant suffix, so use a plain card here.
    ("NVIDIA GeForce RTX 4070 Graphics Card (Renewed)", "refurbished"),
    ("Thermal Pad for RTX 4070 Graphics Card", "accessory"),
    ("RTX 3060 3070 4060 4070 GPU Cards", "several different products"),
])
def test_every_rejection_names_its_rule(title, expected):
    from app.matching import rejection_reason

    reason = rejection_reason("rtx 4070", title)
    assert reason and expected in reason, f"got {reason!r}"


def test_a_genuine_match_has_no_rejection_reason():
    from app.matching import rejection_reason

    assert rejection_reason(
        "rtx 4070", "Gigabyte GeForce RTX 4070 Gaming OC 12G Graphics Card"
    ) is None


def test_dropped_listings_are_reported_with_their_reasons():
    from app.matching import filter_relevant
    from app.models import Market, Offer

    def offer(title, price):
        return Offer(
            store="gcc_gamers", store_label="GCC Gamers", market=Market.LOCAL,
            country="AE", title=title, url=f"https://x.ae/{abs(hash(title))}",
            price=price, currency="AED", price_aed=price, landed_aed=price,
        )

    result = filter_relevant([
        offer("Gigabyte GeForce RTX 4070 Gaming OC 12G", 2150),
        offer("MSI GeForce RTX 4070 VENTUS 2X 12G", 2100),
        offer("PNY GeForce RTX 4070 Ti 12GB", 3000),
        offer("ASUS TUF Gaming Laptop With RTX 4070", 6600),
    ], "rtx 4070")

    assert len(result.offers) == 2
    reasons = {d.title: d.reason for d in result.dropped}
    assert len(reasons) == 2
    assert "variant" in reasons["PNY GeForce RTX 4070 Ti 12GB"]
    assert "laptop" in reasons["ASUS TUF Gaming Laptop With RTX 4070"]
    assert all(d.store_label == "GCC Gamers" for d in result.dropped)


# ---- telling a wrong URL apart from changed markup -----------------------

def test_a_page_with_no_prices_blames_the_url_not_the_markup():
    """Seven stores reported "the markup has changed" when the likeliest cause
    was a search URL that does not exist. Nothing had changed, and there are no
    selectors to check — a config-driven store has no selectors."""
    p = provider()
    p.last_html = "<html><body><h1>Page not found</h1></body></html>"

    message, kind = p.describe_empty_result()
    assert "search URL is probably wrong" in message
    assert "diagnose.py microless" in message
    assert kind == "parse"


def test_a_page_with_prices_points_at_the_card_layout():
    p = provider()
    p.last_html = "<html><body><div>AED 2,150.00</div><div>AED 1,999.00</div></body></html>"

    message, _ = p.describe_empty_result()
    assert "product_path" in message


def test_bespoke_providers_keep_the_selector_message():
    """Their URL is known-good, so their selectors really are the suspect."""
    message, kind = build("amazon_ae").describe_empty_result()
    assert "selectors" in message and kind == "parse"


# ---- waiting for the products, not for the page --------------------------
#
# Microless, Emax, Gear-up and Jumbo each returned around a megabyte of
# rendered HTML with no price anywhere in it. Their shells load at
# DOMContentLoaded and the listings arrive over XHR a second or two later, so
# waiting on the document proves nothing.

def test_script_bodies_do_not_count_as_rendered_content():
    """The near-miss that made the first attempt useless.

    A storefront ships its price formatting inside a <script>, so matching raw
    HTML declares the content "arrived" instantly — on exactly the pages this
    is meant to wait for."""
    from app.browser import visible_html

    html = """<html><body><div>Loading…</div>
      <script>var tpl = "AED 1,949.00";</script>
      <style>.p:after{content:"AED"}</style>
    </body></html>"""

    assert "1,949.00" not in visible_html(html)
    assert "Loading" in visible_html(html)


def test_visible_html_keeps_the_real_markup():
    from app.browser import visible_html

    assert "AED 2,150" in visible_html("<div><span>AED 2,150.00</span></div>")


def test_polling_returns_the_page_once_the_content_appears():
    import asyncio

    from app.browser import _poll_for

    class Page:
        """Renders its products on the third look, like an XHR arriving."""

        def __init__(self):
            self.looks = 0

        async def content(self):
            self.looks += 1
            if self.looks < 3:
                return "<html><body><div>Loading...</div></body></html>"
            return "<html><body><div>AED 1,949.00</div></body></html>"

        async def wait_for_timeout(self, _ms):
            return None

    page = Page()
    html = asyncio.run(_poll_for(page, r"AED\s?[\d,.]+", "http://x"))
    assert html is not None and "1,949.00" in html
    assert page.looks == 3


def test_polling_gives_up_rather_than_hanging():
    """A store that never loads its products must not hold the search open."""
    import asyncio

    from app.browser import (CONTENT_POLL_BUDGET_MS, CONTENT_POLL_INTERVAL_MS,
                             _poll_for)

    class Empty:
        def __init__(self):
            self.looks = 0

        async def content(self):
            self.looks += 1
            return "<html><body>nothing here</body></html>"

        async def wait_for_timeout(self, _ms):
            return None

    page = Empty()
    assert asyncio.run(_poll_for(page, r"AED\s?[\d,.]+", "http://x")) is None
    assert page.looks <= CONTENT_POLL_BUDGET_MS // CONTENT_POLL_INTERVAL_MS + 1


def test_the_generic_browser_path_waits_for_a_price(monkeypatch):
    import asyncio

    import app.providers.generic as generic_module

    seen = {}

    async def fake_render(url, **kwargs):
        seen.update(url=url, **kwargs)
        return SHOP_PAGE

    monkeypatch.setattr(generic_module, "render", fake_render)
    offers = asyncio.run(provider().search_browser("rtx 4070", 10))

    assert seen.get("wait_for_pattern"), "browser path did not wait for content"
    assert len(offers) == 3


# ---- diagnosing a component shop with the right query --------------------

def test_diagnose_picks_a_query_the_named_stores_could_answer():
    """Diagnosing a component shop with "sony wh-1000xm5" proves nothing: an
    empty result is the correct answer there, and it is indistinguishable from
    a scraper that cannot read the page."""
    import diagnose

    assert diagnose.default_query_for(["microless", "emax"], None) == "rtx 4070"
    assert diagnose.default_query_for(["amazon_ae", "microless"], None) \
        == "sony wh-1000xm5"
    assert diagnose.default_query_for(["microless"], "ddr5 ram") == "ddr5 ram"


def test_prices_only_in_javascript_are_reported_as_such():
    """The message said "prices are on the page" for six stores whose only
    prices were inside <script> templates. That points at the card layout when
    nothing had rendered at all — opposite fixes again."""
    p = provider()
    p.last_html = (
        '<html><body><div>Loading...</div>'
        '<script>var fmt = "AED 1,949.00";</script></body></html>'
    )

    message, kind = p.describe_empty_result()
    assert "only inside the page's JavaScript" in message
    assert kind == "parse"


def test_visible_prices_still_point_at_the_card_layout():
    p = provider()
    p.last_html = "<html><body><div>AED 2,150.00</div><div>AED 1,999.00</div></body></html>"
    assert "product_path" in p.describe_empty_result()[0]
