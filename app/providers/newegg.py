"""Newegg scraper — useful global comparison point for computing hardware.

Newegg renders search results server-side and exposes ratings as a CSS class
(`ic-rating-4`) rather than text, so the rating is read off the class name.
"""

from __future__ import annotations

import re

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import clean_text, fetch, parse_int, parse_price
from .base import Provider

ORIGIN = "https://www.newegg.com"

_RATING_CLASS = re.compile(r"rating-(\d)(?:-(\d))?")


class NeweggProvider(Provider):
    origin = ORIGIN

    def _url(self, query: str) -> str:
        return f"{ORIGIN}/p/pl?d={self.q(query)}"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        resp = await fetch(self._url(query), headers={"Accept-Language": "en-US,en;q=0.9"})
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._url(query), locale="en-US", wait_for=".item-cell")
        return self._parse(html, limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)
        offers: list[Offer] = []

        for card in tree.css(".item-cell"):
            title_node = card.css_first("a.item-title")
            if not title_node:
                continue
            title = clean_text(title_node.text())
            url = title_node.attributes.get("href")
            if not title or not url:
                continue

            price_node = card.css_first(".price-current")
            price = parse_price(price_node.text() if price_node else None)
            if price is None:
                continue

            ship_node = card.css_first(".price-ship")
            ship_text = clean_text(ship_node.text() if ship_node else "")
            if "free" in ship_text.lower():
                shipping, ship_known = 0.0, True
            else:
                parsed = parse_price(ship_text)
                shipping, ship_known = (parsed, True) if parsed is not None else (
                    self.spec.default_shipping, False
                )

            image_node = card.css_first("img")
            image = None
            if image_node:
                image = image_node.attributes.get("src") or image_node.attributes.get("data-src")
                if image and image.startswith("//"):
                    image = "https:" + image

            reviews_node = card.css_first(".item-rating-num")

            offers.append(
                self.make_offer(
                    title=title,
                    url=url.split("?")[0],
                    image=image,
                    price=price,
                    shipping=shipping,
                    shipping_is_estimate=not ship_known,
                    rating=self._rating(card),
                    review_count=parse_int(reviews_node.text() if reviews_node else None),
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    @staticmethod
    def _rating(card) -> float | None:
        node = card.css_first("i.rating")
        if not node:
            return None
        match = _RATING_CLASS.search(node.attributes.get("class", ""))
        if not match:
            return None
        whole, half = match.group(1), match.group(2)
        value = float(whole) + (0.5 if half else 0.0)
        return value if 0 < value <= 5 else None


def newegg() -> NeweggProvider:
    return NeweggProvider(STORES["newegg"])
