"""Structure-based product extraction, for stores with no usable class names.

Utility-first CSS (Tailwind and friends) leaves markup like
`class="relative gap-2xs md:gap-1.5xs pl-md"` — styling only, nothing that
identifies a product. Class selectors are useless on those pages and break on
every redesign anyway.

What stays stable is the *shape*: a product card is a block containing a link
whose URL matches the store's product pattern, a price, and a title. This
module finds cards that way.

It is deliberately strict. A candidate is discarded unless it has a product
link, a parseable price and a plausible title, because a comparison built from
mis-parsed rows is worse than one that honestly reports nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser

from .net import absolutise, clean_text, parse_int, parse_price, parse_rating

# "AED 1,279.00", "1,279.00 AED", "Dhs. 349", "$45.99"
PRICE_TEXT = re.compile(
    r"(?:AED|USD|SAR|Dhs\.?|د\.إ|\$)\s?\d[\d,.\s]*|\d[\d,.\s]*\s?(?:AED|SAR|د\.إ)",
    re.I,
)
RATING_TEXT = re.compile(r"\b([0-5](?:[.,]\d)?)\s*(?:/\s*5|out of 5|\()", re.I)
REVIEWS_TEXT = re.compile(r"\(\s*([\d,.]+)\s*\)|\b([\d,.]+)\s*(?:ratings?|reviews?)", re.I)

# Text that is never a product title.
JUNK_TITLE = re.compile(
    r"^(add to (cart|basket)|buy now|compare|wishlist|sold by|free delivery|"
    r"express|sponsored|out of stock|in stock|see more|view all|\d+\s*%)",
    re.I,
)


@dataclass
class Card:
    title: str
    url: str
    price: float
    image: str | None = None
    rating: float | None = None
    review_count: int | None = None


def _ancestors(node, limit: int):
    current = node.parent
    for _ in range(limit):
        if current is None:
            return
        yield current
        current = current.parent


def _title_from(container, link) -> str:
    """Best available product name, preferring explicit test hooks."""
    for selector in (
        "[data-testid*='name']", "[data-testid*='title']", "[data-qa*='name']",
        "[itemprop='name']", "h1", "h2", "h3", "h4",
    ):
        node = container.css_first(selector)
        if node:
            text = clean_text(node.text())
            if len(text) > 8 and not JUNK_TITLE.match(text):
                return text

    # The link's own text, if it reads like a name rather than a call to action.
    text = clean_text(link.text())
    if len(text) > 8 and not JUNK_TITLE.match(text):
        return text

    for attr_holder, attr in ((link, "title"), (link, "aria-label")):
        value = clean_text(attr_holder.attributes.get(attr) or "")
        if len(value) > 8 and not JUNK_TITLE.match(value):
            return value

    image = container.css_first("img[alt]")
    if image:
        alt = clean_text(image.attributes.get("alt") or "")
        if len(alt) > 8 and not JUNK_TITLE.match(alt):
            return alt

    return ""


def _price_from(container) -> float | None:
    """The lowest plausible price in the card.

    Cards often show a struck-through original next to the current price;
    taking the lowest matches what the shopper actually pays.
    """
    for selector in ("[data-testid*='price']", "[data-qa*='price']", "[itemprop='price']"):
        node = container.css_first(selector)
        if node:
            value = parse_price(clean_text(node.text()))
            if value:
                return value

    found = [parse_price(m) for m in PRICE_TEXT.findall(clean_text(container.text()))]
    values = [v for v in found if v and v > 0]
    return min(values) if values else None


def _image_from(container) -> str | None:
    node = container.css_first("img")
    if not node:
        return None
    for attr in ("src", "data-src", "data-original", "srcset"):
        value = node.attributes.get(attr)
        if value:
            return value.split()[0].split(",")[0]
    return None


def _rating_from(container) -> tuple[float | None, int | None]:
    for selector in ("[data-testid*='rating']", "[data-qa*='rating']",
                     "[itemprop='ratingValue']", "[class*='rating']"):
        node = container.css_first(selector)
        if node:
            text = clean_text(node.text())
            match = RATING_TEXT.search(text)
            rating = parse_rating(match.group(1)) if match else parse_rating(text)
            reviews = REVIEWS_TEXT.search(text)
            count = parse_int(reviews.group(1) or reviews.group(2)) if reviews else None
            if rating is not None:
                return rating, count
    return None, None


def parse_cards(
    html: str,
    *,
    origin: str,
    link_match: str,
    max_cards: int = 40,
    max_card_chars: int = 600,
) -> list[Card]:
    """Extract product cards from `html` by structure.

    `link_match` is the substring every product URL contains ("/p/", "/item/").
    """
    if not html:
        return []

    tree = HTMLParser(html)
    seen: set[str] = set()
    cards: list[Card] = []

    for link in tree.css(f'a[href*="{link_match}"]'):
        href = link.attributes.get("href") or ""
        url = absolutise(href.split("?")[0], origin)
        if not url or url in seen:
            continue

        # Walk out from the link until we find the block holding its price.
        #
        # The block must contain exactly one product link. A wrapper holding
        # several — a carousel, a grid, a basket summary — has one price that
        # belongs to none of them in particular, and accepting it would stamp
        # that price onto every product inside. Wrong prices are worse than
        # missing ones, so those are skipped.
        container = None
        for ancestor in _ancestors(link, 6):
            text = clean_text(ancestor.text())
            if len(text) > max_card_chars:
                break
            if not PRICE_TEXT.search(text):
                continue
            if len(ancestor.css(f'a[href*="{link_match}"]')) != 1:
                break
            container = ancestor
            break
        if container is None:
            continue

        price = _price_from(container)
        title = _title_from(container, link)
        if not price or not title:
            continue

        seen.add(url)
        rating, review_count = _rating_from(container)
        cards.append(
            Card(
                title=title,
                url=url,
                price=price,
                image=_image_from(container),
                rating=rating,
                review_count=review_count,
            )
        )
        if len(cards) >= max_cards:
            break

    return cards
