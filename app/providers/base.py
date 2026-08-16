"""Base class every store scraper implements.

A provider's only job is: given a query, return raw `Offer`s in the store's own
currency. Normalisation, FX and ranking all happen later, so a scraper never
needs to know about landed cost or scoring.

Subclasses override `search_http` and optionally `search_browser`. The base
`run()` tries HTTP first (cheap) and falls back to the browser (expensive) when
HTTP returns nothing or is blocked.
"""

from __future__ import annotations

import abc
import logging
import time
from urllib.parse import quote_plus

from selectolax.parser import HTMLParser

from ..browser import BrowserUnavailable
from ..config import SETTINGS, StoreSpec
from ..models import Offer, StoreStatus

log = logging.getLogger(__name__)


class Provider(abc.ABC):
    spec: StoreSpec
    origin: str

    def __init__(self, spec: StoreSpec) -> None:
        self.spec = spec

    # -- to implement -------------------------------------------------------

    @abc.abstractmethod
    async def search_http(self, query: str, limit: int) -> list[Offer]:
        """Fetch and parse results using a plain HTTP request."""

    async def search_browser(self, query: str, limit: int) -> list[Offer]:
        """Optional Playwright path. Default: unsupported."""
        raise BrowserUnavailable(f"{self.spec.key} has no browser path")

    # -- helpers for subclasses --------------------------------------------

    @staticmethod
    def q(query: str) -> str:
        return quote_plus(query.strip())

    @staticmethod
    def dom(html: str) -> HTMLParser:
        return HTMLParser(html)

    def make_offer(self, **kwargs) -> Offer:
        """Build an Offer with this store's defaults already applied."""
        kwargs.setdefault("store", self.spec.key)
        kwargs.setdefault("store_label", self.spec.label)
        kwargs.setdefault("market", self.spec.market)
        kwargs.setdefault("country", self.spec.country)
        kwargs.setdefault("currency", self.spec.currency)
        kwargs.setdefault("delivery_days", self.spec.default_delivery_days)
        # Only fall back to the estimate when shipping is genuinely unknown.
        # A real 0.0 means free shipping and must survive — treating it as
        # "missing" would silently add a phantom fee to the landed cost.
        if kwargs.get("shipping") is None:
            kwargs["shipping"] = self.spec.default_shipping
        return Offer(**kwargs)

    # -- orchestration ------------------------------------------------------

    async def run(self, query: str, limit: int | None = None) -> tuple[list[Offer], StoreStatus]:
        """Run this provider, never raising. Failures come back on the status."""
        limit = limit or SETTINGS.per_store_results
        started = time.perf_counter()
        method = "http"
        error: str | None = None
        offers: list[Offer] = []

        try:
            offers = await self.search_http(query, limit)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.info("%s http path failed: %s", self.spec.key, error)

        if not offers and SETTINGS.use_browser_fallback:
            try:
                offers = await self.search_browser(query, limit)
                method = "browser"
                if offers:
                    error = None
            except BrowserUnavailable as exc:
                log.debug("%s browser path unavailable: %s", self.spec.key, exc)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                log.info("%s browser path failed: %s", self.spec.key, error)

        offers = self._postprocess(offers, limit)
        elapsed = int((time.perf_counter() - started) * 1000)

        if not offers and error is None:
            error = "no matching results found"

        status = StoreStatus(
            store=self.spec.key,
            store_label=self.spec.label,
            market=self.spec.market,
            ok=bool(offers),
            offer_count=len(offers),
            elapsed_ms=elapsed,
            error=error,
            method=method,
        )
        return offers, status

    def _postprocess(self, offers: list[Offer], limit: int) -> list[Offer]:
        """Drop junk, de-duplicate by URL, keep the cheapest N."""
        seen: set[str] = set()
        kept: list[Offer] = []
        for offer in offers:
            if not offer.title or not offer.url or offer.price <= 0:
                continue
            key = offer.url.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            kept.append(offer)

        kept.sort(key=lambda o: o.price)
        return kept[:limit]
