"""Currency conversion to AED, with a cached live rate and a static fallback.

The AED is pegged to the USD at 3.6725, so USD conversion is effectively exact
even when the live feed is unreachable. Other currencies fall back to rates
that are close enough for ranking but are labelled as such in the response.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .config import HOME_CURRENCY
from .net import fetch

log = logging.getLogger(__name__)

# Units of `currency` per 1 AED is confusing to reason about, so these are the
# other direction: 1 unit of `currency` == N AED.
FALLBACK_RATES: dict[str, float] = {
    "AED": 1.0,
    "USD": 3.6725,   # pegged
    "SAR": 0.9793,
    "EUR": 3.99,
    "GBP": 4.66,
    "CNY": 0.505,
    "INR": 0.0441,
    "JPY": 0.0244,
    "EGP": 0.0756,
    "KWD": 11.98,
    "QAR": 1.0088,
    "BHD": 9.74,
    "OMR": 9.54,
    "TRY": 0.091,
}

RATES_URL = "https://open.er-api.com/v6/latest/AED"
CACHE_TTL = 6 * 60 * 60  # 6 hours; FX moves slower than our search traffic

_cache: dict[str, float] = dict(FALLBACK_RATES)
_cache_source = "fallback"
_cache_at = 0.0
_lock = asyncio.Lock()


async def refresh_rates(force: bool = False) -> tuple[dict[str, float], str]:
    """Return (rates_to_aed, source). Never raises — falls back silently."""
    global _cache, _cache_source, _cache_at

    if not force and _cache_source == "live" and (time.time() - _cache_at) < CACHE_TTL:
        return dict(_cache), _cache_source

    async with _lock:
        if not force and _cache_source == "live" and (time.time() - _cache_at) < CACHE_TTL:
            return dict(_cache), _cache_source

        try:
            resp = await fetch(RATES_URL, headers={"Accept": "application/json"})
            payload = resp.json()
            # The feed gives AED -> X; we want X -> AED, so invert.
            quoted = payload.get("rates") or {}
            if not isinstance(quoted, dict) or "USD" not in quoted:
                raise ValueError("unexpected FX payload shape")

            rates = dict(FALLBACK_RATES)
            for code, per_aed in quoted.items():
                try:
                    value = float(per_aed)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    rates[code.upper()] = 1.0 / value
            rates["AED"] = 1.0

            _cache, _cache_source, _cache_at = rates, "live", time.time()
            log.info("FX rates refreshed from live feed")
        except Exception as exc:
            log.warning("FX refresh failed (%s); using fallback rates", exc)
            if _cache_source != "live":
                _cache, _cache_source = dict(FALLBACK_RATES), "fallback"

    return dict(_cache), _cache_source


def to_aed(amount: float, currency: str, rates: dict[str, float] | None = None) -> float:
    """Convert `amount` of `currency` into AED. Unknown currencies pass through
    unchanged rather than silently zeroing out a real price."""
    if amount is None:
        return 0.0
    code = (currency or HOME_CURRENCY).upper()
    if code == HOME_CURRENCY:
        return round(float(amount), 2)

    table = rates if rates is not None else _cache
    rate = table.get(code) or FALLBACK_RATES.get(code)
    if not rate:
        log.warning("no FX rate for %s; treating amount as AED", code)
        return round(float(amount), 2)
    return round(float(amount) * rate, 2)


def current_rates() -> tuple[dict[str, float], str]:
    return dict(_cache), _cache_source
