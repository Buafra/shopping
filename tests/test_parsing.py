"""Parsing helpers carry the whole scraper layer — if these are wrong,
every store returns plausible-looking garbage."""

import pytest

from app.net import absolutise, parse_int, parse_price, parse_rating


@pytest.mark.parametrize("raw,expected", [
    ("AED 1,299.00", 1299.00),
    ("US $45.99", 45.99),
    ("$1,899", 1899.0),
    ("1.299,00 د.إ", 1299.00),      # EU-style separators
    ("2.599,50", 2599.50),
    ("45.00 - 89.00", 45.00),        # range -> low end
    ("Dhs. 349", 349.0),
    (1299, 1299.0),
    (74.5, 74.5),
    ("", None),
    (None, None),
    ("Free", None),
    ("0", None),
    ("out of stock", None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_parse_price_keeps_two_decimal_comma():
    # "89,99" is 89.99 in EU notation, not 8999.
    assert parse_price("89,99") == 89.99


def test_parse_price_thousands_comma():
    # "1,299" is 1299, not 1.299.
    assert parse_price("1,299") == 1299.0


@pytest.mark.parametrize("raw,expected", [
    ("(1,234)", 1234),
    ("2.5K ratings", 2500),
    ("1.2M sold", 1200000),
    ("87", 87),
    ("no reviews", None),
    (None, None),
])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("4.5 out of 5 stars", 4.5),
    ("4,3", 4.3),
    (4.7, 4.7),
    ("6.2", None),      # impossible rating rejected
    ("0", None),
    (None, None),
])
def test_parse_rating(raw, expected):
    assert parse_rating(raw) == expected


def test_parse_rating_rescales():
    # AliExpress-style 0-100 satisfaction becomes 0-5.
    assert parse_rating("90", scale=100.0) == 4.5


@pytest.mark.parametrize("href,expected", [
    ("/dp/B01", "https://www.amazon.ae/dp/B01"),
    ("dp/B01", "https://www.amazon.ae/dp/B01"),
    ("https://other.com/x", "https://other.com/x"),
    ("//cdn.example.com/i.jpg", "https://cdn.example.com/i.jpg"),
    (None, None),
])
def test_absolutise(href, expected):
    assert absolutise(href, "https://www.amazon.ae") == expected


# ---- error classification -------------------------------------------------

def test_transport_errors_are_classified():
    """Knowing *why* a store failed is the difference between fixing your
    network and fixing a selector."""
    import httpx

    from app.net import classify_transport_error

    url = "https://www.noon.com/search"

    # httpx reports a policy-denied CONNECT as a bare "403 Forbidden" with no
    # mention of a proxy, so this must be classified on type, not text.
    proxy = classify_transport_error(httpx.ProxyError("403 Forbidden"), url)
    assert proxy.kind == "unreachable"
    assert "noon.com" in str(proxy)
    assert "proxy" in str(proxy).lower()

    dns = classify_transport_error(
        httpx.ConnectError("[Errno -2] Name or service not known"), url)
    assert dns.kind == "unreachable"

    slow = classify_transport_error(httpx.ReadTimeout("timed out"), url)
    assert slow.kind == "timeout"


def test_fetch_error_renders_hint():
    from app.net import FetchError

    err = FetchError("HTTP 403 from amazon.ae", kind="blocked", hint="try a residential IP")
    assert "403" in str(err) and "residential IP" in str(err)
    assert err.kind == "blocked"


# ---- ratings hidden inside JSON product records --------------------------
#
# Noon returned five graphics cards with a price, a title and no rating at
# all. A missing rating is not neutral: rating and review volume are 35% of
# the score, so those offers lost comparisons they might have won.

@pytest.mark.parametrize("record,expected", [
    ({"rating": 4.3}, 4.3),
    ({"product_rating": 4.3}, 4.3),
    ({"averageRating": 4.3}, 4.3),
    ({"star_rating": "4.3"}, 4.3),
    ({"productRating": "4.3 out of 5"}, 4.3),
    # The shape that broke: the score nested beside its own count.
    ({"product_rating": {"value": 4.3, "count": 88}}, 4.3),
    ({"rating": {"average": 4.3}}, 4.3),
])
def test_rating_found_whatever_the_key_is_called(record, expected):
    from app.net import rating_from_record

    assert rating_from_record(record) == expected


def test_a_review_count_is_never_read_as_a_rating():
    """"rating_count": 3 parses as a perfectly plausible 3.0 stars.

    That is the failure mode that makes shape-matching dangerous, so counting
    keys are excluded from the rating scan outright."""
    from app.net import rating_from_record

    assert rating_from_record({"rating_count": 3}) is None
    assert rating_from_record({"num_ratings": 4}) is None
    assert rating_from_record({"ratingCount": 2, "rating": 4.6}) == 4.6


def test_no_rating_is_reported_as_none_not_zero():
    from app.net import rating_from_record

    assert rating_from_record({"sku": "X", "name": "Thing", "price": 10}) is None
    assert rating_from_record({}) is None
    assert rating_from_record(None) is None


@pytest.mark.parametrize("record,expected", [
    ({"num_ratings": 312}, 312),
    ({"ratingCount": 312}, 312),
    ({"reviewCount": 312}, 312),
    ({"reviews": 312}, 312),
    ({"ratings": "1,204"}, 1204),
    ({"product_rating": {"value": 4.5, "count": 312}}, 312),
])
def test_review_counts_found_whatever_the_key_is_called(record, expected):
    from app.net import reviews_from_record

    assert reviews_from_record(record) == expected


def test_a_rating_is_never_read_as_a_review_count():
    """"rating": 4.3 is a score. Rounding it to "4 reviews" would then be fed
    to the Bayesian shrink as near-zero confidence — quietly wrong twice."""
    from app.net import reviews_from_record

    assert reviews_from_record({"rating": 4.3}) is None
    assert reviews_from_record({"rating": 4.3, "num_ratings": 88}) == 88


def test_stock_quantities_are_not_review_counts():
    from app.net import reviews_from_record

    assert reviews_from_record({"quantity": 14, "stock_count": 3}) is None
