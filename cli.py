#!/usr/bin/env python3
"""Command-line front end, for when you want a pick without opening a browser.

    python cli.py "sony wh-1000xm5"
    python cli.py "airfryer" --market local
    python cli.py "rtx 4070" --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app import aggregator, browser, net
from app.models import Market

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, YELLOW, RED, CYAN = "\033[32m", "\033[33m", "\033[31m", "\033[36m"


def money(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "—"


def render(response) -> None:
    print(f"\n{BOLD}Results for “{response.query}”{RESET}  "
          f"{DIM}({response.elapsed_ms / 1000:.1f}s){RESET}\n")

    if not response.offers:
        print(f"{RED}No offers found.{RESET}")
    else:
        header = (f"{BOLD}{'#':<3}{'STORE':<16}{'LANDED':>10}  {'RATING':>8}  "
                  f"{'ETA':>5}  {'SCORE':>6}  PRODUCT{RESET}")
        print(header)
        print(DIM + "-" * 108 + RESET)
        for i, offer in enumerate(response.offers[:15], 1):
            colour = GREEN if i == 1 else ""
            rating = f"{offer.rating:.1f}★" if offer.rating else "—"
            if offer.review_count:
                rating += f"({offer.review_count})"
            print(
                f"{colour}{i:<3}{offer.store_label[:15]:<16}"
                f"{money(offer.landed_aed):>10}  {rating:>8}  "
                f"{str(offer.delivery_days or '?') + 'd':>5}  "
                f"{offer.score or 0:>6.1f}  {offer.short_title(52)}{RESET}"
            )

    rec = response.recommendation
    if rec:
        print(f"\n{BOLD}{GREEN}▶ RECOMMENDED{RESET}  {BOLD}{rec.offer.short_title(70)}{RESET}")
        print(f"  {rec.offer.store_label} — {BOLD}AED {money(rec.offer.landed_aed)}{RESET} landed "
              f"{DIM}(item {money(rec.offer.price_aed)}"
              f"{f' + ship {money(rec.offer.shipping_aed)}' if rec.offer.shipping_aed else ''}"
              f"{f' + duty {money(rec.offer.import_fees_aed)}' if rec.offer.import_fees_aed else ''})"
              f"{RESET}")
        print(f"  {CYAN}{rec.offer.url}{RESET}")
        print(f"  Confidence: {rec.confidence}")
        for reason in rec.rationale:
            print(f"    • {reason}")

    print(f"\n{BOLD}Store coverage{RESET}")
    for status in response.stores:
        mark = f"{GREEN}✓{RESET}" if status.ok else f"{RED}✗{RESET}"
        detail = (f"{status.offer_count} offers" if status.ok
                  else (status.error or "no results")[:64])
        print(f"  {mark} {status.store_label:<18} {DIM}{status.elapsed_ms/1000:>5.1f}s  "
              f"{detail}{RESET}")

    for note in response.notes:
        print(f"\n{YELLOW}note:{RESET} {note}")
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a product across UAE and global stores")
    parser.add_argument("query", help="what you want to buy")
    parser.add_argument("--market", choices=["all", "local", "global"], default="all")
    parser.add_argument("--stores", help="comma-separated store keys to limit the search")
    parser.add_argument("--limit", type=int, default=6, help="results per store")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of a table")
    args = parser.parse_args()

    markets = None
    if args.market == "local":
        markets = [Market.LOCAL]
    elif args.market == "global":
        markets = [Market.GLOBAL]

    try:
        response = await aggregator.search(
            args.query,
            markets=markets,
            stores=args.stores.split(",") if args.stores else None,
            limit_per_store=args.limit,
        )
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            render(response)
        return 0 if response.offers else 1
    finally:
        await net.close_client()
        await browser.close_browser()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
