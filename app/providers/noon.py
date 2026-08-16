"""Noon UAE scraper.

Noon is a Next.js app, so the search results are already sitting in the
`__NEXT_DATA__` JSON blob on the page — far more reliable than reading the
rendered DOM. We walk the blob looking for anything that has the shape of a
product record, because Noon moves the exact key path around between releases.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import clean_text, fetch, parse_int, parse_price, parse_rating
from .base import Provider

ORIGIN = "https://www.noon.com"
IMAGE_CDN = "https://f.nooncdn.com/p/"


def _iter_dicts(node: Any) -> Iterator[dict]:
    """Depth-first walk over every dict nested anywhere inside `node`."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dicts(value)


def _looks_like_product(record: dict) -> bool:
    has_id = any(k in record for k in ("sku", "productSku", "offerCode"))
    has_name = any(k in record for k in ("name", "productName", "title"))
    has_price = any(k in record for k in ("salePrice", "price", "offerPrice"))
    return has_id and has_name and has_price


def _pick(record: dict, *keys: str) -> Any:
    for key in keys:
        if record.get(key) not in (None, "", []):
            return record[key]
    return None


def _rating_value(record: dict) -> Any:
    """Noon quotes the rating either as a bare number or as {"value": 4.3}."""
    raw = _pick(record, "product_rating", "rating", "averageRating")
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("rating")
    return raw


class NoonProvider(Provider):
    origin = ORIGIN

    def _url(self, query: str) -> str:
        return f"{ORIGIN}/uae-en/search/?q={self.q(query)}"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        resp = await fetch(
            self._url(query),
            headers={"Accept-Language": "en-AE,en;q=0.9", "Referer": f"{ORIGIN}/uae-en/"},
        )
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._url(query), wait_for="script#__NEXT_DATA__")
        return self._parse(html, limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)
        blob = tree.css_first("script#__NEXT_DATA__")
        if not blob:
            return self._parse_dom(tree, limit)

        try:
            data = json.loads(blob.text())
        except json.JSONDecodeError:
            return self._parse_dom(tree, limit)

        offers: list[Offer] = []
        seen: set[str] = set()

        for record in _iter_dicts(data):
            if not _looks_like_product(record):
                continue

            sku = str(_pick(record, "sku", "productSku", "offerCode") or "")
            if not sku or sku in seen:
                continue

            title = clean_text(str(_pick(record, "name", "productName", "title") or ""))
            price = parse_price(_pick(record, "salePrice", "price", "offerPrice"))
            if not title or price is None:
                continue

            seen.add(sku)

            image_key = _pick(record, "image_key", "imageKey", "image")
            image = None
            if isinstance(image_key, str):
                image = image_key if image_key.startswith("http") else f"{IMAGE_CDN}{image_key}"

            offers.append(
                self.make_offer(
                    title=title,
                    url=f"{ORIGIN}/uae-en/{sku}/p/",
                    image=image,
                    price=price,
                    rating=parse_rating(_rating_value(record)),
                    review_count=parse_int(
                        _pick(record, "num_ratings", "ratingCount", "reviewCount")
                    ),
                    in_stock=record.get("isBuyable", True) is not False,
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    def _parse_dom(self, tree, limit: int) -> list[Offer]:
        """Fallback for when the JSON blob is missing or restructured."""
        offers: list[Offer] = []
        for card in tree.css("a[href*='/p/']"):
            href = card.attributes.get("href", "")
            title_node = card.css_first("[data-qa*='name'], h2, .productTitle, span[title]")
            price_node = card.css_first("[class*='rice'] strong, strong.amount, [data-qa*='price']")
            title = clean_text(title_node.text() if title_node else "")
            price = parse_price(price_node.text() if price_node else None)
            if not title or price is None:
                continue
            url = href if href.startswith("http") else ORIGIN + href
            offers.append(self.make_offer(title=title, url=url, price=price))
            if len(offers) >= limit * 2:
                break
        return offers


def noon() -> NoonProvider:
    return NoonProvider(STORES["noon"])
