"""Shared HTTP plumbing: a pooled client, browser-ish headers, retries.

Store sites are hostile to obvious bots, so every request carries a plausible
browser header set and retries with backoff on the failures that are worth
retrying (timeouts, 5xx, 429). A 403/404 is not retried — that is the site
telling us something it will keep telling us.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

import httpx

from .config import SETTINGS, USER_AGENTS

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def base_headers(locale: str = "en-AE,en;q=0.9,ar;q=0.8") -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": locale,
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Chromium";v="126", "Not:A-Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Connection": "keep-alive",
    }


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(SETTINGS.request_timeout),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                    proxy=SETTINGS.proxy_url,
                    http2=False,
                )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class FetchError(RuntimeError):
    """Raised when a URL could not be fetched after all retries.

    `kind` says *why*, because the fix differs completely: a network problem is
    yours to solve, a block needs a different IP, and a bad status usually means
    the store changed its endpoint.
    """

    def __init__(self, message: str, kind: str = "error", hint: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} — {self.hint}" if self.hint else base


# Status codes storefronts use when they think you are a bot.
BLOCK_STATUS = {401, 403, 407, 429, 503}


def classify_transport_error(exc: Exception, url: str) -> FetchError:
    """Say *why* a request failed, in terms that point at the fix.

    Dispatch on the exception type first: httpx raises ProxyError with a bare
    "403 Forbidden" body, so matching on message text alone misfiles the single
    most common failure in a locked-down network.
    """
    text = str(exc).lower()
    host = url.split("/")[2] if "://" in url else url
    detail = str(exc).strip().splitlines()[0][:80] if str(exc).strip() else ""

    if isinstance(exc, httpx.ProxyError) or "tunnel" in text or "connect_rejected" in text:
        return FetchError(
            f"cannot reach {host} through the proxy",
            kind="unreachable",
            hint=f"the proxy or network policy refused the connection"
                 f"{f' ({detail})' if detail else ''}",
        )
    if isinstance(exc, httpx.TimeoutException) or "timed out" in text:
        return FetchError(f"{host} did not respond in time", kind="timeout",
                          hint="raise REQUEST_TIMEOUT or try again")
    if any(s in text for s in ("name or service not known", "nodename", "getaddrinfo",
                               "no address associated", "temporary failure in name")):
        return FetchError(f"cannot resolve {host}", kind="unreachable",
                          hint="DNS lookup failed — check your connection")
    if isinstance(exc, httpx.ConnectError):
        return FetchError(f"cannot connect to {host}", kind="unreachable",
                          hint=detail or "connection refused")
    return FetchError(f"network error reaching {host}", kind="unreachable",
                      hint=detail)


async def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    json_body: Any = None,
) -> httpx.Response:
    client = await get_client()
    merged = base_headers()
    if headers:
        merged.update(headers)

    last_exc: Exception | None = None
    for attempt in range(SETTINGS.max_retries + 1):
        try:
            resp = await client.request(
                method, url, headers=merged, params=params, json=json_body
            )
            if resp.status_code in RETRY_STATUS and attempt < SETTINGS.max_retries:
                await asyncio.sleep(1.5 * (2**attempt) + random.random())
                merged["User-Agent"] = random.choice(USER_AGENTS)
                continue
            if resp.status_code in BLOCK_STATUS:
                raise FetchError(
                    f"HTTP {resp.status_code} from {url.split('/')[2]}",
                    kind="blocked",
                    hint="the store refused an automated request; try "
                         "SCRAPER_PROXY or a residential IP",
                )
            if resp.status_code >= 400:
                raise FetchError(
                    f"HTTP {resp.status_code} from {url.split('/')[2]}",
                    kind="http_error",
                    hint="the store's search endpoint may have moved",
                )
            return resp
        except FetchError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < SETTINGS.max_retries:
                await asyncio.sleep(1.5 * (2**attempt) + random.random())
                continue
            raise classify_transport_error(exc, url) from exc

    raise FetchError(f"exhausted retries for {url}", kind="unreachable") from last_exc


# --------------------------------------------------------------------------
# Parsing helpers — every scraper needs these, and getting them subtly wrong
# is the main source of garbage results.
# --------------------------------------------------------------------------

_PRICE_CLEAN = re.compile(r"[^\d.,]")
_NUM_IN_TEXT = re.compile(r"\d[\d.,\s]*")


def parse_price(raw: str | float | int | None) -> float | None:
    """Pull a float out of messy price text.

    Handles "AED 1,299.00", "US $45.99", "1.299,00 د.إ" and bare numbers.
    Returns None rather than guessing when there is no usable number.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None

    text = str(raw).strip()
    if not text:
        return None

    # Price ranges ("45.00 - 89.00") — take the low end, that's what a shopper
    # would be quoted before choosing options.
    text = text.split("-")[0] if text.count("-") == 1 and "--" not in text else text

    cleaned = _PRICE_CLEAN.sub("", text).strip(" .,")
    if not cleaned:
        return None

    # Decide whether ',' or '.' is the decimal separator.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):  # 1.299,00 (EU style)
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:                                        # 1,299.00 (US style)
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        tail = cleaned.rsplit(",", 1)[1]
        cleaned = cleaned.replace(",", ".") if len(tail) == 2 else cleaned.replace(",", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_int(raw: str | int | None) -> int | None:
    """Extract a review count from text like "(1,234)" or "2.5K ratings"."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None

    text = str(raw).strip().lower()
    if not text:
        return None

    multiplier = 1
    if re.search(r"\d\s*k\b", text):
        multiplier = 1_000
    elif re.search(r"\d\s*m\b", text):
        multiplier = 1_000_000

    match = _NUM_IN_TEXT.search(text)
    if not match:
        return None
    number = parse_price(match.group(0))
    if number is None:
        return None
    return int(number * multiplier)


_RATING_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def parse_rating(raw: str | float | None, scale: float = 5.0) -> float | None:
    """Extract a 0–5 star rating from "4.5 out of 5 stars", "4,3", 4.5, …

    Unlike prices, only the *first* number matters here: "4.5 out of 5 stars"
    must read as 4.5, not as the digits 4.5 and 5 run together. A comma is
    always a decimal separator in a rating — nobody has 4,300 stars.
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        value: float | None = float(raw)
    else:
        match = _RATING_NUM.search(str(raw))
        if not match:
            return None
        try:
            value = float(match.group(0).replace(",", "."))
        except ValueError:
            return None

    if value is None:
        return None
    if scale != 5.0 and scale > 0:
        value = value * 5.0 / scale
    if value <= 0 or value > 5.0:
        return None
    return round(value, 2)


def absolutise(href: str | None, origin: str) -> str | None:
    """Turn a possibly-relative href into a full URL."""
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return origin.rstrip("/") + href


# Pages a store serves *instead* of results when it thinks you are a bot.
# These arrive as HTTP 200 with ordinary-looking HTML, so nothing upstream
# notices; the only tell is what the page says.
BOT_WALL_MARKERS: list[tuple[str, str]] = [
    ("captcha interception", "CAPTCHA challenge"),
    ("pardon our interruption", "bot-detection interstitial"),
    ("captcha", "CAPTCHA challenge"),
    ("are you a human", "bot challenge"),
    ("verify you are human", "bot challenge"),
    ("unusual traffic", "rate-limit challenge"),
    ("access denied", "access-denied page"),
    ("cf-browser-verification", "Cloudflare interstitial"),
    ("just a moment", "Cloudflare interstitial"),
    ("attention required", "Cloudflare block"),
    ("px-captcha", "PerimeterX challenge"),
    ("/_incapsula_", "Imperva challenge"),
    ("error page | ebay", "eBay error page"),
]


def detect_bot_wall(html: str | None) -> str | None:
    """Name the challenge a page represents, or None if it looks like content.

    Only the head and the page title are inspected: the words "captcha" and
    "access denied" appear legitimately in product listings and help pages
    further down, and matching those would suppress real results.
    """
    if not html:
        return None

    head = html[:6000].lower()
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", head, re.S)
    if match:
        title = match.group(1).strip()

    for marker, meaning in BOT_WALL_MARKERS:
        if marker in title or marker in head[:2500]:
            return meaning
    return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def extract_json_object(text: str, marker: str) -> str | None:
    """Pull a complete JSON object out of inline script text.

    Several stores assign their catalogue to a JS variable. A regex cannot do
    this correctly — the payloads nest braces many levels deep, and a lazy
    `{.*?}` stops at the first inner brace. This scans for balanced braces
    while respecting string literals and escapes.
    """
    start = text.find(marker)
    if start == -1:
        return None

    brace = text.find("{", start + len(marker))
    if brace == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(brace, len(text)):
        char = text[index]

        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace : index + 1]

    return None  # unbalanced — the page was truncated
