"""Sharaf DG (UAE electronics retailer) scraper.

Sharaf DG renders search results server-side, so a plain fetch usually works.
Their markup uses generic class names that change often, so selectors are kept
loose and every field degrades to None rather than failing the whole card.
"""

from __future__ import annotations

import json

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import (absolutise, clean_text, fetch, parse_int, parse_price,
                   parse_rating)
from .base import Provider

ORIGIN = "https://uae.sharafdg.com"

CARD_SELECTORS = [
    "div.product-item",
    "li.product-item",
    "div[class*='productCard']",
    "div.product-grid-item",
]


class SharafDGProvider(Provider):
    origin = ORIGIN

    def _url(self, query: str) -> str:
        return f"{ORIGIN}/?s={self.q(query)}&post_type=product"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        resp = await fetch(self._url(query), headers={"Accept-Language": "en-AE,en;q=0.9"})
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._url(query), wait_for="a[href*='/product/']")
        return self._parse(html, limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)

        offers = self._parse_jsonld(tree, limit)
        if offers:
            return offers

        cards = []
        for sel in CARD_SELECTORS:
            cards = tree.css(sel)
            if cards:
                break

        for card in cards:
            link = card.css_first("a[href*='/product/'], a[href]")
            title_node = card.css_first("h2, h3, .product-title, [class*='title']")
            price_node = card.css_first(".price ins .amount, .price .amount, [class*='price']")

            title = clean_text(title_node.text() if title_node else "")
            price = parse_price(price_node.text() if price_node else None)
            url = absolutise(link.attributes.get("href") if link else None, ORIGIN)
            if not title or price is None or not url:
                continue

            image_node = card.css_first("img")
            image = None
            if image_node:
                image = image_node.attributes.get("src") or image_node.attributes.get("data-src")

            rating_node = card.css_first("[class*='rating'], .star-rating")
            reviews_node = card.css_first("[class*='review-count'], [class*='ratingCount']")

            offers.append(
                self.make_offer(
                    title=title,
                    url=url,
                    image=image,
                    price=price,
                    rating=parse_rating(rating_node.text() if rating_node else None),
                    review_count=parse_int(reviews_node.text() if reviews_node else None),
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    def _parse_jsonld(self, tree, limit: int) -> list[Offer]:
        """Prefer schema.org Product data when the page ships it — it is far
        more stable than any CSS class on this site."""
        offers: list[Offer] = []
        for script in tree.css('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.text())
            except (json.JSONDecodeError, AttributeError):
                continue

            candidates = data if isinstance(data, list) else [data]
            for entry in candidates:
                if not isinstance(entry, dict):
                    continue
                if entry.get("@type") == "ItemList":
                    candidates.extend(
                        e.get("item", e) for e in entry.get("itemListElement", [])
                        if isinstance(e, dict)
                    )
                    continue
                if entry.get("@type") != "Product":
                    continue

                offer_block = entry.get("offers") or {}
                if isinstance(offer_block, list):
                    offer_block = offer_block[0] if offer_block else {}
                price = parse_price(offer_block.get("price") if isinstance(offer_block, dict) else None)
                title = clean_text(str(entry.get("name") or ""))
                url = entry.get("url") or (offer_block.get("url") if isinstance(offer_block, dict) else None)
                if not title or price is None or not url:
                    continue

                rating_block = entry.get("aggregateRating") or {}
                image = entry.get("image")
                if isinstance(image, list):
                    image = image[0] if image else None

                offers.append(
                    self.make_offer(
                        title=title,
                        url=absolutise(url, ORIGIN) or url,
                        image=image if isinstance(image, str) else None,
                        price=price,
                        rating=parse_rating(rating_block.get("ratingValue")
                                            if isinstance(rating_block, dict) else None),
                        review_count=parse_int(rating_block.get("reviewCount")
                                               if isinstance(rating_block, dict) else None),
                    )
                )
                if len(offers) >= limit * 2:
                    return offers
        return offers


def make() -> SharafDGProvider:
    return SharafDGProvider(STORES["sharaf_dg"])
