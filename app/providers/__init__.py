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
