"""Runtime settings and the store registry.

Everything tunable lives here so adding a store or re-weighting the ranking
never means touching scraper or UI code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from .models import Market

HOME_CURRENCY = "AED"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Networking
    request_timeout: float = _env_float("REQUEST_TIMEOUT", 20.0)
    browser_timeout: float = _env_float("BROWSER_TIMEOUT", 35.0)
    max_retries: int = _env_int("MAX_RETRIES", 2)

    # Each store gets two chances — plain HTTP, then a real browser — and each
    # phase is capped separately. Without this the retry loop can eat the whole
    # per-store deadline, so a slow store is abandoned before the browser
    # fallback (often the only path that works for JS-heavy sites) ever runs.
    http_phase_timeout: float = _env_float("HTTP_PHASE_TIMEOUT", 20.0)
    browser_phase_timeout: float = _env_float("BROWSER_PHASE_TIMEOUT", 35.0)
    max_concurrent_stores: int = _env_int("MAX_CONCURRENT_STORES", 8)
    per_store_results: int = _env_int("PER_STORE_RESULTS", 6)

    # Behaviour
    use_browser_fallback: bool = _env_bool("USE_BROWSER_FALLBACK", True)
    proxy_url: str | None = os.environ.get("SCRAPER_PROXY") or None

    # Landed-cost assumptions for imports into the UAE.
    # UAE: 5% VAT, 5% customs duty, and duty/VAT are waived under AED 300.
    uae_vat_rate: float = _env_float("UAE_VAT_RATE", 0.05)
    uae_customs_rate: float = _env_float("UAE_CUSTOMS_RATE", 0.05)
    uae_duty_free_threshold_aed: float = _env_float("UAE_DUTY_FREE_THRESHOLD_AED", 300.0)


SETTINGS = Settings()


@dataclass(frozen=True)
class StoreSpec:
    key: str
    label: str
    market: Market
    country: str
    currency: str
    # 0..1 — reputation/return-policy confidence, used as a small ranking term.
    trust: float
    # Typical door-to-door delivery when the listing does not say.
    default_delivery_days: int
    # Flat shipping assumption, in the store's own currency, when unknown.
    default_shipping: float = 0.0
    enabled: bool = True
    # Global stores charge import duty/VAT on arrival into the UAE.
    incurs_import_fees: bool = False
    # Optional per-store cap on the HTTP phase. A store that reliably hangs
    # rather than refusing costs the full budget on every search — Noon held
    # up a 55-second run single-handedly — so it gets a shorter leash than
    # stores that answer promptly or fail fast.
    http_timeout: float | None = None


STORES: dict[str, StoreSpec] = {
    # ---- UAE local market -------------------------------------------------
    "amazon_ae": StoreSpec(
        key="amazon_ae", label="Amazon.ae", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.95, default_delivery_days=2, default_shipping=0.0,
    ),
    "noon": StoreSpec(
        key="noon", label="Noon UAE", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.92, default_delivery_days=2, default_shipping=0.0,
        # Noon does not refuse — it simply never answers, so the timeout is the
        # only thing that ends the attempt. Keep it short.
        http_timeout=8.0,
    ),
    "sharaf_dg": StoreSpec(
        key="sharaf_dg", label="Sharaf DG", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.90, default_delivery_days=3, default_shipping=0.0,
    ),
    "carrefour_ae": StoreSpec(
        key="carrefour_ae", label="Carrefour UAE", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.88, default_delivery_days=3, default_shipping=0.0,
    ),
    # ---- Global market ----------------------------------------------------
    "amazon_com": StoreSpec(
        key="amazon_com", label="Amazon.com (US)", market=Market.GLOBAL, country="US",
        currency="USD", trust=0.93, default_delivery_days=10, default_shipping=15.0,
        incurs_import_fees=True,
    ),
    "ebay": StoreSpec(
        key="ebay", label="eBay", market=Market.GLOBAL, country="US",
        currency="USD", trust=0.78, default_delivery_days=14, default_shipping=12.0,
        incurs_import_fees=True,
    ),
    "aliexpress": StoreSpec(
        key="aliexpress", label="AliExpress", market=Market.GLOBAL, country="CN",
        currency="USD", trust=0.70, default_delivery_days=20, default_shipping=3.0,
        incurs_import_fees=True,
    ),
    "newegg": StoreSpec(
        key="newegg", label="Newegg", market=Market.GLOBAL, country="US",
        currency="USD", trust=0.82, default_delivery_days=12, default_shipping=20.0,
        incurs_import_fees=True,
    ),
}


def _disabled_from_env() -> set[str]:
    raw = os.environ.get("DISABLED_STORES", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


# A store behind a CAPTCHA or a retired API cannot be fixed by better
# selectors, and leaving it on costs every search the full timeout budget.
# Turn those off here rather than editing the registry:
#   DISABLED_STORES=sharaf_dg,carrefour_ae
_DISABLED = _disabled_from_env()
if _DISABLED:
    unknown = _DISABLED - set(STORES)
    if unknown:
        raise ValueError(
            f"DISABLED_STORES names unknown store(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(STORES)}"
        )
    for _key in _DISABLED:
        STORES[_key] = replace(STORES[_key], enabled=False)


@dataclass(frozen=True)
class ScoringWeights:
    """Weights must sum to 1.0; `validate` enforces it at import time."""

    price: float = _env_float("W_PRICE", 0.45)
    rating: float = _env_float("W_RATING", 0.22)
    review_volume: float = _env_float("W_REVIEWS", 0.13)
    delivery: float = _env_float("W_DELIVERY", 0.12)
    trust: float = _env_float("W_TRUST", 0.08)

    def as_dict(self) -> dict[str, float]:
        return {
            "price": self.price,
            "rating": self.rating,
            "review_volume": self.review_volume,
            "delivery": self.delivery,
            "trust": self.trust,
        }

    def validate(self) -> "ScoringWeights":
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.4f}")
        return self


WEIGHTS = ScoringWeights().validate()

# Bayesian prior for star ratings: a 5.0 from 2 reviewers should not outrank a
# 4.6 from 8,000. `PRIOR_COUNT` is how many "average" reviews we blend in.
RATING_PRIOR_MEAN = _env_float("RATING_PRIOR_MEAN", 4.1)
RATING_PRIOR_COUNT = _env_float("RATING_PRIOR_COUNT", 40.0)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

