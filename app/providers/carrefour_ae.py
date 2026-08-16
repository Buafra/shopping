"""Carrefour UAE scraper.

Carrefour's storefront is powered by a public JSON search endpoint, which is
both faster and far more stable than parsing their React output. The endpoint
needs a store identifier and a language header; if it changes shape we fall
back to rendering the search page.
"""

from __future__ import annotations

from typing import Any

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import clean_text, fetch, parse_int, parse_price, parse_rating
from .base import Provider

ORIGIN = "https://www.carrefouruae.com"

# Carrefour versions this endpoint and retires old ones without notice, and a
# retired version answers 403 rather than 404. Try each in turn instead of
# betting the whole store on one guess.
API_CANDIDATES = [
    f"{ORIGIN}/api/v8/search",
    f"{ORIGIN}/api/v7/search",
    f"{ORIGIN}/api/v1/menu/search",
]
API = API_CANDIDATES[0]  # kept for callers/tests that patch a single endpoint

# Carrefour segments its catalogue by fulfilment area; this is the default
# Dubai "market place" store used by the public site.
DEFAULT_STORE_ID = "mafuae"
DEFAULT_AREA = "Dubai"


class CarrefourProvider(Provider):
    origin = ORIGIN

    def _page_url(self, query: str) -> str:
        return f"{ORIGIN}/mafuae/en/v4/search?keyword={self.q(query)}"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        # `API` is honoured first so a patched or pinned endpoint still wins.
        endpoints = [API] + [u for u in API_CANDIDATES if u != API]
        last_error: Exception | None = None

        for endpoint in endpoints:
            try:
                return await self._search_endpoint(endpoint, query, limit)
            except Exception as exc:
                last_error = exc
                continue

        raise last_error if last_error else RuntimeError("no Carrefour endpoint tried")

    async def _search_endpoint(self, endpoint: str, query: str, limit: int) -> list[Offer]:
        resp = await fetch(
            endpoint,
            params={
                "keyword": query,
                "currentPage": 0,
                "filter": "",
                "pageSize": max(limit * 2, 20),
                "maxPrice": "",
                "minPrice": "",
                "sortBy": "relevance",
                "lang": "en",
                "displayCurr": "AED",
                "latitude": "25.2048",
                "longitude": "55.2708",
            },
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-AE",
                "storeid": DEFAULT_STORE_ID,
                "userid": "anonymous",
                "intent": "STANDARD",
                "appid": "Reactweb",
                "Referer": self._page_url(query),
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        return self._parse_json(resp.json(), limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self._page_url(query), wait_for="[data-testid='product_name']")
        return self._parse_dom(html, limit)

    def _parse_json(self, payload: Any, limit: int) -> list[Offer]:
        products = []
        if isinstance(payload, dict):
            products = (
                payload.get("products")
                or payload.get("data", {}).get("products")
                or payload.get("results")
                or []
            )
        if not isinstance(products, list):
            return []

        offers: list[Offer] = []
        for item in products:
            if not isinstance(item, dict):
                continue

            title = clean_text(str(item.get("name") or item.get("title") or ""))
            price_block = item.get("price") or {}
            price = parse_price(
                price_block.get("price")
                if isinstance(price_block, dict)
                else price_block
            ) or parse_price(item.get("salePrice"))
            if not title or price is None:
                continue

            link = item.get("links", {}).get("productUrl", {}).get("href") if isinstance(
                item.get("links"), dict
            ) else None
            product_id = item.get("id") or item.get("productId") or ""
            url = (
                f"{ORIGIN}{link}" if isinstance(link, str) and link.startswith("/")
                else link or f"{ORIGIN}/mafuae/en/p/{product_id}"
            )

            images = item.get("images") or {}
            image = None
            if isinstance(images, dict):
                thumbs = images.get("thumbnails") or images.get("small") or []
                image = thumbs[0] if isinstance(thumbs, list) and thumbs else None
            elif isinstance(images, list) and images:
                image = images[0] if isinstance(images[0], str) else None

            rating_block = item.get("rating") or {}
            rating = parse_rating(
                rating_block.get("value") if isinstance(rating_block, dict) else rating_block
            )
            reviews = parse_int(
                rating_block.get("count") if isinstance(rating_block, dict)
                else item.get("reviewCount")
            )

            offers.append(
                self.make_offer(
                    title=title,
                    url=url,
                    image=image,
                    price=price,
                    rating=rating,
                    review_count=reviews,
                    in_stock=item.get("stock", {}).get("stockLevelStatus") != "outOfStock"
                    if isinstance(item.get("stock"), dict) else True,
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers

    def _parse_dom(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)
        offers: list[Offer] = []
        for card in tree.css("[data-testid='product_card'], li[class*='product']"):
            name = card.css_first("[data-testid='product_name'], a[href*='/p/']")
            price_node = card.css_first("[data-testid='product_price'], [class*='price']")
            link = card.css_first("a[href*='/p/']")
            title = clean_text(name.text() if name else "")
            price = parse_price(price_node.text() if price_node else None)
            href = link.attributes.get("href") if link else None
            if not title or price is None or not href:
                continue
            url = href if href.startswith("http") else ORIGIN + href
            offers.append(self.make_offer(title=title, url=url, price=price))
            if len(offers) >= limit * 2:
                break
        return offers


def make() -> CarrefourProvider:
    return CarrefourProvider(STORES["carrefour_ae"])
