"""Amazon search scraper, shared by amazon.ae and amazon.com.

Amazon serves several different search layouts and rotates them, so each field
is read through a list of candidate selectors rather than a single one. It also
blocks datacentre IPs aggressively, hence the browser fallback.
"""

from __future__ import annotations

from ..browser import render
from ..config import STORES
from ..models import Offer
from ..net import (absolutise, clean_text, fetch, parse_int, parse_price,
                   parse_rating)
from .base import Provider

RESULT_SELECTOR = 'div[data-component-type="s-search-result"]'

TITLE_SELECTORS = ["h2 a span", "h2 span", "[data-cy='title-recipe'] span", "h2"]
LINK_SELECTORS = ["h2 a", "a.a-link-normal.s-no-outline", "a.a-link-normal"]
PRICE_SELECTORS = [
    "span.a-price > span.a-offscreen",
    "span.a-price span.a-offscreen",
    "span.a-color-price",
]
RATING_SELECTORS = ["span.a-icon-alt", "i.a-icon-star-small span", "[aria-label*='out of 5']"]
REVIEWS_SELECTORS = [
    "span.a-size-base.s-underline-text",
    "a[href*='#customerReviews'] span",
    "span[aria-label*='ratings']",
]


def _first_text(node, selectors: list[str]) -> str:
    for sel in selectors:
        found = node.css_first(sel)
        if found:
            text = clean_text(found.text())
            if text:
                return text
    return ""


def _first_attr(node, selectors: list[str], attr: str) -> str | None:
    for sel in selectors:
        found = node.css_first(sel)
        if found and found.attributes.get(attr):
            return found.attributes[attr]
    return None


class AmazonProvider(Provider):
    def __init__(self, spec) -> None:
        super().__init__(spec)
        self.origin = "https://www.amazon.ae" if spec.country == "AE" else "https://www.amazon.com"
        self.locale = "en-AE,en;q=0.9,ar;q=0.8" if spec.country == "AE" else "en-US,en;q=0.9"

    def _url(self, query: str) -> str:
        return f"{self.origin}/s?k={self.q(query)}&ref=nb_sb_noss"

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        resp = await fetch(self._url(query), headers={"Accept-Language": self.locale})
        return self._parse(resp.text, limit)

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(
            self._url(query),
            wait_for=RESULT_SELECTOR,
            locale="en-AE" if self.spec.country == "AE" else "en-US",
            timezone="Asia/Dubai" if self.spec.country == "AE" else "America/New_York",
        )
        return self._parse(html, limit)

    def _parse(self, html: str, limit: int) -> list[Offer]:
        tree = self.dom(html)
        offers: list[Offer] = []

        for card in tree.css(RESULT_SELECTOR):
            # Sponsored placements are ads, not honest price signals.
            if card.css_first("span.puis-sponsored-label-text") or card.css_first(
                "[data-component-type='sp-sponsored-result']"
            ):
                continue

            title = _first_text(card, TITLE_SELECTORS)
            price = parse_price(_first_text(card, PRICE_SELECTORS))
            href = _first_attr(card, LINK_SELECTORS, "href")
            if not title or price is None or not href:
                continue

            asin = card.attributes.get("data-asin")
            url = f"{self.origin}/dp/{asin}" if asin else absolutise(href, self.origin)
            if not url:
                continue

            rating_text = _first_text(card, RATING_SELECTORS)
            rating = parse_rating(rating_text.split(" out of")[0] if rating_text else None)

            image = _first_attr(card, ["img.s-image", "img"], "src")

            offers.append(
                self.make_offer(
                    title=title,
                    url=url,
                    image=image,
                    price=price,
                    rating=rating,
                    review_count=parse_int(_first_text(card, REVIEWS_SELECTORS)),
                    in_stock=True,
                )
            )
            if len(offers) >= limit * 2:
                break

        return offers


def amazon_ae() -> AmazonProvider:
    return AmazonProvider(STORES["amazon_ae"])


def amazon_com() -> AmazonProvider:
    return AmazonProvider(STORES["amazon_com"])
