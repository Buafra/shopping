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
    """Raised when a URL could not be fetched after all retries."""


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
            if resp.status_code >= 400:
                raise FetchError(f"HTTP {resp.status_code} from {url}")
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < SETTINGS.max_retries:
                await asyncio.sleep(1.5 * (2**attempt) + random.random())
                continue
            raise FetchError(f"{type(exc).__name__} fetching {url}") from exc

    raise FetchError(f"exhausted retries for {url}") from last_exc


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
