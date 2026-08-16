"""Provider registry.

`build(store_key)` returns a ready provider instance. Adding a store means
adding one module and one line here — nothing else in the app changes.
"""

from __future__ import annotations

from typing import Callable

from ..config import STORES, StoreSpec
from ..models import Market
from .aliexpress import aliexpress
from .amazon import amazon_ae, amazon_com
from .base import Provider
from .carrefour_ae import carrefour_ae
from .ebay import ebay
from .newegg import newegg
from .noon import noon
from .sharaf_dg import sharaf_dg

FACTORIES: dict[str, Callable[[], Provider]] = {
    "amazon_ae": amazon_ae,
    "noon": noon,
    "sharaf_dg": sharaf_dg,
    "carrefour_ae": carrefour_ae,
    "amazon_com": amazon_com,
    "ebay": ebay,
    "aliexpress": aliexpress,
    "newegg": newegg,
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
