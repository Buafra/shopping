"""Relevance filtering — the difference between comparing phones and
recommending a AED 19 phone case as the best deal."""

import pytest

from app.matching import (filter_relevant, looks_like_accessory, looks_used,
                          model_tokens, relevance, tokenise)
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
    """A title missing a non-model word is a weaker match, not a rejection."""
    score = relevance("sony wireless headphones", "Sony Headphones")
    assert 0 < score < 1


def test_a_title_missing_the_model_number_is_rejected_outright():
    """"Sony Headphones" is not a WH-1000XM5 — no partial credit."""
    assert relevance("sony wh-1000xm5 headphones", "Sony Headphones") == 0.0


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
    result = filter_relevant(offers, "iphone 15 pro")
    titles = [o.title for o in result.offers]
    assert result.dropped_mismatch == 2
    assert all("Case" not in t and "Protector" not in t for t in titles)


def test_filter_drops_price_outliers():
    """A listing an order of magnitude below the pack is not the same product."""
    offers = [
        make("Sony WH-1000XM5 Wireless", 1399),
        make("Sony WH-1000XM5 Wireless Black", 1299),
        make("Sony WH-1000XM5 Wireless Silver", 1350),
        make("Sony WH-1000XM5 Wireless replacement earpads", 39),
    ]
    result = filter_relevant(offers, "sony wh-1000xm5 wireless")
    assert all(o.price >= 1000 for o in result.offers)


def test_filter_relaxes_rather_than_returning_nothing():
    """An unusual query must not produce an empty comparison."""
    offers = [make("Generic Item A", 100), make("Generic Item B", 120)]
    result = filter_relevant(offers, "some very specific unmatched query xyz")
    assert len(result.offers) == 2


def test_filter_handles_empty():
    empty = filter_relevant([], "anything")
    assert empty.offers == [] and empty.dropped_mismatch == 0



# ---- model numbers are identity ------------------------------------------

def test_a_different_model_prefix_is_a_different_product():
    """WF-1000XM5 (earbuds) must never answer a WH-1000XM5 (over-ear) query.
    It once did: the hyphen split the prefix off, leaving a 0.75 match that
    sailed past the threshold and — being cheaper — won the recommendation."""
    assert relevance("sony wh-1000xm5", "Sony (Renewed) WF-1000XM5 Earbuds") == 0.0


def test_a_different_model_generation_is_a_different_product():
    assert relevance("sony wh-1000xm5", "Sony WH-1000XM4 Wireless Headphones") == 0.0


@pytest.mark.parametrize("title", [
    "Sony WH-1000XM5 Wireless Over-Ear Headphones",
    "Sony WH 1000XM5 Noise Cancelling Headphones",
    "Sony WH1000XM5 Black",
    "SONY WH_1000XM5 Headset",
])
def test_the_same_model_written_loosely_still_matches(title):
    assert relevance("sony wh-1000xm5", title) == 1.0


def test_model_tokens_are_extracted_from_loose_spellings():
    assert model_tokens("sony wh-1000xm5") == {"wh1000xm5"}
    assert model_tokens("sony wh 1000xm5") == {"wh1000xm5"}
    assert model_tokens("airfryer") == set()


def test_queries_without_a_model_still_match_loosely():
    """The strict rule must not break ordinary shopping queries."""
    assert relevance("air fryer", "Philips Digital Air Fryer 4.1L") > 0.5


# ---- refurbished stock is not the same purchase --------------------------

@pytest.mark.parametrize("title", [
    "Sony (Renewed) WH-1000XM5",
    "Sony WH-1000XM5 Refurbished",
    "Sony WH-1000XM5 - Open Box",
    "Sony WH-1000XM5 Pre-Owned",
])
def test_used_listings_detected(title):
    assert looks_used("sony wh-1000xm5", title)


def test_used_allowed_when_explicitly_requested():
    assert not looks_used("renewed sony wh-1000xm5", "Sony (Renewed) WH-1000XM5")


def test_new_listing_not_flagged_as_used():
    assert not looks_used("sony wh-1000xm5", "Sony WH-1000XM5 Wireless Headphones")


def _offer(title, price):
    return Offer(
        store="amazon_ae", store_label="Amazon.ae", market=Market.LOCAL, country="AE",
        title=title, url=f"https://example.com/{abs(hash(title))}",
        price=price, currency="AED", price_aed=price, landed_aed=price,
    )


def test_refurbished_units_are_hidden_by_default():
    """A renewed unit undercuts new stock on price without being the same buy."""
    offers = [
        _offer("Sony WH-1000XM5 Wireless Headphones", 1299),
        _offer("Sony WH-1000XM5 Headphones Black", 1249),
        _offer("Sony (Renewed) WH-1000XM5", 700),
    ]
    result = filter_relevant(offers, "sony wh-1000xm5")
    assert result.dropped_used == 1
    assert all("Renewed" not in o.title for o in result.offers)


def test_refurbished_units_shown_on_request():
    offers = [
        _offer("Sony WH-1000XM5 Wireless Headphones", 1299),
        _offer("Sony (Renewed) WH-1000XM5", 700),
    ]
    result = filter_relevant(offers, "sony wh-1000xm5", include_used=True)
    assert len(result.offers) == 2
    assert result.dropped_used == 0


def test_all_refurbished_still_returns_something():
    """If every listing is refurbished, showing them beats showing nothing."""
    offers = [
        _offer("Sony (Renewed) WH-1000XM5", 700),
        _offer("Sony WH-1000XM5 Refurbished", 720),
    ]
    result = filter_relevant(offers, "sony wh-1000xm5")
    assert len(result.offers) == 2


# ---- variant suffixes are identity too -----------------------------------

@pytest.mark.parametrize("title", [
    "ASUS TUF Gaming NVIDIA GeForce RTX 4070 Ti OC Edition",
    "GIGABYTE GeForce RTX 4070 SUPER WINDFORCE OC 12G",
    "ZOTAC Gaming GeForce RTX 4070 Super Twin Edge",
])
def test_a_variant_card_does_not_answer_a_base_model_query(title):
    """A live search for "rtx 4070" recommended an RTX 4070 Ti — a different,
    pricier card — while the actual 4070 sat cheaper one row below."""
    assert relevance("rtx 4070", title) == 0.0


@pytest.mark.parametrize("title", [
    "Gigabyte GeForce RTX 4070 Gaming OC 12G Graphics Card",
    "MSI GeForce RTX 4070 VENTUS 3X E 12G OC",
])
def test_the_base_model_still_matches(title):
    """"OC" is a factory overclock of the same chip, not a different product."""
    assert relevance("rtx 4070", title) == 1.0


def test_asking_for_a_variant_excludes_the_base_model():
    assert relevance("rtx 4070 ti", "Gigabyte GeForce RTX 4070 Gaming OC") == 0.0
    assert relevance("rtx 4070 ti", "ASUS TUF RTX 4070 Ti OC Edition") == 1.0


def test_asking_for_a_variant_excludes_a_different_variant():
    assert relevance("rtx 4070 ti", "GIGABYTE RTX 4070 SUPER WINDFORCE") == 0.0


def test_variant_rule_does_not_fire_without_a_model_number():
    """Ordinary queries must not be caught by the variant rule."""
    from app.matching import variant_mismatch

    assert not variant_mismatch("air fryer", "Philips Air Fryer XL Pro")
    assert relevance("air fryer", "Philips Digital Air Fryer 4.1L") > 0.5


# ---- one listing selling many products is an offer for none of them ------

@pytest.mark.parametrize("title", [
    "بطاقات رسومات 3060TI 3050 3070 GPU RTX 4070 4060TI",
    "بطاقة فيديو للألعاب RTX 5080/RTX5070TI/RTX 5070/RTX 4070",
    "Graphics Card RTX 3060 3070 4060 4070 Gaming GPU",
])
def test_multi_model_listings_are_rejected(title):
    """Marketplaces sell one page across many SKUs and advertise the cheapest.
    A live run surfaced such a listing at AED 959 as the second-best "RTX 4070"
    and then anchored the recommendation's savings against it — the 959 buys a
    3050, not a 4070."""
    assert relevance("rtx 4070", title) == 0.0


@pytest.mark.parametrize("title", [
    "MXZ Intel Core i7 14700KF 5.2GHz, GeForce RTX 4070, 32GB DDR5 Gaming Desktop",
    "MXZ Gaming PC, AMD Ryzen 7 7800X3D, RTX 4070 Super, 32GB",
    "Prebuilt Gaming Rig RTX 4070 Ryzen 5",
])
def test_a_prebuilt_system_is_not_the_component(title):
    assert relevance("rtx 4070", title) == 0.0


@pytest.mark.parametrize("title", [
    "Gigabyte GeForce RTX 4070 Gaming OC 12G Graphics Card 3X WINDFORCE",
    "GIGABYTE WINDFORCE GeForce RTX 4070 12GB GDDR6X PCIe 4.0 128-bit",
    "MSI GeForce RTX 4070 VENTUS 2X E 12G OC",
    "ASUS Dual GeForce RTX 4070 EVO OC Edition 12GB GDDR6X",
])
def test_ordinary_cards_are_untouched(title):
    """Capacities, bus widths and fan counts must not read as extra models."""
    assert relevance("rtx 4070", title) == 1.0


def test_searching_for_a_system_still_finds_systems():
    """The rule only fires when the query did not ask for a system."""
    assert relevance("gaming pc rtx 4070", "MXZ Gaming PC RTX 4070 32GB") > 0.5


def test_cpu_queries_are_unaffected():
    assert relevance("ryzen 7 7800x3d", "AMD Ryzen 7 7800X3D 8-Core Processor") == 1.0
