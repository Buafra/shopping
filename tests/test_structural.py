"""Structure-based extraction, for stores whose markup has no usable classes.

Carrefour UAE is the live example: 1.17MB of Tailwind utility classes, no
semantic class names anywhere, and a retired JSON API. These tests pin the
behaviour that makes such a page parseable — and, just as importantly, the
refusals that stop it inventing products out of navigation chrome.
"""

import pytest

from app.structural import parse_cards

ORIGIN = "https://www.carrefouruae.com"


def tailwind_page(count: int = 4) -> str:
    cards = "".join(f"""
      <div class="relative gap-2xs md:gap-1.5xs pl-md">
        <a href="/mafuae/en/sony-wh-1000xm5-v{i}/p/10{i}23" class="flex flex-col">
          <img src="https://cdn.mafrservices.com/p/{i}.jpg" alt="Sony WH-1000XM5 V{i}">
          <span data-testid="product_name" class="text-sm truncate">Sony WH-1000XM5 Variant {i}</span>
        </a>
        <span data-testid="product_price" class="font-bold">AED {1279 + i * 10}.00</span>
        <div data-testid="rating" class="text-xs">4.{i} (5{i})</div>
      </div>""" for i in range(1, count + 1))
    return f"<html><body><div class='grid'>{cards}</div></body></html>"


def test_extracts_products_from_utility_class_markup():
    cards = parse_cards(tailwind_page(4), origin=ORIGIN, link_match="/p/")
    assert len(cards) == 4

    first = cards[0]
    assert first.title == "Sony WH-1000XM5 Variant 1"
    assert first.price == 1289.00
    assert first.url == f"{ORIGIN}/mafuae/en/sony-wh-1000xm5-v1/p/10123"
    assert first.image.startswith("https://cdn.mafrservices.com")
    assert first.rating == 4.1
    assert first.review_count == 51


def test_absolutises_relative_urls_and_strips_query():
    html = """<div><a href="/mafuae/en/x/p/999?utm=abc">
      <span data-testid="product_name">Sony WH-1000XM5 Headphones</span></a>
      <span data-testid="product_price">AED 1,299.00</span></div>"""
    cards = parse_cards(html, origin=ORIGIN, link_match="/p/")
    assert cards[0].url == f"{ORIGIN}/mafuae/en/x/p/999"


def test_deduplicates_repeated_links():
    """Grid and carousel often render the same product twice."""
    card = """<div><a href="/mafuae/en/x/p/1">
      <span data-testid="product_name">Sony WH-1000XM5 Headphones</span></a>
      <span data-testid="product_price">AED 1,299.00</span></div>"""
    cards = parse_cards(card * 3, origin=ORIGIN, link_match="/p/")
    assert len(cards) == 1


def test_takes_the_lower_of_a_struck_through_pair():
    """Cards show was/now prices; the shopper pays the lower one."""
    html = """<div><a href="/mafuae/en/x/p/1">
      <span data-testid="product_name">Sony WH-1000XM5 Headphones</span></a>
      <span class="line-through">AED 1,699.00</span>
      <span class="font-bold">AED 1,279.00</span></div>"""
    cards = parse_cards(html, origin=ORIGIN, link_match="/p/")
    assert cards[0].price == 1279.00


def test_falls_back_to_image_alt_for_the_title():
    html = """<div><a href="/mafuae/en/x/p/1" class="flex">
      <img src="/i.jpg" alt="Sony WH-1000XM5 Wireless Headphones"></a>
      <span>AED 1,299.00</span></div>"""
    cards = parse_cards(html, origin=ORIGIN, link_match="/p/")
    assert cards[0].title == "Sony WH-1000XM5 Wireless Headphones"


# ---- the refusals matter as much as the extractions ----------------------

def test_ignores_links_with_no_price():
    """Navigation and category links must never become products."""
    html = """<nav><a href="/mafuae/en/electronics/p/cat">Electronics</a></nav>
      <div><a href="/mafuae/en/x/p/1">
        <span data-testid="product_name">Sony WH-1000XM5 Headphones</span></a>
        <span data-testid="product_price">AED 1,299.00</span></div>"""
    cards = parse_cards(html, origin=ORIGIN, link_match="/p/")
    assert len(cards) == 1
    assert cards[0].title == "Sony WH-1000XM5 Headphones"


def test_rejects_call_to_action_text_as_a_title():
    html = """<div><a href="/mafuae/en/x/p/1">Add to Cart</a>
      <span>AED 1,299.00</span></div>"""
    assert parse_cards(html, origin=ORIGIN, link_match="/p/") == []


def test_ignores_a_price_that_belongs_to_the_whole_page():
    """A basket total wrapping the entire page must not price every link."""
    html = """<body><div class="cart-total">Total AED 4,500.00
        <a href="/mafuae/en/a/p/1">Product A</a>
        <a href="/mafuae/en/b/p/2">Product B</a>
      </div></body>"""
    # The only price-bearing block holds two product links, so the price
    # belongs to neither. Claiming it for both would be a wrong price.
    assert parse_cards(html, origin=ORIGIN, link_match="/p/") == []


def test_shared_price_container_is_never_split_across_products():
    """Two products in one price-bearing wrapper must not both inherit it."""
    html = """<div class="promo">AED 999.00 bundle
        <a href="/mafuae/en/a/p/1"><span data-testid="product_name">Headphones A</span></a>
        <a href="/mafuae/en/b/p/2"><span data-testid="product_name">Headphones B</span></a>
      </div>"""
    cards = parse_cards(html, origin=ORIGIN, link_match="/p/")
    assert [c.price for c in cards] != [999.0, 999.0]
    assert cards == []


def test_respects_the_card_limit():
    cards = parse_cards(tailwind_page(30), origin=ORIGIN, link_match="/p/", max_cards=5)
    assert len(cards) == 5


@pytest.mark.parametrize("html", ["", "<html></html>", "not html", "<a href='/p/1'>x</a>"])
def test_survives_junk_input(html):
    assert parse_cards(html, origin=ORIGIN, link_match="/p/") == []


def test_link_match_scopes_the_search():
    """A store whose products live under /item/ must not pick up /p/ links."""
    html = """<div><a href="/blog/p/some-article">
      <span data-testid="product_name">An article about headphones</span></a>
      <span data-testid="product_price">AED 1,299.00</span></div>"""
    assert parse_cards(html, origin=ORIGIN, link_match="/item/") == []


# ---- price is the number the shopper pays, not the nearest number ---------

def card(price_block: str) -> str:
    return f"""<div><a href="/mafuae/en/sony/p/1">
      <span>Sony WH-1000XM5 Headphones</span></a>{price_block}</div>"""


@pytest.mark.parametrize("label,block,expected", [
    ("plain price", '<span>AED 1,279.00</span>', 1279.00),
    ("was + now",
     '<span>Was AED 1,299.00</span><span>Now AED 845.00</span>', 845.00),
    ("struck-through original",
     '<span class="line-through">AED 1,299.00</span><span>AED 845.00</span>', 845.00),
    ("save badge before the price",
     '<span>Save AED 454.00</span><span>AED 845.00</span>', 845.00),
    ("amount-off badge before the price",
     '<span>AED 200.00 off</span><span>AED 999.00</span>', 999.00),
    ("percent-off badge before the price",
     '<span>25% OFF</span><span>AED 999.00</span>', 999.00),
    ("the full Carrefour spread",
     '<span class="line-through">AED 1,299.00</span><span>AED 845.00</span>'
     '<span>Save AED 454.00</span>'
     '<span>or AED 70.42/month for 12 months</span>', 845.00),
])
def test_price_ignores_savings_and_instalments(label, block, expected):
    """A monthly instalment is the smallest number on the card. Taking it
    would put AED 70.42 into the ranking instead of AED 845 and win."""
    cards = parse_cards(card(block), origin=ORIGIN, link_match="/p/")
    assert cards, f"{label}: nothing parsed"
    assert cards[0].price == expected, label


def test_a_card_with_only_an_instalment_yields_no_price():
    """Better to report nothing than to publish a per-month figure as a price."""
    cards = parse_cards(card('<span>AED 70.42 per month</span>'),
                        origin=ORIGIN, link_match="/p/")
    assert cards == []


def test_adjacent_elements_do_not_fuse():
    """selectolax joins children with no separator, so '...off' + 'AED 999'
    reads as 'offAED 999' and every word-boundary rule silently fails."""
    from selectolax.parser import HTMLParser

    from app.structural import node_text

    node = HTMLParser("<div><span>off</span><span>AED 999.00</span></div>").css_first("div")
    assert node_text(node) == "off AED 999.00"
