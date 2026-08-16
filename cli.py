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

from app import aggregator, blocklist, browser, history, net
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
        if status.ok:
            kept = status.kept_count
            detail = (
                f"{status.offer_count} offers"
                if kept is None or kept == status.offer_count
                else f"{status.offer_count} offers, {kept} matched"
            )
            if kept == 0:
                mark = f"{YELLOW}!{RESET}"
        else:
            detail = (status.error or "no results")[:64]
        out(f"  {mark} {status.store_label:<18} {DIM}{status.elapsed_ms/1000:>5.1f}s  "
              f"{detail}{RESET}")

    for note in response.notes:
        out(f"\n{YELLOW}note:{RESET} {note}")
    out("")


def _markets(market: str):
    if market == "local":
        return [Market.LOCAL]
    if market == "global":
        return [Market.GLOBAL]
    return None


async def run_search(query: str, args):
    return await aggregator.search(
        query,
        markets=_markets(args.market),
        stores=args.stores.split(",") if args.stores else None,
        limit_per_store=args.limit,
        include_used=args.include_used,
    )


def render_history() -> None:
    records = history.list_searches()
    if not records:
        out(f"\n{DIM}No saved searches yet — run a search first.{RESET}\n")
        return

    out(f"\n{BOLD}Saved searches{RESET}\n")
    out(f"{BOLD}{'ID':<4}{'BEST (AED)':>12}  {'CHANGE':>10}  {'CHECKS':>7}  "
        f"{'LAST CHECKED':<22}QUERY{RESET}")
    out(DIM + "-" * 100 + RESET)
    for r in records:
        delta = r.best_delta
        if delta is None:
            change, colour = f"{G['dash']}", DIM
        elif delta < 0:
            change, colour = f"{money(delta)}", GREEN
        elif delta > 0:
            change, colour = f"+{money(delta)}", RED
        else:
            change, colour = "0", DIM
        out(f"{r.id:<4}{money(r.latest_best_aed):>12}  {colour}{change:>10}{RESET}  "
            f"{r.checks:>7}  {r.last_checked_at[:19].replace('T', ' '):<22}{r.query}")
    out(f"\n{DIM}Re-check with: python cli.py --recheck <id>   (or --recheck all){RESET}\n")


def render_blocked() -> None:
    from app.config import SETTINGS

    entries = blocklist.blocked(SETTINGS.skip_blocked_hours)
    if not entries:
        out(f"\n{DIM}No stores are currently being skipped.{RESET}\n")
        return

    out(f"\n{BOLD}Stores being skipped{RESET}  "
        f"{DIM}(retried automatically after {SETTINGS.skip_blocked_hours:.0f}h)"
        f"{RESET}\n")
    for store, reason in sorted(entries.items()):
        out(f"  {RED}{G['cross']}{RESET} {store:<16}{DIM}{reason[:78]}{RESET}")
    out(f"\n{DIM}Retry one now: python cli.py --unblock <store>   "
        f"(or --unblock all){RESET}\n")


def render_changes(response, args) -> None:
    """Show what moved since the previous run of this same search."""
    if args.no_save:
        return
    search_id = history.record(
        response, market=args.market, include_used=args.include_used
    )
    if search_id is None:
        out(f"{YELLOW}note:{RESET} every store failed, so this run was not "
            f"recorded — tracked prices are left as they were.\n")
        return
    changes = history.compare(search_id)
    if changes is None or changes.is_first_run:
        out(f"{DIM}Saved as search #{search_id}. "
            f"Re-run with: python cli.py --recheck {search_id}{RESET}\n")
        return

    out(f"{BOLD}Since last check{RESET}  {DIM}(search #{search_id}){RESET}")
    out(f"  {changes.summary()}")
    for offer in changes.changed[:8]:
        colour = GREEN if offer.direction == "down" else RED
        sign = "" if (offer.delta or 0) < 0 else "+"
        out(f"    {colour}{sign}{money(offer.delta)} ({sign}{offer.pct:.1f}%){RESET}  "
            f"{money(offer.previous_landed_aed)} {G['rarrow']} {money(offer.landed_aed)}  "
            f"{DIM}{offer.store_label}{RESET}  {offer.title[:44]}")
    for offer in changes.appeared[:4]:
        out(f"    {DIM}new{RESET}      {money(offer.landed_aed):>17}  "
            f"{DIM}{offer.store_label}{RESET}  {offer.title[:44]}")
    for offer in changes.disappeared[:4]:
        out(f"    {DIM}gone     {money(offer.landed_aed):>17}  "
            f"{offer.store_label}  {offer.title[:44]}{RESET}")
    out("")


async def recheck(which: str, args) -> int:
    records = history.list_searches()
    if not records:
        out("No saved searches to re-check.")
        return 1

    if which.lower() != "all":
        try:
            wanted = int(which)
        except ValueError:
            out(f"--recheck takes a numeric id or 'all', not {which!r}")
            return 2
        records = [r for r in records if r.id == wanted]
        if not records:
            out(f"No saved search #{which}")
            return 1

    for record_ in records:
        out(f"\n{BOLD}Re-checking #{record_.id}: {record_.query}{RESET}")
        args.market = record_.market
        args.include_used = record_.include_used
        response = await run_search(record_.query, args)
        render(response)
        render_changes(response, args)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="Compare a product across UAE and global stores")
    parser.add_argument("query", nargs="?", help="what you want to buy")
    parser.add_argument("--market", choices=["all", "local", "global"], default="all")
    parser.add_argument("--stores", help="comma-separated store keys to limit the search")
    parser.add_argument("--limit", type=int, default=6, help="results per store")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of a table")
    parser.add_argument("--include-used", action="store_true",
                        help="include refurbished/renewed/used listings")
    parser.add_argument("--history", action="store_true",
                        help="list past searches and how their prices have moved")
    parser.add_argument("--recheck", metavar="ID",
                        help="re-run a saved search by id, or 'all'")
    parser.add_argument("--forget", type=int, metavar="ID",
                        help="delete a saved search and its price history")
    parser.add_argument("--no-save", action="store_true",
                        help="do not record this search in the history")
    parser.add_argument("--blocked", action="store_true",
                        help="list stores currently being skipped as blocked")
    parser.add_argument("--unblock", metavar="STORE",
                        help="retry a blocked store now, or 'all'")
    args = parser.parse_args()

    try:
        if args.forget is not None:
            gone = history.delete(args.forget)
            out(f"{'Deleted' if gone else 'No such'} saved search #{args.forget}")
            return 0 if gone else 1

        if args.blocked:
            render_blocked()
            return 0

        if args.unblock:
            target = None if args.unblock.lower() == "all" else args.unblock
            removed = blocklist.clear(target)
            if removed:
                out(f"Cleared {removed} blocked-store record(s); they will be "
                    f"tried again on the next search.")
                return 0

            out(f"{target or 'No store'} was not on the blocked list.")
            from app.config import STORES
            if target and target in STORES and not STORES[target].enabled:
                out(f"{YELLOW}note:{RESET} {target} is switched off by "
                    f"DISABLED_STORES, which is a separate thing. Clear that "
                    f"variable to use it again.")
            elif target and target not in STORES:
                out(f"{YELLOW}note:{RESET} no store is called {target!r}. "
                    f"Known: {', '.join(STORES)}")
            return 1

        if args.history:
            render_history()
            return 0

        if args.recheck:
            return await recheck(args.recheck, args)

        if not args.query:
            parser.error("give a query, or use --history / --recheck")

        try:
            response = await run_search(args.query, args)
        except ValueError as exc:
            out(f"{RED}Cannot run that search:{RESET} {exc}")
            return 2
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            render(response)
            render_changes(response, args)
        return 0 if response.offers else 1
    finally:
        await net.close_client()
        await browser.close_browser()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
