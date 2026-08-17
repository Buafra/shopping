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
    # Enough for every store to run in one wave. A cap below the store count
    # costs a second round of the slowest store for no saving — these are
    # network waits, not CPU work.
    max_concurrent_stores: int = _env_int("MAX_CONCURRENT_STORES", 16)
    per_store_results: int = _env_int("PER_STORE_RESULTS", 6)

    # Behaviour
    use_browser_fallback: bool = _env_bool("USE_BROWSER_FALLBACK", True)
    # Perform the TLS handshake with a browser's fingerprint where curl_cffi
    # is installed. Stores that block on JA3 reject the default Python
    # handshake before any header is read, so this is the one thing headers
    # cannot fix.
    use_tls_impersonation: bool = _env_bool("USE_TLS_IMPERSONATION", True)
    # A store that answered with a CAPTCHA will answer the same way an hour
    # later, so remember it and stop spending the timeout budget on it.
    skip_blocked_hours: float = _env_float("SKIP_BLOCKED_HOURS", 24.0)
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

    # ---- config-driven stores --------------------------------------------
    #
    # A store with `origin` set needs no Python at all: the structural parser
    # finds product cards by shape, so adding a shop is an entry in this dict
    # rather than a new module. Bespoke providers still win where a store
    # ships a JSON API worth using.
    origin: str | None = None
    # Search-page templates, tried in order until one yields listings. `{q}`
    # is the URL-encoded query. Empty means "try the common platform paths".
    search_urls: tuple[str, ...] = ()
    # The path fragment product URLs contain ("/p/", "/products/"). Left None,
    # it is detected from the page — see structural.guess_product_path.
    product_path: str | None = None
    # Stores that only make sense for some searches. An untagged store is
    # always used; a tagged one joins in when the query matches its tag.
    tags: tuple[str, ...] = ()


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

    # ---- PC-component specialists ----------------------------------------
    #
    # Tagged `pc_parts`, so they join a search for a graphics card or a CPU and
    # sit out a search for a kettle. General-goods stores carry a thin, dear
    # slice of this category; these are where the cheap stock actually is.
    #
    # All of them are config-driven: no parser module, no selectors. If one
    # stops working the fix is a URL in this dict, and `python diagnose.py
    # <key>` says which URL to use.
    # The UAE storefront is on the `uae.` subdomain, not `www.` — the latter is
    # the group site and does not carry UAE pricing.
    "microless": StoreSpec(
        key="microless", label="Microless (UAE)", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.84, default_delivery_days=3, default_shipping=0.0,
        origin="https://uae.microless.com",
        search_urls=("https://uae.microless.com/search/?q={q}",),
        tags=("pc_parts",),
    ),
    # Independent UAE component shops. Between them these are where the enthusiast
    # market actually buys, and they undercut the general retailers on parts
    # precisely because parts are all they sell. Mostly WooCommerce and Shopify,
    # both of which the default search patterns already cover.
    "uaegamers": StoreSpec(
        key="uaegamers", label="UAEGAMERS", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.78, default_delivery_days=3, default_shipping=0.0,
        origin="https://uaegamers.com",
        tags=("pc_parts",),
    ),
    "gcc_gamers": StoreSpec(
        key="gcc_gamers", label="GCC Gamers", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.80, default_delivery_days=3, default_shipping=0.0,
        origin="https://gccgamers.com",
        tags=("pc_parts",),
    ),
    "dxb_gamers": StoreSpec(
        key="dxb_gamers", label="DXB Gamers", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.76, default_delivery_days=3, default_shipping=0.0,
        origin="https://dxbgamers.com",
        tags=("pc_parts",),
    ),
    "pcdubai": StoreSpec(
        key="pcdubai", label="PCDubai", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.76, default_delivery_days=3, default_shipping=0.0,
        origin="https://pcdubai.com",
        tags=("pc_parts",),
    ),
    "gear_up": StoreSpec(
        key="gear_up", label="Gear-up.me", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.76, default_delivery_days=3, default_shipping=0.0,
        origin="https://gear-up.me",
        tags=("pc_parts",),
    ),
    "emax": StoreSpec(
        key="emax", label="Emax (UAE)", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.82, default_delivery_days=3, default_shipping=0.0,
        origin="https://www.emaxme.com",
        tags=("pc_parts",),
    ),
    "jumbo_ae": StoreSpec(
        key="jumbo_ae", label="Jumbo (UAE)", market=Market.LOCAL, country="AE",
        currency="AED", trust=0.85, default_delivery_days=3, default_shipping=0.0,
        origin="https://www.jumbo.ae",
        tags=("pc_parts",),
    ),
    "bhphoto": StoreSpec(
        key="bhphoto", label="B&H Photo (US)", market=Market.GLOBAL, country="US",
        currency="USD", trust=0.90, default_delivery_days=12, default_shipping=25.0,
        incurs_import_fees=True,
        origin="https://www.bhphotovideo.com",
        search_urls=("https://www.bhphotovideo.com/c/search?q={q}",),
        product_path="/c/product/",
        tags=("pc_parts",),
    ),
    "overclockers_uk": StoreSpec(
        key="overclockers_uk", label="Overclockers UK", market=Market.GLOBAL,
        country="GB", currency="GBP", trust=0.84,
        default_delivery_days=12, default_shipping=25.0, incurs_import_fees=True,
        origin="https://www.overclockers.co.uk",
        search_urls=("https://www.overclockers.co.uk/search?sSearch={q}",),
        tags=("pc_parts",),
    ),
    "scan_uk": StoreSpec(
        key="scan_uk", label="Scan UK", market=Market.GLOBAL, country="GB",
        currency="GBP", trust=0.84, default_delivery_days=12, default_shipping=25.0,
        incurs_import_fees=True,
        origin="https://www.scan.co.uk",
        search_urls=("https://www.scan.co.uk/search?q={q}",),
        tags=("pc_parts",),
    ),
    "alternate_de": StoreSpec(
        key="alternate_de", label="Alternate (DE)", market=Market.GLOBAL, country="DE",
        currency="EUR", trust=0.83, default_delivery_days=14, default_shipping=30.0,
        incurs_import_fees=True,
        origin="https://www.alternate.de",
        search_urls=("https://www.alternate.de/listing.xhtml?q={q}",),
        tags=("pc_parts",),
    ),
}

# Tried in order for a store that names no search URL of its own, and only the
# first MAX_CANDIDATE_URLS of them are attempted — so the order is not
# cosmetic. Shopify and the plain `?q=` convention share one line; WooCommerce
# powers most independent shops and must stay inside the cap; Magento is third.
DEFAULT_SEARCH_PATTERNS: tuple[str, ...] = (
    "{origin}/search?q={q}",                    # Shopify, and most custom builds
    "{origin}/?s={q}&post_type=product",        # WooCommerce
    "{origin}/catalogsearch/result/?q={q}",     # Magento
)


def apply_disabled_stores(registry: dict[str, StoreSpec], raw: str) -> set[str]:
    """Switch off the stores named in `raw`, in place. Returns their keys.

    A store behind a CAPTCHA or a retired API cannot be fixed by better
    selectors, and leaving it on costs every search the full timeout budget:
      DISABLED_STORES=sharaf_dg,carrefour_ae

    Mutates the registry rather than rebuilding it, so anything that already
    imported the dict keeps seeing the truth. Rebuilding is how a reload in a
    test leaves two registries disagreeing about which stores exist.
    """
    wanted = {k.strip() for k in (raw or "").split(",") if k.strip()}
    if not wanted:
        return set()

    unknown = wanted - set(registry)
    if unknown:
        raise ValueError(
            f"DISABLED_STORES names unknown store(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(registry)}"
        )
    for key in wanted:
        registry[key] = replace(registry[key], enabled=False)
    return wanted


apply_disabled_stores(STORES, os.environ.get("DISABLED_STORES", ""))


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

# For a shopper who has said what they want: the cheapest one that is not a
# trap. Price dominates, ratings still break ties and still keep a 5.0 from
# two reviewers from winning, but a 50% premium can no longer be justified by
# review count alone. The defaults above stay untouched — this is opt-in.
CHEAPEST_WEIGHTS = ScoringWeights(
    price=0.75, rating=0.10, review_volume=0.05, delivery=0.05, trust=0.05,
).validate()

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

