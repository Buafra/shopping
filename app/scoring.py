"""Ranking and recommendation.

Every offer gets a 0–100 score built from five normalised components, then the
winner is explained in plain language. The explanation matters as much as the
number — a recommendation you cannot audit is not worth acting on.
"""

from __future__ import annotations

import math

from .config import (RATING_PRIOR_COUNT, RATING_PRIOR_MEAN, STORES, WEIGHTS,
                     ScoringWeights)
from .models import Market, Offer, Recommendation

# A review count at/above this is treated as full confidence.
REVIEW_SATURATION = 3_000


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def price_score(landed: float, best: float) -> float:
    """1.0 for the cheapest landed cost, decaying as offers get pricier.

    Ratio-based rather than min-max: paying 2x the best price should score
    ~0.5 regardless of how expensive the worst listing in the set happens to
    be, so one absurd outlier cannot flatter everything else.
    """
    if landed <= 0 or best <= 0:
        return 0.0
    return _clamp(best / landed)


def rating_score(rating: float | None, review_count: int | None) -> float:
    """Bayesian-shrunk star rating mapped to 0..1.

    A lone 5.0 from two buyers is pulled toward the prior mean, so it cannot
    beat a 4.6 backed by thousands of reviews.
    """
    if rating is None:
        # Unrated is treated as "unknown", not "bad" — the review-volume term
        # already penalises the missing evidence.
        return 0.5

    n = float(review_count or 0)
    shrunk = ((n * rating) + (RATING_PRIOR_COUNT * RATING_PRIOR_MEAN)) / (
        n + RATING_PRIOR_COUNT
    )
    # Map 3.0–5.0 onto 0–1; below 3 stars is uniformly poor.
    return _clamp((shrunk - 3.0) / 2.0)


def review_volume_score(review_count: int | None) -> float:
    """Log-scaled confidence in the rating. 0 reviews -> 0, 3k+ -> 1."""
    n = review_count or 0
    if n <= 0:
        return 0.0
    return _clamp(math.log10(1 + n) / math.log10(1 + REVIEW_SATURATION))


def delivery_score(days: int | None) -> float:
    """Same-day/next-day is 1.0, decaying to 0 at about three weeks."""
    if days is None:
        return 0.5
    return _clamp(1.0 - (days - 1) / 20.0)


def trust_score(store_key: str) -> float:
    spec = STORES.get(store_key)
    return spec.trust if spec else 0.6


def score_offer(offer: Offer, best_landed: float, weights: ScoringWeights) -> Offer:
    landed = offer.landed_aed or offer.price_aed or offer.price

    components = {
        "price": price_score(landed, best_landed),
        "rating": rating_score(offer.rating, offer.review_count),
        "review_volume": review_volume_score(offer.review_count),
        "delivery": delivery_score(offer.delivery_days),
        "trust": trust_score(offer.store),
    }

    total = sum(components[k] * getattr(weights, k) for k in components)

    # Out-of-stock listings stay visible but can never win.
    if not offer.in_stock:
        total *= 0.4

    offer.score = round(total * 100, 1)
    offer.score_breakdown = {k: round(v, 3) for k, v in components.items()}
    return offer


def rank(offers: list[Offer], weights: ScoringWeights | None = None) -> list[Offer]:
    """Score every offer and return them best-first."""
    if not offers:
        return []

    weights = weights or WEIGHTS
    landed_values = [o.landed_aed or o.price_aed or o.price for o in offers]
    best_landed = min(v for v in landed_values if v > 0)

    scored = [score_offer(o, best_landed, weights) for o in offers]
    scored.sort(key=lambda o: (o.score or 0), reverse=True)

    _apply_badges(scored)
    return scored


def _apply_badges(offers: list[Offer]) -> None:
    if not offers:
        return

    in_stock = [o for o in offers if o.in_stock] or offers

    cheapest = min(in_stock, key=lambda o: o.landed_aed or o.price_aed or o.price)
    cheapest.badges.append("Lowest landed cost")

    rated = [o for o in in_stock if o.rating is not None and (o.review_count or 0) >= 20]
    if rated:
        best_rated = max(rated, key=lambda o: (o.rating or 0, o.review_count or 0))
        best_rated.badges.append("Best reviewed")

    with_eta = [o for o in in_stock if o.delivery_days is not None]
    if with_eta:
        fastest = min(with_eta, key=lambda o: o.delivery_days or 999)
        fastest.badges.append("Fastest delivery")

    offers[0].badges.insert(0, "Best overall")


def _fmt(amount: float | None) -> str:
    return f"AED {amount:,.0f}" if amount is not None else "n/a"


def build_recommendation(ranked: list[Offer]) -> Recommendation | None:
    """Explain, in shopper's terms, why the winner won."""
    if not ranked:
        return None

    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    reasons: list[str] = []

    landed_values = [o.landed_aed or o.price for o in ranked if o.in_stock] or [
        o.landed_aed or o.price for o in ranked
    ]
    cheapest_landed = min(landed_values)
    dearest_landed = max(landed_values)
    winner_landed = winner.landed_aed or winner.price

    # Price standing
    if abs(winner_landed - cheapest_landed) < 0.01:
        reasons.append(
            f"It is the cheapest option once shipping and UAE duties are counted "
            f"({_fmt(winner_landed)})."
        )
    else:
        premium = winner_landed - cheapest_landed
        pct = (premium / cheapest_landed * 100) if cheapest_landed else 0
        reasons.append(
            f"At {_fmt(winner_landed)} landed it costs {_fmt(premium)} ({pct:.0f}%) more "
            f"than the outright cheapest listing, but wins on reviews, delivery and "
            f"seller reliability."
        )

    # Reviews
    if winner.rating is not None:
        if winner.review_count:
            reasons.append(
                f"Rated {winner.rating:.1f}/5 across {winner.review_count:,} reviews — "
                f"enough volume for that score to mean something."
            )
        else:
            reasons.append(f"Rated {winner.rating:.1f}/5, though the review count is unstated.")
    else:
        reasons.append("No rating was published for this listing, so judge the seller yourself.")

    # Market and logistics
    if winner.market == Market.LOCAL:
        reasons.append(
            f"Buying locally from {winner.store_label} means no customs surprises, "
            f"local warranty, and delivery in about "
            f"{winner.delivery_days or '2–3'} days."
        )
    else:
        fees = winner.import_fees_aed or 0
        fee_note = (
            f"including {_fmt(fees)} of estimated UAE duty and VAT"
            if fees > 0 else "with no duty expected at this value"
        )
        reasons.append(
            f"It ships from {winner.store_label} ({winner.country}) in roughly "
            f"{winner.delivery_days or 14} days, {fee_note}. "
            f"Returns on cross-border orders are slower than buying locally."
        )

    # Margin over the runner-up
    if runner_up and runner_up.score is not None and winner.score is not None:
        gap = winner.score - runner_up.score
        if gap < 3:
            reasons.append(
                f"It is a close call — {runner_up.store_label} scores within "
                f"{gap:.1f} points, so either is defensible."
            )

    savings = round(dearest_landed - winner_landed, 2) if dearest_landed > winner_landed else None

    # Confidence reflects how much evidence we actually have.
    stores_seen = len({o.store for o in ranked})
    if stores_seen >= 3 and (winner.review_count or 0) >= 50:
        confidence = "high"
    elif stores_seen >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return Recommendation(
        offer=winner,
        rationale=reasons,
        runner_up=runner_up,
        savings_vs_worst_aed=savings,
        confidence=confidence,
    )
