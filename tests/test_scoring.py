"""The ranking rules are the product. These lock in the judgement calls."""

import pytest

from app.config import WEIGHTS
from app.models import Market, Offer
from app.pricing import import_fees_aed, normalise_offer
from app.scoring import (build_recommendation, delivery_score, price_score,
                         rank, rating_score, review_volume_score)

RATES = {"AED": 1.0, "USD": 3.6725}


def make(store="amazon_ae", market=Market.LOCAL, price=1000.0, currency="AED",
         rating=4.5, reviews=500, days=2, shipping=0.0, in_stock=True, title="Test Product"):
    return Offer(
        store=store, store_label=store, market=market, country="AE",
        title=title, url=f"https://example.com/{store}/{price}",
        price=price, currency=currency, shipping=shipping,
        rating=rating, review_count=reviews, delivery_days=days, in_stock=in_stock,
    )


# ---- component scores ----

def test_price_score_is_ratio_based():
    assert price_score(100, 100) == 1.0
    assert price_score(200, 100) == pytest.approx(0.5)
    # An extreme outlier must not distort the rest of the field.
    assert price_score(110, 100) == pytest.approx(0.909, abs=1e-3)


def test_rating_score_shrinks_low_volume_ratings():
    """A perfect score from 2 buyers must not beat a strong score from thousands."""
    lonely_five = rating_score(5.0, 2)
    proven_four_six = rating_score(4.6, 5000)
    assert proven_four_six > lonely_five


def test_rating_score_treats_missing_as_neutral():
    assert rating_score(None, None) == 0.5


def test_rating_score_floors_at_bad_ratings():
    assert rating_score(2.0, 1000) == 0.0


def test_review_volume_saturates():
    assert review_volume_score(0) == 0.0
    assert review_volume_score(3000) == pytest.approx(1.0, abs=1e-6)
    assert review_volume_score(50_000) == 1.0


def test_delivery_score_prefers_fast():
    assert delivery_score(1) == 1.0
    assert delivery_score(21) == 0.0
    assert delivery_score(2) > delivery_score(10)


# ---- landed cost ----

def test_import_fees_waived_under_threshold():
    assert import_fees_aed(250.0, 40.0, "ebay") == 0.0


def test_import_fees_applied_above_threshold():
    # 1000 goods + 100 freight: duty 50, VAT 5% of 1150 = 57.5 -> 107.5
    assert import_fees_aed(1000.0, 100.0, "ebay") == pytest.approx(107.5)


def test_local_stores_never_incur_import_fees():
    assert import_fees_aed(5000.0, 0.0, "amazon_ae") == 0.0


def test_normalise_converts_usd_and_adds_duty():
    offer = make(store="ebay", market=Market.GLOBAL, price=100.0, currency="USD", shipping=10.0)
    normalise_offer(offer, RATES)
    assert offer.price_aed == pytest.approx(367.25)
    assert offer.shipping_aed == pytest.approx(36.73, abs=0.01)
    assert offer.import_fees_aed > 0          # above the AED 300 threshold
    assert offer.landed_aed > offer.price_aed


# ---- ranking ----

def test_cheapest_landed_wins_when_all_else_equal():
    offers = [make(price=1200.0), make(store="noon", price=900.0)]
    for o in offers:
        normalise_offer(o, RATES)
    ranked = rank(offers)
    assert ranked[0].price == 900.0
    assert "Lowest landed cost" in ranked[0].badges


def test_slightly_cheaper_but_unreviewed_loses_to_trusted_listing():
    """The whole point of the app: cheapest is not automatically best."""
    sketchy = make(store="aliexpress", market=Market.GLOBAL, price=95.0, currency="USD",
                   rating=None, reviews=0, days=25)
    solid = make(store="amazon_ae", price=380.0, rating=4.6, reviews=4200, days=2)
    for o in (sketchy, solid):
        normalise_offer(o, RATES)
    ranked = rank([sketchy, solid])
    assert ranked[0].store == "amazon_ae"


def test_out_of_stock_cannot_win():
    dead = make(price=500.0, in_stock=False)
    alive = make(store="noon", price=900.0)
    for o in (dead, alive):
        normalise_offer(o, RATES)
    ranked = rank([dead, alive])
    assert ranked[0].store == "noon"


def test_scores_are_bounded_and_breakdown_complete():
    offers = [make(price=p) for p in (500.0, 1500.0)]
    for o in offers:
        normalise_offer(o, RATES)
    for offer in rank(offers):
        assert 0 <= offer.score <= 100
        assert set(offer.score_breakdown) == set(WEIGHTS.as_dict())


def test_rank_handles_empty():
    assert rank([]) == []


def test_weights_sum_to_one():
    assert sum(WEIGHTS.as_dict().values()) == pytest.approx(1.0)


# ---- recommendation ----

def test_recommendation_explains_itself():
    offers = [make(price=900.0), make(store="noon", price=1200.0)]
    for o in offers:
        normalise_offer(o, RATES)
    rec = build_recommendation(rank(offers))
    assert rec is not None
    assert rec.rationale
    assert rec.runner_up is not None
    assert rec.confidence in {"high", "medium", "low"}


def test_recommendation_flags_when_pick_is_not_cheapest():
    cheap = make(store="aliexpress", market=Market.GLOBAL, price=20.0, currency="USD",
                 rating=None, reviews=0, days=30)
    good = make(store="amazon_ae", price=300.0, rating=4.8, reviews=9000, days=1)
    for o in (cheap, good):
        normalise_offer(o, RATES)
    rec = build_recommendation(rank([cheap, good]))
    assert rec.offer.store == "amazon_ae"
    assert any("more" in reason for reason in rec.rationale)


def test_recommendation_none_for_empty():
    assert build_recommendation([]) is None
