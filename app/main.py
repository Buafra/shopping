"""FastAPI application: JSON API plus the bundled single-page UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import aggregator, browser, fx, history, net
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
    include_used: bool = Query(False, description="Include refurbished/used listings"),
    save_history: bool = Query(True, description="Record this search for price tracking"),
) -> SearchResponse:
    markets = None
    if market == "local":
        markets = [Market.LOCAL]
    elif market == "global":
        markets = [Market.GLOBAL]

    store_list = [s for s in stores.split(",") if s.strip()] if stores else None

    try:
        response = await aggregator.search(
            q,
            markets=markets,
            stores=store_list,
            limit_per_store=limit_per_store,
            max_results=max_results,
            include_used=include_used,
        )
        if save_history and not store_list:
            # Store-filtered searches are diagnostics, not something to track.
            try:
                history.record(response, market=market, include_used=include_used)
            except Exception:
                log.warning("could not record search history", exc_info=True)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("search failed for %r", q)
        raise HTTPException(status_code=502, detail=f"search failed: {exc}") from exc


@app.get("/api/history")
async def api_history() -> JSONResponse:
    """Past searches, newest check first, with how the best price has moved."""
    return JSONResponse({
        "searches": [
            {
                "id": r.id, "query": r.query, "market": r.market,
                "include_used": r.include_used,
                "created_at": r.created_at, "last_checked_at": r.last_checked_at,
                "checks": r.checks,
                "best_landed_aed": r.latest_best_aed,
                "previous_best_aed": r.previous_best_aed,
                "best_delta_aed": r.best_delta,
            }
            for r in history.list_searches()
        ]
    })


@app.get("/api/history/{search_id}")
async def api_history_detail(search_id: int) -> JSONResponse:
    changes = history.compare(search_id)
    if changes is None:
        raise HTTPException(status_code=404, detail=f"no saved search #{search_id}")
    return JSONResponse(_changes_payload(changes))


@app.post("/api/history/{search_id}/recheck")
async def api_history_recheck(search_id: int) -> JSONResponse:
    """Re-run a saved search and report what moved since last time."""
    record = history.get_search(search_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no saved search #{search_id}")

    markets = None
    if record.market == "local":
        markets = [Market.LOCAL]
    elif record.market == "global":
        markets = [Market.GLOBAL]

    try:
        response = await aggregator.search(
            record.query, markets=markets, include_used=record.include_used
        )
    except Exception as exc:
        log.exception("recheck failed for #%s", search_id)
        raise HTTPException(status_code=502, detail=f"recheck failed: {exc}") from exc

    stored = history.record(
        response, market=record.market, include_used=record.include_used
    )
    if stored is None:
        raise HTTPException(
            status_code=502,
            detail="every store failed, so the re-check was discarded rather "
                   "than recorded as a price change",
        )
    changes = history.compare(search_id)
    return JSONResponse({
        "result": response.model_dump(mode="json"),
        "changes": _changes_payload(changes) if changes else None,
    })


@app.delete("/api/history/{search_id}")
async def api_history_delete(search_id: int) -> JSONResponse:
    if not history.delete(search_id):
        raise HTTPException(status_code=404, detail=f"no saved search #{search_id}")
    return JSONResponse({"deleted": search_id})


def _changes_payload(changes) -> dict:
    def offer(o):
        return {
            "url": o.url, "store_label": o.store_label, "title": o.title,
            "landed_aed": o.landed_aed,
            "previous_landed_aed": o.previous_landed_aed,
            "delta_aed": o.delta, "pct": o.pct, "direction": o.direction,
        }

    return {
        "search_id": changes.search.id,
        "query": changes.search.query,
        "checks": changes.search.checks,
        "last_checked_at": changes.search.last_checked_at,
        "summary": changes.summary(),
        "is_first_run": changes.is_first_run,
        "unchanged": changes.unchanged,
        "changed": [offer(o) for o in changes.changed],
        "appeared": [offer(o) for o in changes.appeared],
        "disappeared": [offer(o) for o in changes.disappeared],
    }


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

    from .browser import proxy_settings
    proxy = proxy_settings()

    return JSONResponse(
        {
            "status": "ok",
            "stores_registered": len(STORES),
            "proxy": {
                "configured": bool(SETTINGS.proxy_url),
                # Never echo the credentials back out.
                "server": proxy["server"] if proxy else None,
                "authenticated": bool(proxy and proxy.get("username")),
                "used_by": ["http", "browser"] if proxy else [],
            },
            "browser_fallback": {"enabled": SETTINGS.use_browser_fallback,
                                 "ok": browser_ok, "detail": browser_detail},
            "fx": {"source": source, "usd_to_aed": rates.get("USD")},
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
