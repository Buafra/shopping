"""Fan out one query across every enabled store, then rank the union.

Stores run concurrently with a bounded semaphore and a hard per-store deadline,
so one slow or hanging site cannot hold up the whole search. A store that fails
still appears in the response with its error, because silently returning fewer
results would misrepresent how complete the comparison is.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from . import blocklist, category, fx, matching, pricing, scoring
from .config import CHEAPEST_WEIGHTS, SETTINGS
from .models import DroppedListing, Market, Offer, SearchResponse, StoreStatus
from .providers import Provider, build_all

log = logging.getLogger(__name__)

# Per-store ceiling. Derived from the phase budgets rather than hard-coded, so
# it can never be shorter than the work it is meant to contain — a mismatch
# silently kills the browser fallback for exactly the slow stores that need it.
STORE_DEADLINE = (
    SETTINGS.http_phase_timeout + SETTINGS.browser_phase_timeout + 10.0
)


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


def _explain_no_stores(stores: list[str] | None, markets: list[Market] | None) -> str:
    """Say which named store could not be used, and why.

    "No stores match the requested filters" is true and useless: the usual
    cause is a store switched off by DISABLED_STORES in a shell the user set
    up an hour ago and has since forgotten.
    """
    from .config import STORES

    if not stores:
        market_names = ", ".join(m.value for m in markets) if markets else "any"
        return f"no stores are enabled for the {market_names} market"

    named = [s.strip() for s in stores if s.strip()]
    unknown = [s for s in named if s not in STORES]
    disabled = [s for s in named if s in STORES and not STORES[s].enabled]

    parts = []
    if unknown:
        parts.append(
            f"unknown store(s): {', '.join(unknown)}. Known: {', '.join(STORES)}"
        )
    if disabled:
        parts.append(
            f"{', '.join(disabled)} is switched off by DISABLED_STORES "
            f"(currently: {os.environ.get('DISABLED_STORES', '')!r}). "
            f"Clear it to use it again"
        )
    if not parts and markets:
        market_names = ", ".join(m.value for m in markets)
        parts.append(
            f"{', '.join(named)} is not in the {market_names} market"
        )
    return "; ".join(parts) or "no stores match the requested filters"


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
        if SETTINGS.proxy_url:
            host = SETTINGS.proxy_url.split("@")[-1].rstrip("/")
            return (
                f"No store could be reached, and SCRAPER_PROXY is set to {host}. "
                f"A proxy that cannot be resolved or reached blocks every store at "
                f"once, exactly like this. Verify the host and credentials, or "
                f"unset SCRAPER_PROXY to go direct."
            )
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
    include_used: bool = False,
    cheapest_first: bool = False,
) -> SearchResponse:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")

    started = time.perf_counter()
    limit = limit_per_store or SETTINGS.per_store_results
    notes: list[str] = []

    providers = build_all(markets=markets, only=stores)
    if not providers:
        raise ValueError(_explain_no_stores(stores, markets))

    # Specialist stores join a search they can answer and sit out one they
    # cannot. Naming stores explicitly overrides this — an explicit request is
    # a decision, not a suggestion.
    specialists: list[str] = []
    if not stores:
        general = [
            p for p in providers
            if category.store_matches_query(p.spec.tags, query)
        ]
        specialists = [
            p.spec.label for p in general if p.spec.tags
        ]
        providers = general or providers

    # Skip stores that refused us recently. Explicitly naming stores overrides
    # this — asking for a store by name is a request to try it regardless.
    skipped: dict[str, str] = {}
    rested: dict[str, str] = {}
    if not stores and SETTINGS.skip_blocked_hours > 0:
        try:
            skipped = blocklist.blocked(SETTINGS.skip_blocked_hours)
            # Stores that answer but never with anything readable cost the full
            # browser budget on every search — two of them alone were adding
            # twenty seconds to a run they contributed nothing to.
            rested = blocklist.resting()
            skipped = {**rested, **skipped}
        except Exception:
            log.warning("could not read the blocked-store list", exc_info=True)
        if skipped:
            remaining = [p for p in providers if p.spec.key not in skipped]
            # Never skip everything: with no store left there is nothing to
            # compare, and an out-of-date record should not silence the app.
            if remaining:
                providers = remaining
            else:
                skipped = {}

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

    # Record fresh blocks so the next search does not pay for them again.
    for status in statuses:
        try:
            if status.error_kind in blocklist.BLOCKING_KINDS:
                blocklist.remember(status.store, status.error or "blocked")
            if status.ok:
                blocklist.record_outcome(status.store, True)
            elif status.error_kind in blocklist.UNPRODUCTIVE_KINDS:
                blocklist.record_outcome(
                    status.store, False, status.error or "no listings"
                )
        except Exception:
            log.warning("could not record outcome for %s", status.store, exc_info=True)

    if specialists:
        notes.append(
            f"Added {len(specialists)} component specialist(s) for this search: "
            + ", ".join(sorted(specialists))
            + ". They stock parts that general retailers do not, and sit out "
            "searches outside that category."
        )

    if rested:
        notes.append(
            f"Resting {len(rested)} store(s) that returned nothing "
            f"{blocklist.REST_AFTER_FAILURES} runs in a row: "
            + ", ".join(sorted(rested))
            + f". They are retried after {blocklist.REST_HOURS:.0f}h — or now, "
            f"with --stores. This is a guess about them, not their decision, so "
            f"it is revisited sooner than a block."
        )

    if skipped:
        notes.append(
            f"Skipped {len(skipped)} store(s) that blocked us recently: "
            + ", ".join(sorted(skipped))
            + f". They are retried automatically after "
            f"{SETTINGS.skip_blocked_hours:.0f}h — or now, with --stores."
        )

    if not all_offers:
        notes.append(_diagnose_total_failure(statuses))
        return SearchResponse(
            query=query, offers=[], stores=statuses, fx_rates=rates,
            fx_source=fx_source, notes=notes,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    pricing.normalise_offers(all_offers, rates)

    result = matching.filter_relevant(all_offers, query, include_used=include_used)
    if result.dropped_used:
        notes.append(
            f"Hid {result.dropped_used} refurbished/used listing(s) — they undercut "
            f"new stock on price without being the same purchase. "
            f"Pass include_used=true to see them."
        )
    if result.dropped_mismatch:
        notes.append(
            f"Filtered out {result.dropped_mismatch} listing(s) that looked like "
            f"accessories or a different model rather than the product searched for."
        )

    weights = CHEAPEST_WEIGHTS if cheapest_first else None
    ranked = scoring.rank(result.offers, weights)[:max_results]
    if cheapest_first:
        notes.append(
            "Ranking on price (--cheapest): reviews and delivery now only break "
            "ties. The cheapest listing that is not an accessory, a variant or "
            "refurbished should win."
        )
    recommendation = scoring.build_recommendation(ranked)

    survivors: dict[str, int] = {}
    for offer in ranked:
        survivors[offer.store] = survivors.get(offer.store, 0) + 1
    for status in statuses:
        status.kept_count = survivors.get(status.store, 0)

    contributed_nothing = [
        s for s in statuses if s.ok and not s.kept_count
    ]
    if contributed_nothing:
        notes.append(
            "Returned results but none matched the search: "
            + ", ".join(s.store_label for s in contributed_nothing)
            + ". Their listings were accessories, other models or refurbished units."
        )

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
        dropped=[
            DroppedListing(
                title=d.title, store_label=d.store_label,
                price=d.price, reason=d.reason,
            )
            for d in result.dropped
        ],
        recommendation=recommendation,
        fx_rates={k: v for k, v in rates.items() if k in {"USD", "EUR", "GBP", "CNY", "SAR"}},
        fx_source=fx_source,
        notes=notes,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
