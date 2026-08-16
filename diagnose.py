#!/usr/bin/env python3
"""Capture what a store actually returns, so selectors can be fixed from
evidence instead of guesswork.

    python diagnose.py sharaf_dg
    python diagnose.py carrefour_ae --query "airfryer"
    python diagnose.py --all

For each store it runs the HTTP path and the browser path separately, saves the
raw response under captures/, and prints a summary: what came back, whether the
current selectors match anything, and which repeated CSS classes look like
product cards. Paste the summary; the saved HTML stays on your machine.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import pathlib
import re
import sys

from selectolax.parser import HTMLParser

from app import browser, net
from app.config import STORES
from app.providers import build

CAPTURES = pathlib.Path(__file__).parent / "captures"

PRICE_HINT = re.compile(r"(AED|USD|\$|د\.إ|Dhs)\s?[\d,.]{2,}", re.I)

# Classes that appear on almost every page and never identify a product card.
BORING = re.compile(
    r"^(row|col|container|wrapper|flex|grid|btn|button|icon|hidden|active|"
    r"clearfix|sr-only|d-none|text-|mt-|mb-|px-|py-|w-|h-)", re.I
)


def summarise(html: str, provider, label: str) -> None:
    print(f"\n  --- {label} ---")
    if not html:
        print("    nothing returned")
        return

    print(f"    bytes            : {len(html):,}")
    tree = HTMLParser(html)

    title = tree.css_first("title")
    print(f"    <title>          : {(title.text()[:70] if title else '(none)')}")

    # Bot-wall detection: these pages are big and look normal but have no goods.
    lowered = html[:4000].lower()
    for marker, meaning in [
        ("captcha", "CAPTCHA challenge"),
        ("are you a human", "bot challenge"),
        ("access denied", "access denied page"),
        ("cf-browser-verification", "Cloudflare interstitial"),
        ("just a moment", "Cloudflare interstitial"),
        ("enable javascript", "JS-required shell"),
    ]:
        if marker in lowered:
            print(f"    !! looks like a {meaning}")

    has_next_data = "__NEXT_DATA__" in html
    print(f"    __NEXT_DATA__    : {'yes' if has_next_data else 'no'}")
    ldjson = tree.css('script[type="application/ld+json"]')
    print(f"    JSON-LD blocks   : {len(ldjson)}")
    if ldjson:
        kinds = set()
        for node in ldjson:
            kinds.update(re.findall(r'"@type"\s*:\s*"([^"]+)"', node.text() or ""))
        print(f"      @types         : {', '.join(sorted(kinds)) or '(none)'}")

    prices = PRICE_HINT.findall(html)
    print(f"    price-like tokens: {len(prices)}")

    # Do the provider's own selectors still match?
    print("    current selectors:")
    for name in ("RESULT_SELECTOR", "CARD_SELECTORS"):
        selectors = getattr(sys.modules[type(provider).__module__], name, None)
        if selectors is None:
            continue
        for sel in ([selectors] if isinstance(selectors, str) else selectors):
            print(f"      {len(tree.css(sel)):>4} x  {sel}")

    parsed = provider._parse(html, 10) if hasattr(provider, "_parse") else []
    # _parse is raw; _postprocess is what a search actually keeps after
    # dropping junk and de-duplicating, so report both.
    final = provider._postprocess(list(parsed), 10) if parsed else []
    print(f"    provider parsed  : {len(parsed)} raw -> {len(final)} after dedup")
    for offer in final[:3]:
        print(f"      - {offer.price:>9,.2f} {offer.currency}  {offer.title[:52]}")

    # Suggest candidates: repeated classes on elements that contain both a link
    # and something price-shaped. That is what a product card looks like.
    counter: collections.Counter = collections.Counter()
    for node in tree.css("div, li, article, section"):
        classes = (node.attributes.get("class") or "").split()
        if not classes:
            continue
        text = node.text() or ""
        if not PRICE_HINT.search(text) or not node.css_first("a[href]"):
            continue
        if len(text) > 700:          # too big to be one card
            continue
        for cls in classes:
            if not BORING.match(cls) and len(cls) > 3:
                counter[cls] += 1

    likely = [(c, n) for c, n in counter.most_common(12) if n >= 2][:8]
    if likely:
        print("    candidate card classes (repeated, contain a link + a price):")
        for cls, count in likely:
            print(f"      {count:>4} x  .{cls}")
    else:
        print("    no repeating price-bearing blocks found "
              "(page is probably a bot wall or fully client-rendered)")


async def run_store(key: str, query: str, save: bool) -> None:
    spec = STORES[key]
    provider = build(key)
    print(f"\n{'=' * 72}\n{spec.label}  ({key})\n{'=' * 72}")

    url = provider._url(query) if hasattr(provider, "_url") else "(custom endpoint)"
    print(f"  search URL: {url}")

    # -- HTTP path
    html = ""
    try:
        if hasattr(provider, "_url"):
            resp = await net.fetch(provider._url(query))
            html = resp.text
            print(f"  HTTP status: {resp.status_code}")
        else:
            offers = await provider.search_http(query, 6)
            print(f"  HTTP (JSON endpoint) returned {len(offers)} offers")
    except Exception as exc:
        print(f"  HTTP failed: {type(exc).__name__}: {exc}")

    if html:
        summarise(html, provider, "HTTP response")
        if save:
            path = CAPTURES / f"{key}-http.html"
            path.write_text(html, encoding="utf-8")
            print(f"    saved -> {path}")

    # -- browser path
    try:
        rendered = await browser.render(provider._url(query)) if hasattr(provider, "_url") else ""
    except Exception as exc:
        rendered = ""
        print(f"\n  browser failed: {type(exc).__name__}: "
              f"{str(exc).splitlines()[0][:110]}")

    if rendered:
        summarise(rendered, provider, "browser-rendered DOM")
        if save:
            path = CAPTURES / f"{key}-browser.html"
            path.write_text(rendered, encoding="utf-8")
            print(f"    saved -> {path}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stores", nargs="*", help="store keys, e.g. sharaf_dg noon")
    parser.add_argument("--all", action="store_true", help="every configured store")
    parser.add_argument("--query", default="sony wh-1000xm5")
    parser.add_argument("--no-save", action="store_true", help="do not write captures/")
    args = parser.parse_args()

    keys = list(STORES) if args.all else args.stores
    if not keys:
        parser.error(f"name at least one store, or --all. Known: {', '.join(STORES)}")
    unknown = [k for k in keys if k not in STORES]
    if unknown:
        parser.error(f"unknown store(s): {', '.join(unknown)}")

    save = not args.no_save
    if save:
        CAPTURES.mkdir(exist_ok=True)

    print(f'Diagnosing {len(keys)} store(s) with query "{args.query}"')
    try:
        for key in keys:
            await run_store(key, args.query, save)
    finally:
        await net.close_client()
        await browser.close_browser()

    print("\nDone. Paste the summaries above; captures/ stays local.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
