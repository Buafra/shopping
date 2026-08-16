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
    assert "sdg-product-tile" in out, "must surface the real card class"


def test_summary_flags_a_bot_wall(capsys):
    diagnose.summarise(BOT_WALL, build("sharaf_dg"), "test")
    out = capsys.readouterr().out

    assert "access denied page" in out or "JS-required shell" in out
    assert "no repeating price-bearing blocks found" in out


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
