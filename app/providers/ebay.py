"""eBay scraper.

eBay renders search results server-side and does not block plain clients hard,
so this is the most dependable of the global providers. eBay also states real
shipping cost per listing, which matters a lot for landed-cost comparison.
"""

from __future__ import annotations

import re

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import clean_text, fetch, parse_int, parse_price, parse_rating
from ..structural import parse_cards
from .base import Provider

ORIGIN = "https://www.ebay.com"

# eBay has been migrating from `.s-item` to `.s-card`; support both.
CARD_SELECTORS = ["li.s-item", "li.s-card", "div.s-item__wrapper"]

# eBay writes free postage several ways: "Free shipping",
# "Free International Shipping", "Free 3 day delivery".
_FREE_SHIPPING = re.compile(
    r"\bfree\b(?:\s+\w+){0,3}\s+(?:shipping|delivery|postage)\b", re.I
)
_DAYS = re.compile(r"(\d+)\s*(?:-\s*(\d+))?\s*(?:business\s+)?day", re.I)


class EbayProvider(Provider):
    origin = ORIGIN

    def _url(self, query: str) -> str:
        # LH_PrefLoc=2 widens to worldwide sellers who ship internationally.
        return f"{ORIGIN}/sch/i.html?_nkw={self.q(query)}&_sop=15&LH_PrefLoc=2"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        """Fetch search results, priming a session first.

        eBay answers 403 to a search request that arrives with no cookies and
        no referer — a browser never does that, it lands on the site first.
        The shared client keeps the cookie jar, so one cheap homepage request
        makes the search look like what it is: a second page view.
        """
        try:
            await fetch(ORIGIN, headers={"Accept-Language": "en-US,en;q=0.9"})
        except Exception:
            pass  # priming is best-effort; the search may still succeed

        resp = await fetch(
            self._url(query),
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{ORIGIN}/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._url(query), locale="en-US", wait_for="li.s-item, li.s-card")
        return self._parse(html, limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        self.last_html = html
        offers = self._parse_cards(html, limit)
        # eBay has been rotating between `.s-item` and `.s-card` layouts; when
        # neither matches, fall back to structure rather than reporting the
        # store dead. This is the same mechanism that recovered Carrefour.
        return offers or self._parse_structural(html, limit)

    def _parse_structural(self, html: str, limit: int) -> list[Offer]:
        return [
            self.make_offer(
                title=card.title,
                url=card.url,
                image=card.image,
                price=card.price,
                currency=card.currency or self.spec.currency,
                rating=card.rating,
                review_count=card.review_count,
            )
            for card in parse_cards(
                html, origin=ORIGIN, link_match="/itm/", max_cards=limit * 2
            )
        ]

    def _parse_cards(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)

        cards = []
        for sel in CARD_SELECTORS:
            cards = tree.css(sel)
            if len(cards) > 1:
                break

        offers: list[Offer] = []
        for card in cards:
            title_node = card.css_first(
                ".s-item__title, .s-card__title, [role='heading'] span"
            )
            title = clean_text(title_node.text() if title_node else "")
            # eBay injects a literal "Shop on eBay" placeholder as the first card.
            if not title or title.lower().startswith("shop on ebay"):
                continue

            price_node = card.css_first(".s-item__price, .s-card__price")
            price = parse_price(price_node.text() if price_node else None)
            if price is None:
                continue

            link_node = card.css_first("a.s-item__link, a.su-link, a[href*='/itm/']")
            url = link_node.attributes.get("href") if link_node else None
            if not url:
                continue
            url = url.split("?")[0]

            ship_node = card.css_first(
                ".s-item__shipping, .s-item__logisticsCost, [class*='shipping']"
            )
            ship_text = clean_text(ship_node.text() if ship_node else "")
            if _FREE_SHIPPING.search(ship_text):
                shipping, ship_known = 0.0, True
            else:
                parsed = parse_price(ship_text)
                shipping, ship_known = (parsed, True) if parsed is not None else (
                    self.spec.default_shipping, False
                )

            rating_node = card.css_first(".x-star-rating span, [class*='review-star']")
            reviews_node = card.css_first(".s-item__reviews-count span, [class*='reviewCount']")

            image_node = card.css_first("img")
            image = None
            if image_node:
                image = image_node.attributes.get("src") or image_node.attributes.get("data-src")

            offers.append(
                self.make_offer(
                    title=title,
                    url=url,
                    image=image,
                    price=price,
                    shipping=shipping,
                    shipping_is_estimate=not ship_known,
                    rating=parse_rating(
                        rating_node.text().split(" out of")[0] if rating_node else None
                    ),
                    review_count=parse_int(reviews_node.text() if reviews_node else None),
                    delivery_days=self._delivery_days(card),
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    def _delivery_days(self, card) -> int:
        node = card.css_first(".s-item__deliveryOptions, [class*='delivery']")
        if node:
            match = _DAYS.search(node.text())
            if match:
                # Use the slow end of a range — that is what the buyer plans around.
                return int(match.group(2) or match.group(1))
        return self.spec.default_delivery_days


def make() -> EbayProvider:
    return EbayProvider(STORES["ebay"])
