"""Provider registry.

`build(store_key)` returns a ready provider instance. Adding a store means
adding one module and one line here — nothing else in the app changes.
"""

from __future__ import annotations

from typing import Callable

from ..config import STORES, StoreSpec
from ..models import Market
from .aliexpress import make as make_aliexpress
from .amazon import amazon_ae, amazon_com
from .base import Provider
from .carrefour_ae import make as make_carrefour_ae
from .ebay import make as make_ebay
from .newegg import make as make_newegg
from .noon import make as make_noon
from .sharaf_dg import make as make_sharaf_dg

# Factories are aliased on import so they never shadow their own submodule.
# `from .ebay import ebay` would rebind `app.providers.ebay` from the module to
# the function, which silently breaks anything that patches module-level
# constants (base URLs, API endpoints) for testing.
FACTORIES: dict[str, Callable[[], Provider]] = {
    "amazon_ae": amazon_ae,
    "noon": make_noon,
    "sharaf_dg": make_sharaf_dg,
    "carrefour_ae": make_carrefour_ae,
    "amazon_com": amazon_com,
    "ebay": make_ebay,
    "aliexpress": make_aliexpress,
    "newegg": make_newegg,
}


def _config_driven(store_key: str) -> Callable[[], Provider]:
    """Factory for a store that has configuration but no module of its own.

    The spec is looked up when the provider is built, not now, so a store
    switched off or altered after import is still seen correctly.
    """

    def make() -> Provider:
        from .generic import GenericProvider

        return GenericProvider(STORES[store_key])

    return make


# Any configured store without a bespoke module gets the structural scraper.
# This is what makes adding a shop a config change rather than a code change.
for _key, _spec in STORES.items():
    if _key not in FACTORIES and (_spec.origin or _spec.search_urls):
        FACTORIES[_key] = _config_driven(_key)


def build(store_key: str) -> Provider:
    try:
        return FACTORIES[store_key]()
    except KeyError as exc:
        raise KeyError(f"unknown store {store_key!r}") from exc


def build_all(
    markets: list[Market] | None = None,
    only: list[str] | None = None,
) -> list[Provider]:
    specs: list[StoreSpec] = [s for s in STORES.values() if s.enabled]
    if markets:
        specs = [s for s in specs if s.market in markets]
    if only:
        wanted = {k.strip() for k in only if k.strip()}
        specs = [s for s in specs if s.key in wanted]
    return [build(s.key) for s in specs if s.key in FACTORIES]


__all__ = ["Provider", "build", "build_all", "FACTORIES"]
