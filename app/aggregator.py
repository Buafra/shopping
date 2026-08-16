"""Fan out one query across every enabled store, then rank the union.

Stores run concurrently with a bounded semaphore and a hard per-store deadline,
so one slow or hanging site cannot hold up the whole search. A store that fails
still appears in the response with its error, because silently returning fewer
results would misrepresent how complete the comparison is.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import fx, matching, pricing, scoring
from .config import SETTINGS
from .models import Market, Offer, SearchResponse, StoreStatus
from .providers import Provider, build_all

log = logging.getLogger(__name__)

# Per-store ceiling: HTTP attempt + browser fallback both have to fit.
STORE_DEADLINE = 45.0


async def _run_provider(
    provider: Provider, query: str, limit: int, sem: asyncio.Semaphore
) -> tuple[list[Offer], StoreStatus]:
    async with sem:
        try:
            return await asyncio.wait_for(
                provider.run(query, limit), timeout=STORE_DEADLINE
            )
        except asyncio.TimeoutError:
            return [], StoreStatus(
                store=provider.spec.key,
                store_label=provider.spec.label,
                market=provider.spec.market,
                ok=False,
                elapsed_ms=int(STORE_DEADLINE * 1000),
                error=f"timed out after {STORE_DEADLINE:.0f}s",
            )
        except Exception as exc:  # defensive: provider.run already traps most
            return [], StoreStatus(
                store=provider.spec.key,
                store_label=provider.spec.label,
                market=provider.spec.market,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )


def _diagnose_total_failure(statuses: list[StoreStatus]) -> str:
    """When nothing came back, name the most likely single cause.

    "Every store blocked you" and "your machine has no route to the internet"
    look identical in a list of red rows, but they need opposite fixes.
    """
    kinds = [s.error_kind for s in statuses if s.error_kind]
    if not kinds:
        return "No store returned results."

    dominant = max(set(kinds), key=kinds.count)
    share = kinds.count(dominant)
    everywhere = share == len(statuses)

    if dominant == "unreachable":
        return (
            "No store could be reached at all"
            + (" — every single one failed to connect, which points at this "
               "machine's network, proxy or DNS rather than at the stores."
               if everywhere else
               ". Check connectivity and any proxy settings.")
        )
    if dominant == "blocked":
        return (
            "Every store refused the request as automated traffic. Cloud and "
            "datacentre IPs are blocked aggressively — set SCRAPER_PROXY to a "
            "residential proxy, or run this from a home connection."
        )
    if dominant == "timeout":
        return ("Every store timed out. The network may be slow or throttled — "
                "try raising REQUEST_TIMEOUT.")
    if dominant == "parse":
        return ("Stores responded, but no listing could be parsed from any of "
                "them. That points at the parsers, not the network — the "
                "selectors in app/providers/ likely need updating.")
    return ("No store returned results for this query. Try a broader search "
            "term, or check the per-store errors below.")


async def search(
    query: str,
    *,
    markets: list[Market] | None = None,
    stores: list[str] | None = None,
    limit_per_store: int | None = None,
    max_results: int = 40,
) -> SearchResponse:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")

    started = time.perf_counter()
    limit = limit_per_store or SETTINGS.per_store_results
    notes: list[str] = []

    providers = build_all(markets=markets, only=stores)
    if not providers:
        raise ValueError("no stores match the requested filters")

    sem = asyncio.Semaphore(SETTINGS.max_concurrent_stores)

    rates_task = asyncio.create_task(fx.refresh_rates())
    store_results = await asyncio.gather(
        *(_run_provider(p, query, limit, sem) for p in providers)
    )
    rates, fx_source = await rates_task

    all_offers: list[Offer] = []
    statuses: list[StoreStatus] = []
    for offers, status in store_results:
        all_offers.extend(offers)
        statuses.append(status)

    statuses.sort(key=lambda s: (s.market.value, s.store_label))

    if not all_offers:
        notes.append(_diagnose_total_failure(statuses))
        return SearchResponse(
            query=query, offers=[], stores=statuses, fx_rates=rates,
            fx_source=fx_source, notes=notes,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    pricing.normalise_offers(all_offers, rates)

    relevant, dropped = matching.filter_relevant(all_offers, query)
    if dropped:
        notes.append(
            f"Filtered out {dropped} listing(s) that looked like accessories or "
            f"mismatches rather than the product searched for."
        )

    ranked = scoring.rank(relevant)[:max_results]
    recommendation = scoring.build_recommendation(ranked)

    failed = [s for s in statuses if not s.ok]
    if failed:
        notes.append(
            f"{len(failed)} of {len(statuses)} stores returned nothing: "
            + ", ".join(s.store_label for s in failed)
            + ". The comparison is still valid for the stores that answered."
        )

    if fx_source == "fallback":
        notes.append(
            "Live FX feed unavailable — conversions use built-in rates "
            "(the AED/USD peg is fixed, so USD figures remain accurate)."
        )

    local_count = sum(1 for o in ranked if o.market == Market.LOCAL)
    if local_count == 0:
        notes.append("No UAE store returned a match, so this compares global sellers only.")

    return SearchResponse(
        query=query,
        offers=ranked,
        stores=statuses,
        recommendation=recommendation,
        fx_rates={k: v for k, v in rates.items() if k in {"USD", "EUR", "GBP", "CNY", "SAR"}},
        fx_source=fx_source,
        notes=notes,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
