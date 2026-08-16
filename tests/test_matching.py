"""Relevance filtering — the difference between comparing phones and
recommending a AED 19 phone case as the best deal."""

import pytest

from app.matching import (filter_relevant, looks_like_accessory, relevance,
                          tokenise)
from app.models import Market, Offer


def make(title, price):
    return Offer(
        store="amazon_ae", store_label="Amazon.ae", market=Market.LOCAL, country="AE",
        title=title, url=f"https://example.com/{abs(hash(title))}",
        price=price, currency="AED", price_aed=price, landed_aed=price,
    )


def test_tokenise_drops_stopwords():
    assert "the" not in tokenise("the best iPhone in UAE")
    assert "iphone" in tokenise("the best iPhone in UAE")


def test_relevance_full_match():
    assert relevance("sony wh-1000xm5", "Sony WH-1000XM5 Wireless Headphones") == 1.0


def test_relevance_partial():
    score = relevance("sony wh-1000xm5 headphones", "Sony Headphones")
    assert 0 < score < 1


def test_relevance_weights_model_numbers():
    """Matching the brand but missing the model must score below matching both."""
    both = relevance("iphone 15 pro", "Apple iPhone 15 Pro 256GB")
    brand_only = relevance("iphone 15 pro", "Apple iPhone Pro Max case")
    assert both > brand_only


def test_relevance_no_overlap():
    assert relevance("playstation 5", "Kitchen Blender 500W") == 0.0


def test_relevance_empty_inputs():
    assert relevance("", "anything") == 0.0
    assert relevance("something", "") == 0.0


@pytest.mark.parametrize("title", [
    "Silicone Case for iPhone 15 Pro",
    "Tempered Glass Screen Protector iPhone 15",
    "USB-C Charger Cable compatible with iPhone",
])
def test_accessories_detected(title):
    assert looks_like_accessory("iphone 15 pro", title)


def test_accessory_allowed_when_explicitly_searched():
    """Searching for a case should return cases."""
    assert not looks_like_accessory("iphone 15 case", "Silicone Case for iPhone 15")


def test_real_product_not_flagged():
    assert not looks_like_accessory("iphone 15 pro", "Apple iPhone 15 Pro 256GB Blue Titanium")


def test_filter_removes_accessories():
    offers = [
        make("Apple iPhone 15 Pro 256GB", 4299),
        make("Apple iPhone 15 Pro 128GB", 3899),
        make("Silicone Case for iPhone 15 Pro", 49),
        make("Screen Protector for iPhone 15 Pro", 25),
    ]
    kept, dropped = filter_relevant(offers, "iphone 15 pro")
    titles = [o.title for o in kept]
    assert dropped == 2
    assert all("Case" not in t and "Protector" not in t for t in titles)


def test_filter_drops_price_outliers():
    """A listing an order of magnitude below the pack is not the same product."""
    offers = [
        make("Sony WH-1000XM5 Wireless", 1399),
        make("Sony WH-1000XM5 Wireless Black", 1299),
        make("Sony WH-1000XM5 Wireless Silver", 1350),
        make("Sony WH-1000XM5 Wireless replacement earpads", 39),
    ]
    kept, _ = filter_relevant(offers, "sony wh-1000xm5 wireless")
    assert all(o.price >= 1000 for o in kept)


def test_filter_relaxes_rather_than_returning_nothing():
    """An unusual query must not produce an empty comparison."""
    offers = [make("Generic Item A", 100), make("Generic Item B", 120)]
    kept, _ = filter_relevant(offers, "some very specific unmatched query xyz")
    assert len(kept) == 2


def test_filter_handles_empty():
    assert filter_relevant([], "anything") == ([], 0)
