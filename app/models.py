"""Core data structures shared by scrapers, scoring and the API layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Market(str, Enum):
    LOCAL = "local"
    GLOBAL = "global"


class Offer(BaseModel):
    """A single product listing found at one store.

    Prices arrive in whatever currency the store quotes. Everything downstream
    ranks on `landed_aed`, which is filled in by the normalisation step.
    """

    store: str
    store_label: str
    market: Market
    country: str

    title: str
    url: str
    image: str | None = None

    price: float
    currency: str
    shipping: float = 0.0
    shipping_is_estimate: bool = True

    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)

    in_stock: bool = True
    delivery_days: int | None = None

    # Filled in by pricing.normalise_offers()
    price_aed: float | None = None
    shipping_aed: float | None = None
    import_fees_aed: float | None = None
    landed_aed: float | None = None

    # Filled in by scoring.rank()
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    badges: list[str] = Field(default_factory=list)

    def short_title(self, limit: int = 90) -> str:
        return self.title if len(self.title) <= limit else self.title[: limit - 1] + "…"


class StoreStatus(BaseModel):
    """Per-store outcome for one search, so the UI can be honest about gaps."""

    store: str
    store_label: str
    market: Market
    ok: bool
    offer_count: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    # "unreachable" | "timeout" | "blocked" | "http_error" | "no_results" | "parse"
    error_kind: str | None = None
    method: str | None = None  # "http" or "browser"


class Recommendation(BaseModel):
    offer: Offer
    rationale: list[str]
    runner_up: Offer | None = None
    savings_vs_worst_aed: float | None = None
    confidence: str  # "high" | "medium" | "low"


class SearchResponse(BaseModel):
    query: str
    currency: str = "AED"
    offers: list[Offer]
    stores: list[StoreStatus]
    recommendation: Recommendation | None = None
    fx_rates: dict[str, float] = Field(default_factory=dict)
    fx_source: str = "fallback"
    elapsed_ms: int = 0
    notes: list[str] = Field(default_factory=list)

    def model_dump_api(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
