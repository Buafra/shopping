"""Relevance filtering.

Searching "iPhone 15 Pro" at any store returns cases, screen protectors and
cables alongside the phone. Ranking those by price would hand back a AED 19
case as "the best deal", so offers are filtered on two signals before scoring:

1. Token overlap with the query, with model numbers weighted heavily.
2. Price sanity — accessories cluster far below the real product, so listings
   priced absurdly below the median of the well-matched set are dropped.
"""

from __future__ import annotations

import re
import statistics

from .models import Offer

# Words that almost always mean "an add-on for the thing", not the thing.
ACCESSORY_TERMS = {
    "case", "cover", "sleeve", "pouch", "skin", "protector", "tempered",
    "screen guard", "cable", "charger", "adapter", "dock", "stand", "holder",
    "mount", "strap", "band", "bag", "replacement", "spare", "repair",
    "sticker", "decal", "wrap", "lens protector", "keyboard cover", "grip",
    "compatible with", "accessory", "accessories",
}

# Query words that are noise for matching purposes.
STOPWORDS = {
    "the", "a", "an", "and", "or", "with", "for", "new", "best", "buy",
    "cheap", "price", "in", "uae", "dubai", "online", "original", "genuine",
}

_TOKEN = re.compile(r"[a-z0-9]+")
_MODEL_TOKEN = re.compile(r"^(?:[a-z]+\d+[a-z0-9]*|\d+[a-z]+[a-z0-9]*|\d{3,})$")


def tokenise(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in STOPWORDS]


def relevance(query: str, title: str) -> float:
    """0..1 — how well a listing title answers the query.

    Model-number-ish tokens ("15", "pro", "m3", "rtx4070") count double: they
    are what distinguishes the product the shopper actually asked for.
    """
    q_tokens = tokenise(query)
    if not q_tokens:
        return 0.0

    t_tokens = set(tokenise(title))
    if not t_tokens:
        return 0.0

    total = 0.0
    matched = 0.0
    for token in q_tokens:
        weight = 2.0 if _MODEL_TOKEN.match(token) else 1.0
        total += weight
        if token in t_tokens:
            matched += weight

    return matched / total if total else 0.0


def looks_like_accessory(query: str, title: str) -> bool:
    """True when the title advertises an add-on the query did not ask for."""
    title_l = (title or "").lower()
    query_l = (query or "").lower()
    for term in ACCESSORY_TERMS:
        # If the shopper explicitly searched for a case, a case is on-target.
        if term in title_l and term not in query_l:
            return True
    return False


def filter_relevant(
    offers: list[Offer],
    query: str,
    *,
    min_relevance: float = 0.5,
    price_floor_ratio: float = 0.25,
) -> tuple[list[Offer], int]:
    """Return (kept_offers, dropped_count).

    Two passes: a text pass, then a price-outlier pass anchored on the median
    of whatever survived the text pass.
    """
    if not offers:
        return [], 0

    scored = [(o, relevance(query, o.title)) for o in offers]
    kept = [
        o for o, score in scored
        if score >= min_relevance and not looks_like_accessory(query, o.title)
    ]

    # If the filter was too aggressive (unusual query, sparse titles), relax it
    # rather than returning nothing at all.
    if len(kept) < 2:
        kept = [o for o, score in scored if score >= min_relevance * 0.6]
    if len(kept) < 2:
        kept = [o for o, _ in scored]
        return kept, len(offers) - len(kept)

    prices = [o.landed_aed or o.price_aed or o.price for o in kept]
    median = statistics.median(prices)
    floor = median * price_floor_ratio

    final = [
        o for o in kept
        if (o.landed_aed or o.price_aed or o.price) >= floor
    ]
    if len(final) < 2:
        final = kept

    return final, len(offers) - len(final)
