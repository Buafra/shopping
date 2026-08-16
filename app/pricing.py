"""Turn store-quoted prices into a comparable landed cost in AED.

Comparing sticker prices across borders is misleading: a USD 180 item from the
US can land dearer than an AED 720 item bought in Dubai once shipping, 5% VAT
and 5% customs are on it. Ranking happens on `landed_aed` for that reason.
"""

from __future__ import annotations

from .config import SETTINGS, STORES
from .fx import to_aed
from .models import Offer


def import_fees_aed(goods_aed: float, shipping_aed: float, store_key: str) -> float:
    """UAE import charges on a personal shipment.

    Customs duty is levied on the goods value; VAT applies to goods + freight +
    duty. Consignments under the de-minimis threshold clear free of both.
    """
    spec = STORES.get(store_key)
    if spec is None or not spec.incurs_import_fees:
        return 0.0
    if goods_aed <= SETTINGS.uae_duty_free_threshold_aed:
        return 0.0

    duty = goods_aed * SETTINGS.uae_customs_rate
    vat = (goods_aed + shipping_aed + duty) * SETTINGS.uae_vat_rate
    return round(duty + vat, 2)


def normalise_offer(offer: Offer, rates: dict[str, float]) -> Offer:
    """Fill in the AED price fields on a single offer, in place."""
    offer.price_aed = to_aed(offer.price, offer.currency, rates)
    offer.shipping_aed = to_aed(offer.shipping or 0.0, offer.currency, rates)
    offer.import_fees_aed = import_fees_aed(
        offer.price_aed, offer.shipping_aed, offer.store
    )
    offer.landed_aed = round(
        offer.price_aed + offer.shipping_aed + offer.import_fees_aed, 2
    )
    return offer


def normalise_offers(offers: list[Offer], rates: dict[str, float]) -> list[Offer]:
    return [normalise_offer(o, rates) for o in offers]
