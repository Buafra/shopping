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
import asyncio
import logging
import time
from urllib.parse import quote_plus

from selectolax.parser import HTMLParser

from ..browser import BrowserUnavailable
from ..browser import describe_error as describe_browser_error
from ..config import SETTINGS, StoreSpec
from ..models import Offer, StoreStatus
from ..net import FetchError

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
        error_kind: str | None = None
        fetch_succeeded = False
        offers: list[Offer] = []

        try:
            offers = await asyncio.wait_for(
                self.search_http(query, limit), SETTINGS.http_phase_timeout
            )
            fetch_succeeded = True
        except asyncio.TimeoutError:
            error, error_kind = (
                f"no HTTP response within {SETTINGS.http_phase_timeout:.0f}s",
                "timeout",
            )
            log.info("%s http path timed out", self.spec.key)
        except FetchError as exc:
            error, error_kind = str(exc), exc.kind
            log.info("%s http path failed: %s", self.spec.key, error)
        except Exception as exc:
            error, error_kind = f"{type(exc).__name__}: {exc}", "error"
            log.info("%s http path failed: %s", self.spec.key, error)

        if not offers and SETTINGS.use_browser_fallback:
            try:
                offers = await asyncio.wait_for(
                    self.search_browser(query, limit), SETTINGS.browser_phase_timeout
                )
                method = "browser"
                fetch_succeeded = True
                if offers:
                    error, error_kind = None, None
            except asyncio.TimeoutError:
                browser_error = (
                    f"browser did not finish loading within "
                    f"{SETTINGS.browser_phase_timeout:.0f}s"
                )
                error = error or browser_error
                error_kind = error_kind or "timeout"
                log.info("%s browser path timed out", self.spec.key)
            except BrowserUnavailable as exc:
                log.debug("%s browser path unavailable: %s", self.spec.key, exc)
            except Exception as exc:
                # Keep the HTTP diagnosis if we already have one — it names the
                # cause more precisely than Chromium's ERR_* string does.
                browser_error = describe_browser_error(
                    exc, getattr(self, "origin", self.spec.label)
                )
                error = error or browser_error
                error_kind = error_kind or "unreachable"
                log.info("%s browser path failed: %s", self.spec.key, browser_error)

        offers = self._postprocess(offers, limit)
        elapsed = int((time.perf_counter() - started) * 1000)

        if not offers and error is None:
            # The page came back fine but nothing parsed out of it. That is a
            # different problem from a network failure, and pointing at the
            # parser rather than the connection saves real debugging time.
            error, error_kind = (
                ("page fetched but no listings parsed — this store's markup has "
                 "probably changed, check its provider selectors", "parse")
                if fetch_succeeded
                else ("no matching results found", "no_results")
            )

        status = StoreStatus(
            store=self.spec.key,
            store_label=self.spec.label,
            market=self.spec.market,
            ok=bool(offers),
            offer_count=len(offers),
            elapsed_ms=elapsed,
            error=error,
            error_kind=error_kind,
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
