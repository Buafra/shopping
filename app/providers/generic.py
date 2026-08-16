"""A store scraper driven entirely by configuration.

Every bespoke provider in this package exists because its store ships
something worth reading directly — Carrefour's JSON API, Noon's `__NEXT_DATA__`
blob, Amazon's stable markup. Most shops ship none of that, and writing a
module of CSS selectors for each one is both slow to do and quick to rot.

This provider takes the other route: give it an origin, and it finds the search
page and reads product cards by shape (see `structural`). Adding a store then
costs one entry in `config.STORES` and no code, which is what makes it
practical to cover the long tail of component shops where the cheap stock is.

The trade-off is honest: a bespoke parser knows exactly which element holds the
price, and this one infers it. It is strict about what it accepts — no price or
no title means no offer — because a wrong price ranks first and misleads.
"""

from __future__ import annotations

import logging

from ..browser import render
from ..config import DEFAULT_SEARCH_PATTERNS, STORES, StoreSpec
from ..models import Offer
from ..net import FetchError, base_headers, fetch
from ..structural import PRICE_TEXT, Card, guess_product_path, parse_cards
from .base import Provider

log = logging.getLogger(__name__)

# More than this and a single store can eat its whole HTTP budget probing URLs
# that were never going to work.
MAX_CANDIDATE_URLS = 3


class GenericProvider(Provider):
    """Structural scraper for any storefront named in the registry."""

    def __init__(self, spec: StoreSpec) -> None:
        super().__init__(spec)
        if not spec.origin and not spec.search_urls:
            raise ValueError(
                f"store {spec.key!r} needs an origin or a search URL to be "
                f"config-driven"
            )
        self.origin = spec.origin or _origin_of(spec.search_urls[0])

    # -- URLs ---------------------------------------------------------------

    def search_urls(self, query: str) -> list[str]:
        """Every search page worth trying for this store, best first."""
        encoded = self.q(query)
        templates = self.spec.search_urls or tuple(
            pattern.format(origin=self.origin, q="{q}")
            for pattern in DEFAULT_SEARCH_PATTERNS
        )
        return [t.format(q=encoded) for t in templates][:MAX_CANDIDATE_URLS]

    def _url(self, query: str) -> str:
        """The primary search page — used by diagnose.py."""
        return self.search_urls(query)[0]

    # -- fetching -----------------------------------------------------------

    async def search_http(self, query: str, limit: int) -> list[Offer]:
        """Try each candidate search URL until one yields listings.

        A store that answers 200 with a "no results" page is indistinguishable
        from one whose search lives at a different path, so the only reliable
        test of a candidate URL is whether products come out of it.
        """
        first_error: FetchError | None = None

        for url in self.search_urls(query):
            try:
                response = await fetch(url, headers=base_headers())
            except FetchError as exc:
                first_error = first_error or exc
                log.debug("%s: %s failed (%s)", self.spec.key, url, exc.kind)
                continue

            offers = self._parse(response.text, limit)
            if offers:
                return offers

        # Nothing parsed anywhere. A transport failure is the more useful
        # diagnosis, so report it rather than the silent empty result.
        if first_error is not None:
            raise first_error
        return []

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        html = await render(self.search_urls(query)[0])
        return self._parse(html, limit)

    # -- parsing ------------------------------------------------------------

    def _parse(self, html: str, limit: int) -> list[Offer]:
        self.last_html = html
        link_match = self.spec.product_path or guess_product_path(html)
        if not link_match:
            # No repeated price-adjacent link: an empty result page, a bot
            # wall, or a shell that renders its products in JavaScript. run()
            # tells those apart; here there is simply nothing to read.
            return []

        cards = parse_cards(
            html, origin=self.origin, link_match=link_match, max_cards=limit * 4
        )
        return [self._offer(card) for card in cards]

    def describe_empty_result(self) -> tuple[str, str]:
        """A config-driven store has one failure mode a coded one does not.

        Its search URL is a guess. A wrong guess returns a perfectly healthy
        404 or home page, which "the markup changed" describes exactly wrongly
        — nothing changed and there are no selectors to check. The tell is
        whether the page has any prices on it at all.
        """
        html = self.last_html or ""
        if not html:
            return ("no page was returned by any candidate search URL", "parse")

        if not PRICE_TEXT.search(html):
            return (
                "the page loaded but contains no prices at all — the search "
                "URL is probably wrong for this store, or its results are "
                "rendered by JavaScript. Run: python diagnose.py "
                f"{self.spec.key}",
                "parse",
            )
        return (
            "prices are on the page but no product cards could be read from "
            "them — set `product_path` for this store in app/config.py, or run "
            f"python diagnose.py {self.spec.key} to see the card layout",
            "parse",
        )

    def _offer(self, card: Card) -> Offer:
        return self.make_offer(
            title=card.title,
            url=card.url,
            image=card.image,
            price=card.price,
            # A store that localises by IP quotes whatever currency it decided
            # the visitor wants, so the page wins over the configured default.
            currency=card.currency or self.spec.currency,
            rating=card.rating,
            review_count=card.review_count,
        )


def _origin_of(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def make(store_key: str) -> GenericProvider:
    return GenericProvider(STORES[store_key])
