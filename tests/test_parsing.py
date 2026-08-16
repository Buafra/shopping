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
