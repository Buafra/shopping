"""The diagnostic tool is how a broken store gets fixed, so it has to work on
markup nobody has seen before."""

import diagnose
from app.providers import build

REDESIGNED = """<html><head><title>Search - Store</title></head><body>
<div class="plp-grid">
  <div class="sdg-product-tile"><a href="/product/a/">Sony WH-1000XM5</a>
    <span class="tile__price">AED 1,329.00</span></div>
  <div class="sdg-product-tile"><a href="/product/b/">Sony WH-1000XM5 Silver</a>
    <span class="tile__price">AED 1,379.00</span></div>
  <div class="sdg-product-tile"><a href="/product/c/">Sony WH-1000XM4</a>
    <span class="tile__price">AED 899.00</span></div>
</div></body></html>"""

BOT_WALL = """<html><head><title>Access Denied</title></head>
<body><h1>Access Denied</h1><p>Please enable JavaScript.</p></body></html>"""


def test_summary_reports_dead_selectors_and_suggests_the_real_one(capsys):
    diagnose.summarise(REDESIGNED, build("sharaf_dg"), "test")
    out = capsys.readouterr().out

    assert "0 x  div.product-item" in out, "must show the current selectors matching nothing"
    assert "provider parsed  : 0 raw -> 0 after dedup" in out
    assert "/product/" in out, "must surface the product-link shape"


def test_summary_flags_a_bot_wall(capsys):
    diagnose.summarise(BOT_WALL, build("sharaf_dg"), "test")
    out = capsys.readouterr().out

    assert "access denied page" in out or "JS-required shell" in out
    assert "no price-adjacent links found" in out


def test_summary_survives_empty_and_junk_input(capsys):
    provider = build("ebay")
    for junk in ("", "not html", "<html></html>"):
        diagnose.summarise(junk, provider, "test")   # must not raise
    assert capsys.readouterr().out


def test_summary_reports_working_selectors(capsys):
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "ebay_search.html").read_text()
    diagnose.summarise(html, build("ebay"), "test")
    out = capsys.readouterr().out

    # 3 cards parse; one is a duplicate URL and drops in postprocess
    assert "provider parsed  : 3 raw -> 2 after dedup" in out


# ---- what the page's own JSON actually carries ---------------------------
#
# Noon returned prices and titles with no ratings. The fix is a key name, and
# key names change between releases — so the tool has to read them off the
# page rather than leave three plausible spellings to be tried in turn.

JSON_PAGE = """<html><body><script id="__NEXT_DATA__">
{"props":{"hits":[{"sku":"N1","name":"MSI RTX 4070","sale_price":2150,
"star_rating":4.4,"totalReviews":61}]}}
</script></body></html>"""


def test_diagnose_reports_the_keys_a_product_record_carries(capsys):
    diagnose.report_json_records(JSON_PAGE)
    out = capsys.readouterr().out
    assert "1 product-shaped record(s)" in out
    assert "star_rating = 4.4" in out
    assert "totalReviews = 61" in out
    assert "sale_price" in out          # the full key list, for the price too


def test_diagnose_says_so_when_a_record_has_no_rating_keys(capsys):
    page = JSON_PAGE.replace('"star_rating":4.4,"totalReviews":61', '"stock":4')
    diagnose.report_json_records(page)
    out = capsys.readouterr().out
    assert "no rating/review-ish keys" in out


def test_diagnose_handles_pages_with_no_json_at_all(capsys):
    diagnose.report_json_records("<html><body><p>nothing</p></body></html>")
    assert "no product-shaped records found" in capsys.readouterr().out


def test_field_coverage_singles_out_the_field_that_is_missing(capsys):
    from app.models import Market, Offer

    offers = [
        Offer(store="noon", store_label="Noon UAE", market=Market.LOCAL,
              country="AE", title=f"Card {n}", url=f"https://noon.com/{n}",
              price=2000.0, currency="AED", rating=None, review_count=None)
        for n in range(3)
    ]
    diagnose.report_field_coverage(offers)
    lines = {
        parts[0]: " ".join(parts[1:])
        for parts in (line.split() for line in capsys.readouterr().out.splitlines())
        if parts
    }
    assert lines["rating"].startswith("0/3")
    assert "MISSING" in lines["rating"]
    assert lines["url"] == "3/3"
    assert "MISSING" not in lines["url"]
