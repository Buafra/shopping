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


def _setup_console() -> tuple[bool, bool]:
    """Make the terminal safe to print to. Returns (unicode_ok, colour_ok).

    Windows consoles still default to cp1252, where printing a star or an em
    dash raises UnicodeEncodeError and kills the run. Ask for UTF-8, enable
    ANSI escapes where the OS supports them, and report what we actually got so
    the output can degrade instead of crashing.
    """
    unicode_ok = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
        unicode_ok = encoding.replace("-", "") in {"utf8", "utf16", "utf32"}

    colour_ok = sys.stdout.isatty()
    if colour_ok and sys.platform == "win32":
        # Turn on virtual-terminal processing; without it Windows prints the
        # raw escape bytes instead of colouring the text.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(
                    handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                )
            else:
                colour_ok = False
        except Exception:
            colour_ok = False

    return unicode_ok, colour_ok


UNICODE_OK, COLOUR_OK = _setup_console()

if COLOUR_OK:
    RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
    GREEN, YELLOW, RED, CYAN = "\033[32m", "\033[33m", "\033[31m", "\033[36m"
else:
    RESET = BOLD = DIM = GREEN = YELLOW = RED = CYAN = ""

# Glyphs with ASCII stand-ins for consoles that cannot encode them.
_GLYPHS = {
    "star": ("★", "*"), "arrow": ("▶", ">"), "bullet": ("•", "-"),
    "tick": ("✓", "OK"), "cross": ("✗", "X"), "dash": ("—", "-"),
    "rarrow": ("→", "->"), "ellipsis": ("…", "..."),
    "ldquo": ("“", '"'), "rdquo": ("”", '"'),
}
G = {name: (rich if UNICODE_OK else plain) for name, (rich, plain) in _GLYPHS.items()}


def out(text: str) -> None:
    """Print, surviving any console that cannot encode a character we used."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"))


def money(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else G["dash"]


def render(response) -> None:
    out(f"\n{BOLD}Results for {G['ldquo']}{response.query}{G['rdquo']}{RESET}  "
          f"{DIM}({response.elapsed_ms / 1000:.1f}s){RESET}\n")

    if not response.offers:
        out(f"{RED}No offers found.{RESET}")
    else:
        header = (f"{BOLD}{'#':<3}{'STORE':<16}{'LANDED':>10}  {'RATING':>8}  "
                  f"{'ETA':>5}  {'SCORE':>6}  PRODUCT{RESET}")
        out(header)
        out(DIM + "-" * 108 + RESET)
        for i, offer in enumerate(response.offers[:15], 1):
            colour = GREEN if i == 1 else ""
            rating = f"{offer.rating:.1f}{G['star']}" if offer.rating else G["dash"]
            if offer.review_count:
                rating += f"({offer.review_count})"
            out(
                f"{colour}{i:<3}{offer.store_label[:15]:<16}"
                f"{money(offer.landed_aed):>10}  {rating:>8}  "
                f"{str(offer.delivery_days or '?') + 'd':>5}  "
                f"{offer.score or 0:>6.1f}  {offer.short_title(52)}{RESET}"
            )

    rec = response.recommendation
    if rec:
        out(f"\n{BOLD}{GREEN}{G['arrow']} RECOMMENDED{RESET}  {BOLD}{rec.offer.short_title(70)}{RESET}")
        out(f"  {rec.offer.store_label} {G['dash']} {BOLD}AED {money(rec.offer.landed_aed)}{RESET} landed "
              f"{DIM}(item {money(rec.offer.price_aed)}"
              f"{f' + ship {money(rec.offer.shipping_aed)}' if rec.offer.shipping_aed else ''}"
              f"{f' + duty {money(rec.offer.import_fees_aed)}' if rec.offer.import_fees_aed else ''})"
              f"{RESET}")
        out(f"  {CYAN}{rec.offer.url}{RESET}")
        out(f"  Confidence: {rec.confidence}")
        for reason in rec.rationale:
            out(f"    {G['bullet']} {reason}")

    out(f"\n{BOLD}Store coverage{RESET}")
    for status in response.stores:
        mark = f"{GREEN}{G['tick']}{RESET}" if status.ok else f"{RED}{G['cross']}{RESET}"
        detail = (f"{status.offer_count} offers" if status.ok
                  else (status.error or "no results")[:64])
        out(f"  {mark} {status.store_label:<18} {DIM}{status.elapsed_ms/1000:>5.1f}s  "
              f"{detail}{RESET}")

    for note in response.notes:
        out(f"\n{YELLOW}note:{RESET} {note}")
    out("")


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
