"""FastAPI application: JSON API plus the bundled single-page UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import aggregator, browser, fx, net
from .config import SETTINGS, STORES, WEIGHTS
from .models import Market, SearchResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("shopping")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await fx.refresh_rates()
    log.info("ready — %d stores registered", len(STORES))
    yield
    await net.close_client()
    await browser.close_browser()


app = FastAPI(
    title="Shopping Scout",
    description="Compare a product across UAE and global stores, and get a pick.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/search", response_model=SearchResponse)
async def api_search(
    q: str = Query(..., min_length=2, max_length=200, description="Product to find"),
    market: str = Query("all", pattern="^(all|local|global)$"),
    stores: str | None = Query(None, description="Comma-separated store keys"),
    limit_per_store: int = Query(SETTINGS.per_store_results, ge=1, le=20),
    max_results: int = Query(40, ge=1, le=100),
) -> SearchResponse:
    markets = None
    if market == "local":
        markets = [Market.LOCAL]
    elif market == "global":
        markets = [Market.GLOBAL]

    store_list = [s for s in stores.split(",") if s.strip()] if stores else None

    try:
        return await aggregator.search(
            q,
            markets=markets,
            stores=store_list,
            limit_per_store=limit_per_store,
            max_results=max_results,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("search failed for %r", q)
        raise HTTPException(status_code=502, detail=f"search failed: {exc}") from exc


@app.get("/api/stores")
async def api_stores() -> JSONResponse:
    return JSONResponse(
        {
            "stores": [
                {
                    "key": s.key,
                    "label": s.label,
                    "market": s.market.value,
                    "country": s.country,
                    "currency": s.currency,
                    "trust": s.trust,
                    "typical_delivery_days": s.default_delivery_days,
                    "enabled": s.enabled,
                }
                for s in STORES.values()
            ],
            "weights": WEIGHTS.as_dict(),
        }
    )


@app.get("/api/health")
async def api_health() -> JSONResponse:
    rates, source = fx.current_rates()
    browser_ok = True
    browser_detail = "available"
    try:
        await browser._get_browser()  # noqa: SLF001 - health probe
    except browser.BrowserUnavailable as exc:
        browser_ok, browser_detail = False, str(exc)

    return JSONResponse(
        {
            "status": "ok",
            "stores_registered": len(STORES),
            "browser_fallback": {"enabled": SETTINGS.use_browser_fallback,
                                 "ok": browser_ok, "detail": browser_detail},
            "fx": {"source": source, "usd_to_aed": rates.get("USD")},
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
