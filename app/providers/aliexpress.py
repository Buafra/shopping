"""AliExpress scraper.

AliExpress is fully client-rendered and hides its data in an inline
`window._dida_config_._init_data_` assignment. We extract that JSON when it is
present and walk it for product records; otherwise we render the page and read
the DOM. Ratings there are quoted out of 5 already.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import (clean_text, extract_json_object, fetch, parse_int,
                   parse_price, parse_rating)
from .base import Provider

ORIGIN = "https://www.aliexpress.com"


def _iter_dicts(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dicts(value)


class AliExpressProvider(Provider):
    origin = ORIGIN

    def _url(self, query: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        return f"{ORIGIN}/w/wholesale-{slug}.html?SearchText={self.q(query)}"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        resp = await fetch(
            self._url(query),
            headers={"Accept-Language": "en-US,en;q=0.9", "Referer": ORIGIN},
        )
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._url(query), locale="en-US", wait_for="a[href*='/item/']")
        offers = self._parse(html, limit)
        return offers or self._parse_dom(self.dom(html), limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        raw = extract_json_object(html, "_init_data_")
        if not raw:
            return self._parse_dom(self.dom(html), limit)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._parse_dom(self.dom(html), limit)

        offers: list[Offer] = []
        seen: set[str] = set()

        for record in _iter_dicts(data):
            product_id = record.get("productId") or record.get("product_id")
            if not product_id or str(product_id) in seen:
                continue

            title = clean_text(str(record.get("title") or record.get("productTitle") or ""))
            price = parse_price(
                record.get("salePrice")
                or record.get("targetSalePrice")
                or record.get("minPrice")
            )
            if not title or price is None:
                continue

            seen.add(str(product_id))

            image = record.get("image") or record.get("productImage")
            if isinstance(image, str) and image.startswith("//"):
                image = "https:" + image

            offers.append(
                self.make_offer(
                    title=title,
                    url=f"{ORIGIN}/item/{product_id}.html",
                    image=image if isinstance(image, str) else None,
                    price=price,
                    rating=parse_rating(
                        record.get("evaluateRate") or record.get("averageStar")
                    ),
                    review_count=parse_int(
                        record.get("tradeCount") or record.get("orders")
                    ),
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    def _parse_dom(self, tree, limit: int) -> list[Offer]:
        offers: list[Offer] = []
        for card in tree.css("a[href*='/item/']"):
            href = card.attributes.get("href", "")
            item_id = re.search(r"/item/(\d+)\.html", href)
            if not item_id:
                continue

            title_node = card.css_first("h3, [class*='title'], [title]")
            title = clean_text(
                title_node.text() if title_node else card.attributes.get("title", "")
            )
            price_node = card.css_first("[class*='price']")
            price = parse_price(price_node.text() if price_node else None)
            if not title or price is None:
                continue

            offers.append(
                self.make_offer(
                    title=title,
                    url=f"{ORIGIN}/item/{item_id.group(1)}.html",
                    price=price,
                )
            )
            if len(offers) >= limit * 2:
                break
        return offers


def make() -> AliExpressProvider:
    return AliExpressProvider(STORES["aliexpress"])
