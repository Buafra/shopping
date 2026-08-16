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

from dataclasses import dataclass, field

from .models import Offer

# Words that almost always mean "an add-on for the thing", not the thing.
ACCESSORY_TERMS = {
    "case", "cover", "sleeve", "pouch", "skin", "protector", "tempered",
    "screen guard", "cable", "charger", "adapter", "dock", "stand", "holder",
    "mount", "strap", "band", "bag", "replacement", "spare", "repair",
    "sticker", "decal", "wrap", "lens protector", "keyboard cover", "grip",
    "compatible with", "accessory", "accessories",
    # PC-component add-ons. These sit far below the part's price and would
    # otherwise anchor the comparison: a AED 25 thermal pad "for RTX 4070" was
    # kept as a graphics card.
    "thermal pad", "riser cable", "riser card", "support bracket",
    "gpu support", "anti-sag", "sag holder", "backplate", "water block",
    "waterblock", "dust filter", "extension cable", "adapter cable",
}

# A refurbished unit is not the same purchase as a new one — different
# warranty, different condition — so it must not undercut new listings on
# price unless the shopper asked for it.
USED_TERMS = {
    "renewed", "refurbished", "refurb", "pre-owned", "preowned", "used",
    "open box", "openbox", "second hand", "secondhand", "like new",
    "certified refurbished",
}

# Query words that are noise for matching purposes.
STOPWORDS = {
    "the", "a", "an", "and", "or", "with", "for", "new", "best", "buy",
    "cheap", "price", "in", "uae", "dubai", "online", "original", "genuine",
}

_TOKEN = re.compile(r"[a-z0-9]+")
_MODEL_TOKEN = re.compile(r"^(?:[a-z]+\d+[a-z0-9]*|\d+[a-z]+[a-z0-9]*|\d{3,})$")

# A short letter prefix separated from a model number is one identifier written
# loosely: "WH-1000XM5", "WH 1000XM5" and "WH1000XM5" are the same product.
# Splitting on the separator turns the prefix into a throwaway token, and then
# WF-1000XM5 — a completely different product — matches a WH-1000XM5 query on
# everything except one letter.
_MODEL_JOIN = re.compile(r"\b([a-z]{1,4})[\s\-_/]+(\d{2,}[a-z0-9]*)")

# Short words that routinely sit in front of a number while describing it
# rather than naming a model. Without this, "iPhone 15 Pro 256GB" collapses
# to "pro256gb" and loses both the variant and the capacity.
NOT_MODEL_PREFIXES = {
    # Variant suffixes must never be glued to the capacity that follows them:
    # "RTX 4070 Ti 12GB" would become "rtx4070" + "ti12gb", and the variant
    # check — which looks for a bare "ti" after the model — would never see it,
    # so a 4070 Ti answered a 4070 query.
    "ti", "super", "xt", "xtx", "fe", "oc",
    "pro", "max", "plus", "air", "mini", "lite", "ultra", "gen", "size",
    "set", "pack", "kit", "box", "new", "top", "all", "up", "to", "of",
    "cm", "mm", "inch", "in", "ml", "l", "kg", "g", "w", "v", "hz", "gb",
    "tb", "mb", "ah", "mah", "k", "hd", "fhd", "uhd", "led", "lcd",
}


def _join_model(match: re.Match) -> str:
    prefix, number = match.group(1), match.group(2)
    if prefix in NOT_MODEL_PREFIXES:
        return match.group(0)
    return prefix + number


def normalise_models(text: str) -> str:
    """Glue loosely-written model numbers into single tokens.

    "WH-1000XM5", "WH 1000XM5" and "WH1000XM5" all become `wh1000xm5`, so a
    one-letter difference like WF-1000XM5 no longer looks like a near match.
    """
    lowered = (text or "").lower()
    previous = None
    while previous != lowered:
        previous = lowered
        lowered = _MODEL_JOIN.sub(_join_model, lowered)
    return lowered


def tokenise(text: str) -> list[str]:
    return [
        t for t in _TOKEN.findall(normalise_models(text)) if t not in STOPWORDS
    ]


# Suffixes that sit right after a model number and name a *different* product
# at a different price: an RTX 4070 Ti is not an RTX 4070, and a 4070 Super is
# neither. Factory-overclock marks like "OC" are deliberately absent — those
# are the same chip.
VARIANT_SUFFIXES = {
    "ti", "super", "xt", "xtx", "pro", "max", "plus", "ultra", "mini",
    "lite", "se", "fe",
}


def variant_mismatch(query: str, title: str) -> bool:
    """True when the title's model carries a variant suffix the query did not
    ask for, or drops one the query did ask for."""
    q_tokens = tokenise(query)
    wanted_models = {t for t in q_tokens if _MODEL_TOKEN.match(t)}
    if not wanted_models:
        return False

    q_set = set(q_tokens)
    t_tokens = tokenise(title)

    def suffix_after(tokens: list[str]) -> str | None:
        for index, token in enumerate(tokens):
            if token in wanted_models and index + 1 < len(tokens):
                nxt = tokens[index + 1]
                if nxt in VARIANT_SUFFIXES:
                    return nxt
        return None

    asked = suffix_after(q_tokens)
    offered = suffix_after(t_tokens)
    if offered and offered not in q_set:
        return True          # "rtx 4070" must not answer with a 4070 Ti
    if asked and asked != offered:
        return True          # "rtx 4070 ti" must not answer with a plain 4070
    return False


# A product model number: 4-5 digits, optionally prefixed (rtx4070) or
# suffixed (3060ti). Deliberately excludes 3-digit numbers and capacities like
# "12gb" or "128" (bit width), which appear in perfectly ordinary titles.
PRODUCT_MODEL = re.compile(
    r"^(?:[a-z]{1,4})?\d{4,5}(?:ti|xt|xtx|super|s|k|kf|kb|f|x|x3d|hx|hs|h|u)?$"
)

# A listing selling a whole system rather than the part that was searched for.
SYSTEM_TERMS = {
    "gaming pc", "pc gaming", "gaming desktop", "gaming rig", "gaming tower",
    "desktop pc", "desktop computer", "pc desktop", "tower pc",
    "prebuilt", "pre-built", "barebone", "workstation", "mini pc",
    "all-in-one", "complete pc", "full set", "gaming bundle",
    # A laptop containing the card is not the card. Mobile GPUs are different
    # silicon at different power limits, so this is a product difference, not
    # merely a packaging one.
    "laptop", "notebook", "gaming laptop",
}

# A processor named alongside the part you searched for means a built machine.
# This catches systems whose titles never use the word "PC" — "1STPLAYER PC
# Gaming super Pro TMD, Core i5-12400F, RTX 4070" reads as a graphics card
# until you notice it also has a CPU.
CPU_MARKERS = {
    "core i3", "core i5", "core i7", "core i9", "core ultra",
    "ryzen 3", "ryzen 5", "ryzen 7", "ryzen 9", "ryzen ai",
    "celeron", "pentium", "xeon", "threadripper",
}


def product_models(text: str) -> set[str]:
    return {t for t in tokenise(text) if PRODUCT_MODEL.match(t)}


def lists_multiple_products(query: str, title: str) -> bool:
    """True for a listing that covers several different products at once.

    Marketplaces sell one page across many SKUs — "3060TI 3050 3070 GPU RTX
    4070 4060TI" — and advertise the price of the cheapest. The result looks
    like an RTX 4070 for AED 959 when the 959 buys a 3050, which then anchors
    the whole comparison as "the cheapest listing".
    """
    extra = product_models(title) - product_models(query)
    return len(extra) >= 2


def looks_like_a_system(query: str, title: str) -> bool:
    """A prebuilt PC or laptop containing the part is not the part."""
    title_l = (title or "").lower()
    query_l = (query or "").lower()

    if any(term in title_l and term not in query_l for term in SYSTEM_TERMS):
        return True
    # A CPU quoted next to the queried part means a whole machine — unless the
    # query itself named a CPU, in which case that is what was asked for.
    return any(cpu in title_l and cpu not in query_l for cpu in CPU_MARKERS)


def model_tokens(text: str) -> set[str]:
    """The tokens that identify *which* product this is, not what kind."""
    return {t for t in tokenise(text) if _MODEL_TOKEN.match(t)}


def relevance(query: str, title: str) -> float:
    """0..1 — how well a listing title answers the query.

    Model-number-ish tokens ("15", "m3", "rtx4070") count double: they are what
    distinguishes the product the shopper actually asked for. A query model
    number that is absent from the title scores 0 outright — that is not a
    weaker match, it is a different item.
    """
    q_tokens = tokenise(query)
    if not q_tokens:
        return 0.0

    t_tokens = set(tokenise(title))
    if not t_tokens:
        return 0.0

    # Model numbers are identity, not description.
    wanted_models = model_tokens(query)
    if wanted_models and not wanted_models.issubset(t_tokens):
        return 0.0

    # ...and so is the variant suffix attached to them.
    if variant_mismatch(query, title):
        return 0.0

    # A page selling six GPUs at the price of the cheapest is not an offer for
    # any one of them, and a prebuilt PC is not a graphics card.
    if lists_multiple_products(query, title) or looks_like_a_system(query, title):
        return 0.0

    total = 0.0
    matched = 0.0
    for token in q_tokens:
        weight = 2.0 if _MODEL_TOKEN.match(token) else 1.0
        total += weight
        if token in t_tokens:
            matched += weight

    return matched / total if total else 0.0


def looks_used(query: str, title: str) -> bool:
    """True when the listing is refurbished/used and the query did not ask."""
    title_l = (title or "").lower()
    query_l = (query or "").lower()
    return any(
        term in title_l and term not in query_l for term in USED_TERMS
    )


def looks_like_accessory(query: str, title: str) -> bool:
    """True when the title advertises an add-on the query did not ask for."""
    title_l = (title or "").lower()
    query_l = (query or "").lower()
    for term in ACCESSORY_TERMS:
        # If the shopper explicitly searched for a case, a case is on-target.
        if term in title_l and term not in query_l:
            return True
    return False


def rejection_reason(query: str, title: str) -> str | None:
    """Why this listing is not the product searched for — None if it is.

    "6 offers, 0 matched" is a dead end for whoever has to fix it: a store that
    genuinely has no stock and a filter that is too strict look identical. This
    names which rule fired, so the two can be told apart from the output alone.
    """
    if not tokenise(query):
        return "the query has no searchable words"
    if not tokenise(title):
        return "no title was parsed from the listing"

    # Checked before the model number, though relevance() rejects on the model
    # first: a multi-SKU listing usually fails *both* tests, because "RTX 3060
    # 3070 4070" glues the prefix onto the first number only and so never
    # contains a bare "rtx4070". "Missing the model" is true but misleading —
    # the model is right there, in a listing that sells five other cards too.
    if lists_multiple_products(query, title):
        return "one listing covering several different products"

    wanted = model_tokens(query)
    if wanted:
        missing = wanted - set(tokenise(title))
        if missing:
            return f"missing the model number {', '.join(sorted(missing))}"
    if variant_mismatch(query, title):
        return "a different variant of that model (Ti/Super/XT/Pro…)"
    if looks_like_a_system(query, title):
        return "a complete system or laptop, not the component"
    if looks_like_accessory(query, title):
        return "an accessory for the product rather than the product"
    if looks_used(query, title):
        return "refurbished or used stock"

    score = relevance(query, title)
    if score < 0.5:
        return f"too little overlap with the query (scored {score:.2f})"
    return None


@dataclass
class Dropped:
    """One listing that did not make the comparison, and why."""

    title: str
    store_label: str
    price: float
    reason: str


@dataclass
class FilterResult:
    offers: list[Offer]
    dropped_mismatch: int = 0
    dropped_used: int = 0
    dropped: list[Dropped] = field(default_factory=list)


def filter_relevant(
    offers: list[Offer],
    query: str,
    *,
    min_relevance: float = 0.5,
    price_floor_ratio: float = 0.25,
    include_used: bool = False,
) -> FilterResult:
    """Keep only listings that are plausibly the product the shopper asked for.

    Three passes: drop wrong products and accessories, drop refurbished units
    unless requested, then drop price outliers relative to the median of what
    survived.
    """
    if not offers:
        return FilterResult([])

    original = list(offers)
    used_count = 0
    if not include_used:
        fresh = [o for o in offers if not looks_used(query, o.title)]
        used_count = len(offers) - len(fresh)
        offers = fresh or offers        # never leave the shopper with nothing
        if not fresh:
            used_count = 0

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
        return FilterResult(
            kept, len(offers) - len(kept), used_count,
            _explain_dropped(original, kept, query),
        )

    prices = [o.landed_aed or o.price_aed or o.price for o in kept]
    median = statistics.median(prices)
    floor = median * price_floor_ratio

    final = [
        o for o in kept
        if (o.landed_aed or o.price_aed or o.price) >= floor
    ]
    if len(final) < 2:
        final = kept

    return FilterResult(
        final, len(offers) - len(final), used_count,
        _explain_dropped(original, final, query),
    )


def _explain_dropped(
    original: list[Offer], kept: list[Offer], query: str
) -> list[Dropped]:
    """Name a reason for every listing that did not survive the filters."""
    survivors = {id(o) for o in kept}
    dropped = []
    for offer in original:
        if id(offer) in survivors:
            continue
        dropped.append(
            Dropped(
                title=offer.title,
                store_label=offer.store_label,
                price=offer.landed_aed or offer.price_aed or offer.price,
                # Nothing structural matched, so it lost on price alone.
                reason=rejection_reason(query, offer.title)
                or "priced far below the others, so probably a different item",
            )
        )
    return dropped
