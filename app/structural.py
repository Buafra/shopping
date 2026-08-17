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

import collections
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from .net import absolutise, clean_text, parse_int, parse_price, parse_rating

# "AED 1,279.00", "1,279.00 AED", "Dhs. 349", "$45.99", "£549.99", "2.199,00 €"
#
# The symbol list must stay in step with CURRENCY_MARKERS below. It did not:
# £ and € were missing, so a UK or German storefront had no prices as far as
# this module was concerned, every card was discarded for want of one, and the
# store reported "markup changed" — for markup it had never been able to read.
#
# Whitespace is deliberately *not* allowed inside the number. It used to be,
# and a Carrefour card reading "Logitech Gaming Mouse Wireless G305 114 AED"
# matched "305 114 AED" — the model number's digits fused with the price and
# the mouse was listed at AED 305,114. An absurd high price is not a harmless
# outlier either: it drags the median that the price-floor filter is built on.
PRICE_TEXT = re.compile(
    r"(?:AED|USD|SAR|GBP|EUR|Dhs\.?|د\.إ|\$|£|€)\s?\d[\d,.]*"
    # For the trailing-currency form the number must not be glued to a letter:
    # "Logitech G305 AED 114" otherwise offers "305 AED" as a candidate price.
    # No real price begins in the middle of a model number.
    r"|(?<![A-Za-z0-9])\d[\d,.]*\s?(?:AED|SAR|GBP|EUR|د\.إ|£|€)",
    re.I,
)
# A store that localises by IP quotes local currency on the same markup. The
# currency has to be read from the page, never assumed from the store's
# nationality: AliExpress served a UAE visitor AED prices, and treating those
# as USD multiplied every listing by 3.67 and pushed real bargains out of
# contention as if they were absurdly expensive.
CURRENCY_MARKERS: list[tuple[str, str]] = [
    ("aed", "AED"), ("د.إ", "AED"), ("dhs", "AED"), ("dh", "AED"),
    ("sar", "SAR"), ("ر.س", "SAR"),
    ("usd", "USD"), ("us $", "USD"), ("$", "USD"),
    ("eur", "EUR"), ("€", "EUR"),
    ("gbp", "GBP"), ("£", "GBP"),
    ("inr", "INR"), ("₹", "INR"),
    ("cny", "CNY"), ("¥", "CNY"),
]


def detect_currency(text: str) -> str | None:
    """Read the currency out of a price string, or None if it is unmarked."""
    lowered = (text or "").lower()
    for marker, code in CURRENCY_MARKERS:
        if marker in lowered:
            return code
    return None


RATING_TEXT = re.compile(r"\b([0-5](?:[.,]\d)?)\s*(?:/\s*5|out of 5|\()", re.I)

# A number is not the selling price when it is introduced as a discount or an
# old price...
# Note "off" is deliberately absent here: it trails a discount ("AED 200 off")
# but leads a real price ("25% OFF  AED 999.00"), so it belongs below.
NOT_A_PRICE_BEFORE = re.compile(
    r"(save|saving|you save|discount of|was|before|instead of|rrp|"
    r"list price|reduced from|worth|"
    # Buy-now-pay-later splits, which UAE storefronts put on every card:
    # "or 4 interest-free payments of AED 104". Taking that as the price
    # quartered a graphics card and would have won the comparison outright.
    r"payments? of|instal?ments? of|as low as|starting (?:at|from))\W{0,4}$",
    re.I,
)
# ...or when it is quoted per month as an instalment, or is itself the discount.
NOT_A_PRICE_AFTER = re.compile(
    r"\W{0,3}(?:/|per\s+)?\s*(?:month|mo\b|year|installment|instalment)|"
    r"\W{0,3}x\s*\d+\s*(?:month|payment)|"
    r"\W{0,3}off\b",
    re.I,
)
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
    # None when the page did not mark the currency; the caller then falls back
    # to the store's default rather than guessing.
    currency: str | None = None
    image: str | None = None
    rating: float | None = None
    review_count: int | None = None


def node_text(node) -> str:
    """Text of an element with a space between child elements.

    selectolax concatenates children with no separator, so
    `<span>...off</span><span>AED 999</span>` reads as "offAED 999" — the
    tokens fuse, word boundaries vanish, and both the price-context rules and
    the title checks silently misfire. Always read text through here.
    """
    return clean_text(node.text(separator=" "))


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
            text = node_text(node)
            if len(text) > 8 and not JUNK_TITLE.match(text):
                return text

    # The link's own text, if it reads like a name rather than a call to action.
    text = node_text(link)
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


PRICE_HOOKS = ("[data-testid*='price']", "[data-qa*='price']", "[itemprop='price']")


def _price_hook_value(container) -> tuple[float | None, str | None]:
    """A price read from an explicit hook, which may carry no currency mark."""
    for selector in PRICE_HOOKS:
        node = container.css_first(selector)
        if node:
            raw = node_text(node)
            value = parse_price(raw)
            if value:
                return value, detect_currency(raw)
    return None, None


def _price_from(container) -> tuple[float | None, str | None]:
    """What the shopper actually pays for this item.

    Cards are littered with numbers that are not the price: a struck-through
    original, a "Save AED 454" badge, and a monthly instalment figure. Taking
    the smallest number picks the instalment — on a real Carrefour card,
    AED 70.42/month instead of AED 845 — which is not a small error, it is a
    wrong answer that then wins the ranking.

    So candidates whose surrounding text marks them as a saving, an old price
    or an instalment are discarded first; of what remains, the lowest is the
    current price (the other survivor is usually the struck-through original).
    """
    hooked, hooked_currency = _price_hook_value(container)
    if hooked:
        return hooked, hooked_currency or detect_currency(node_text(container))

    text = node_text(container)
    candidates: list[tuple[float, str | None]] = []
    for match in PRICE_TEXT.finditer(text):
        before = text[max(0, match.start() - 26):match.start()]
        after = text[match.end():match.end() + 20]
        if NOT_A_PRICE_BEFORE.search(before) or NOT_A_PRICE_AFTER.match(after):
            continue
        value = parse_price(match.group(0))
        if value and value > 0:
            candidates.append((value, detect_currency(match.group(0))))

    if not candidates:
        return None, None
    return min(candidates, key=lambda pair: pair[0])


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
            text = node_text(node)
            match = RATING_TEXT.search(text)
            rating = parse_rating(match.group(1)) if match else parse_rating(text)
            reviews = REVIEWS_TEXT.search(text)
            count = parse_int(reviews.group(1) or reviews.group(2)) if reviews else None
            if rating is not None:
                return rating, count
    return None, None


# Path fragments that mark a product page across most storefront platforms.
# Ordered loosely by specificity; ties are broken towards the longer match.
PRODUCT_PATH_HINTS = (
    "/dp/", "/itm/", "/products/", "/product/", "/item/", "/prd/",
    "/pd/", "/ip/", "/p/", "/buy/", "/shop/",
)


def guess_product_paths(html: str, *, min_links: int = 2, limit: int = 3) -> list[str]:
    """Candidate product-URL patterns for this page, best first.

    One guess is not enough. A store whose product links carry a locale prefix
    ("/en/", "/uae/") shares that segment with its navigation, so the most
    common segment can be a real pattern that still matches the wrong links —
    the page then yields visible prices and no readable cards, which is exactly
    what three stores reported. The caller tries each until one produces cards.
    """
    if not html:
        return []

    tree = HTMLParser(html)
    hrefs: list[str] = []
    for link in tree.css("a[href]"):
        href = link.attributes.get("href") or ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        for ancestor in _ancestors(link, 4):
            text = node_text(ancestor)
            if len(text) < 900 and PRICE_TEXT.search(text):
                hrefs.append(href)
                break

    if not hrefs:
        return []

    ranked: list[tuple[int, int, str]] = []
    for hint in PRODUCT_PATH_HINTS:
        count = sum(1 for href in hrefs if hint in href)
        if count >= min_links:
            # More specific wins ties: "/products/" contains "/product/" only
            # by accident of spelling.
            ranked.append((count, len(hint), hint))

    segments: collections.Counter = collections.Counter()
    for href in hrefs:
        for segment in urlsplit(href).path.split("/"):
            if segment and not segment.isdigit() and len(segment) <= 14:
                segments[f"/{segment}/"] += 1
    known = {hint for _, _, hint in ranked}
    for segment, count in segments.most_common():
        if count >= min_links and segment not in known:
            ranked.append((count, 0, segment))

    ranked.sort(key=lambda item: (-item[0], -item[1]))
    return [path for _, _, path in ranked][:limit]


def guess_product_path(html: str, *, min_links: int = 2) -> str | None:
    """Work out what this store's product URLs look like, from the page itself.

    Adding a store should not require reading its HTML first. Product links are
    the one thing every storefront has in common, and the ones that matter sit
    next to a price — navigation and footer links do not. So: collect the
    links that sit near a price, and report the path fragment they share.

    Returns None when the page has no price-adjacent links at all, which is
    what a bot wall or a JS-only shell looks like.
    """
    if not html:
        return None

    tree = HTMLParser(html)
    hrefs: list[str] = []
    for link in tree.css("a[href]"):
        href = link.attributes.get("href") or ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        for ancestor in _ancestors(link, 4):
            text = node_text(ancestor)
            if len(text) < 900 and PRICE_TEXT.search(text):
                hrefs.append(href)
                break

    if not hrefs:
        return None

    hinted = {
        hint: sum(1 for href in hrefs if hint in href)
        for hint in PRODUCT_PATH_HINTS
    }
    usable = {hint: n for hint, n in hinted.items() if n >= min_links}
    if usable:
        # Prefer the more specific fragment when several match: "/products/"
        # contains "/product/" only by accident of spelling, and picking the
        # longer one keeps the match tighter.
        return max(usable.items(), key=lambda kv: (kv[1], len(kv[0])))[0]

    # No recognised platform. Fall back to whatever path segment these links
    # actually share — plenty of stores use /gp/, /catalog/ or a locale prefix.
    segments: collections.Counter = collections.Counter()
    for href in hrefs:
        for segment in urlsplit(href).path.split("/"):
            if segment and not segment.isdigit() and len(segment) <= 14:
                segments[f"/{segment}/"] += 1
    for segment, count in segments.most_common():
        if count >= min_links:
            return segment
    return None


def _product_urls(container, link_match: str, origin: str) -> set[str]:
    """The distinct products a block links to.

    Resolved and query-stripped, so "/p/x", "/p/x?ref=grid" and
    "https://shop/p/x" count once between them rather than three times.
    """
    urls = set()
    for anchor in container.css(f'a[href*="{link_match}"]'):
        href = (anchor.attributes.get("href") or "").split("?")[0]
        resolved = absolutise(href, origin)
        if resolved:
            urls.add(resolved.rstrip("/"))
    return urls


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
        # Normalised the same way the container check normalises, or a store
        # that links one product both with and without a trailing slash yields
        # it twice — two identical rows competing in the same comparison.
        if not url or url.rstrip("/") in seen:
            continue

        # Walk out from the link until we find the block holding its price.
        #
        # The block must cover exactly one *product*. A wrapper holding several
        # — a carousel, a grid, a basket summary — has one price that belongs
        # to none of them in particular, and accepting it would stamp that
        # price onto every product inside. Wrong prices are worse than missing
        # ones, so those are skipped.
        #
        # Distinct destinations, not link elements: almost every storefront
        # links the image and the title separately to the same product page,
        # and counting anchors rejected every one of those cards. Four stores
        # rendered perfectly readable prices and returned nothing because of
        # this.
        container = None
        for ancestor in _ancestors(link, 6):
            text = node_text(ancestor)
            if len(text) > max_card_chars:
                break
            # A price needs either a currency marker in the text or an explicit
            # price hook — some stores mark the element and leave the currency
            # to the page furniture, and requiring the marker loses them all.
            if not PRICE_TEXT.search(text) and not _price_hook_value(ancestor)[0]:
                continue
            if len(_product_urls(ancestor, link_match, origin)) != 1:
                break
            container = ancestor
            break
        if container is None:
            continue

        price, currency = _price_from(container)
        title = _title_from(container, link)
        if not price or not title:
            continue

        seen.add(url.rstrip("/"))
        rating, review_count = _rating_from(container)
        cards.append(
            Card(
                title=title,
                url=url,
                price=price,
                currency=currency,
                image=_image_from(container),
                rating=rating,
                review_count=review_count,
            )
        )
        if len(cards) >= max_cards:
            break

    return cards
