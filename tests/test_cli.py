"""Console-safety tests.

The CLI prints stars, arrows and em dashes. On a Windows console defaulting to
cp1252 (or an older cp437 one) that raises UnicodeEncodeError mid-render and
kills an otherwise successful search. These tests pin the fallbacks.
"""

import io
import sys

import cli
from app.models import (Market, Offer, Recommendation, SearchResponse,
                        StoreStatus)


def test_out_survives_unencodable_characters(monkeypatch):
    """A console that cannot encode a glyph must degrade, never raise."""

    class AsciiOut(io.TextIOWrapper):
        pass

    buffer = io.BytesIO()
    stream = AsciiOut(buffer, encoding="ascii", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)

    cli.out("stars ★ arrows ▶ dash —")   # must not raise
    stream.flush()

    written = buffer.getvalue().decode("ascii")
    assert "stars" in written and "arrows" in written


def test_out_passes_plain_text_through(capsys):
    cli.out("plain ascii line")
    assert "plain ascii line" in capsys.readouterr().out


def test_glyph_table_has_ascii_fallback_for_every_symbol():
    for name, (rich, plain) in cli._GLYPHS.items():
        assert plain, f"{name} has no ASCII stand-in"
        assert plain.isascii(), f"{name} fallback {plain!r} is not ASCII"


def test_colour_codes_collapse_when_not_a_tty():
    """Piping to a file must not litter it with escape sequences."""
    if not cli.COLOUR_OK:
        assert cli.RESET == "" and cli.GREEN == ""
    else:
        assert cli.RESET.startswith("\033")


def _offer(**kw):
    base = dict(
        store="amazon_ae", store_label="Amazon.ae", market=Market.LOCAL, country="AE",
        title="Sony WH-1000XM5 Wireless Headphones", url="https://www.amazon.ae/dp/X",
        price=1299.0, currency="AED", rating=4.6, review_count=2847,
        delivery_days=2, price_aed=1299.0, shipping_aed=0.0, import_fees_aed=0.0,
        landed_aed=1299.0, score=87.6,
    )
    base.update(kw)
    return Offer(**base)


def test_render_handles_a_full_response(capsys):
    offer = _offer()
    response = SearchResponse(
        query="sony wh-1000xm5",
        offers=[offer],
        stores=[StoreStatus(store="amazon_ae", store_label="Amazon.ae",
                            market=Market.LOCAL, ok=True, offer_count=1)],
        recommendation=Recommendation(
            offer=offer, rationale=["Cheapest once duty is counted."],
            confidence="high"),
        elapsed_ms=1200,
    )
    cli.render(response)
    out = capsys.readouterr().out

    assert "sony wh-1000xm5" in out
    assert "Amazon.ae" in out
    assert "RECOMMENDED" in out
    assert "Cheapest once duty is counted." in out
    assert "Store coverage" in out


def test_render_handles_empty_response(capsys):
    """A totally failed search must still print a readable report."""
    response = SearchResponse(
        query="nothing",
        offers=[],
        stores=[StoreStatus(store="ebay", store_label="eBay", market=Market.GLOBAL,
                            ok=False, error="cannot reach www.ebay.com",
                            error_kind="unreachable")],
        notes=["No store could be reached at all."],
        elapsed_ms=500,
    )
    cli.render(response)
    out = capsys.readouterr().out

    assert "No offers found." in out
    assert "cannot reach www.ebay.com" in out
    assert "No store could be reached at all." in out


def test_money_formats_and_handles_missing():
    assert cli.money(1299.0) == "1,299"
    assert cli.money(None) == cli.G["dash"]
