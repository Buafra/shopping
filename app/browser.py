"""Playwright fallback for stores that render their results in JavaScript,
or that block plain HTTP clients.

One Chromium instance is shared across the process and each scrape gets its
own isolated context, so cookies from one store never leak into another.
Images/fonts/media are aborted — we only ever want the DOM, and blocking them
cuts page load time roughly in half.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import random
from urllib.parse import unquote, urlparse

from .config import SETTINGS, USER_AGENTS

log = logging.getLogger(__name__)


# Words that only appear in a proxy URL nobody has filled in yet. Pasting the
# documented example verbatim points every request at a host that does not
# exist, which fails identically to having no internet at all.
PROXY_PLACEHOLDERS = (
    "user:pass", "username:password", "your-proxy", "yourproxy",
    "provider.com", "example.com", "example.net", "changeme",
    "host:port", "proxy-host",
)


def proxy_looks_unconfigured(raw: str | None) -> bool:
    return bool(raw) and any(token in raw.lower() for token in PROXY_PLACEHOLDERS)


def proxy_settings() -> dict | None:
    """Translate SCRAPER_PROXY into Playwright's proxy option.

    Without this the browser fallback ignores the proxy entirely and goes out
    on the real IP — so a store that blocks you would still see you, and the
    setting would appear to half-work for reasons nobody could see.
    """
    raw = SETTINGS.proxy_url
    if not raw:
        return None

    if proxy_looks_unconfigured(raw):
        log.warning(
            "SCRAPER_PROXY still contains placeholder text (%s) — ignoring it "
            "and going direct. Replace it with a real proxy, or unset it.", raw
        )
        return None

    parsed = urlparse(raw)
    if not parsed.hostname:
        log.warning("SCRAPER_PROXY %r is not a usable URL; ignoring", raw)
        return None

    server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"

    settings: dict = {"server": server}
    if parsed.username:
        settings["username"] = unquote(parsed.username)
    if parsed.password:
        settings["password"] = unquote(parsed.password)
    return settings


def find_chromium() -> str | None:
    """Locate a usable Chromium binary.

    Playwright pins an exact browser build per release, so a pip upgrade can
    leave it pointing at a directory that does not exist while a perfectly
    good Chromium sits next to it. Prefer an explicit CHROMIUM_PATH, then any
    browser Playwright already downloaded, then a system install.
    """
    explicit = os.environ.get("CHROMIUM_PATH")
    if explicit and os.path.exists(explicit):
        return explicit

    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers",
             os.path.expanduser("~/.cache/ms-playwright")]

    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        # A stable symlink, when the image provides one.
        direct = os.path.join(root, "chromium")
        if os.path.isfile(direct) or os.path.islink(direct):
            resolved = os.path.realpath(direct)
            if os.path.exists(resolved):
                return resolved
        # Otherwise take the highest-numbered build present.
        for pattern in ("chromium-*/chrome-linux/chrome",
                        "chromium_headless_shell-*/chrome-linux/headless_shell"):
            found = sorted(glob.glob(os.path.join(root, pattern)))
            if found:
                return found[-1]

    for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome"):
        if os.path.exists(candidate):
            return candidate

    return None

_playwright = None
_browser = None
_lock = asyncio.Lock()

_BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}


class BrowserUnavailable(RuntimeError):
    """Playwright or Chromium is not usable in this environment."""


# Chromium reports network failures as ERR_* codes inside the exception text.
_NETWORK_MARKERS = (
    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED",
    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_TIMED_OUT", "ERR_ADDRESS_UNREACHABLE",
)


def describe_error(exc: Exception, host: str = "") -> str:
    """Turn a raw Playwright exception into something a human can act on."""
    text = str(exc)
    where = f" {host}" if host else ""

    if any(marker in text for marker in _NETWORK_MARKERS):
        return (
            f"cannot reach{where} — the network or proxy refused the connection "
            f"(the store itself may be fine)"
        )
    if "ERR_CERT" in text or "SSL" in text:
        return f"TLS verification failed for{where} — check your CA configuration"
    if "Timeout" in text or "timeout" in text:
        return f"{host or 'the page'} did not finish loading in time"
    return f"browser could not load{where}: {text.splitlines()[0][:140]}"


async def _get_browser():
    global _playwright, _browser
    if _browser is not None and _browser.is_connected():
        return _browser

    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on install
            raise BrowserUnavailable("playwright is not installed") from exc

        launch_args: dict = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                # Some storefronts terminate the HTTP/2 stream rather than
                # answering, which surfaces as ERR_HTTP2_PROTOCOL_ERROR and no
                # page at all. Falling back to HTTP/1.1 costs a little speed
                # and gets a response.
                "--disable-http2",
                "--disable-quic",
            ],
        }

        # Chromium wants the proxy at launch; a context-level proxy alone is
        # not honoured reliably for all request types.
        proxy = proxy_settings()
        if proxy:
            launch_args["proxy"] = proxy
            log.info("browser will route through proxy %s", proxy["server"])

        try:
            _playwright = await async_playwright().start()
        except Exception as exc:  # pragma: no cover - environment dependent
            raise BrowserUnavailable(f"could not start playwright: {exc}") from exc

        try:
            _browser = await _playwright.chromium.launch(**launch_args)
        except Exception as first_error:
            # Playwright's pinned build is missing — fall back to whatever
            # Chromium this machine actually has.
            executable = find_chromium()
            if not executable:
                raise BrowserUnavailable(
                    f"could not launch chromium: {first_error}"
                ) from first_error
            try:
                _browser = await _playwright.chromium.launch(
                    executable_path=executable, **launch_args
                )
                log.info("launched chromium via fallback binary %s", executable)
            except Exception as exc:  # pragma: no cover - environment dependent
                raise BrowserUnavailable(
                    f"could not launch chromium at {executable}: {exc}"
                ) from exc
    return _browser


async def close_browser() -> None:
    global _playwright, _browser
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:  # pragma: no cover
            pass
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:  # pragma: no cover
            pass
    _browser = None
    _playwright = None


async def render(
    url: str,
    *,
    wait_for: str | None = None,
    locale: str = "en-AE",
    timezone: str = "Asia/Dubai",
    extra_wait_ms: int = 1200,
) -> str:
    """Load `url` in a real browser and return the settled HTML.

    `wait_for` is a CSS selector worth waiting on; if it never appears we still
    return whatever rendered, because a partial page often still parses.
    """
    if not SETTINGS.use_browser_fallback:
        raise BrowserUnavailable("browser fallback disabled by configuration")

    browser = await _get_browser()
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale=locale,
        timezone_id=timezone,
        viewport={"width": 1440, "height": 900},
        java_script_enabled=True,
        extra_http_headers={"Accept-Language": f"{locale},en;q=0.9"},
    )
    # Hide the most obvious automation tell before any page script runs.
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    page = None
    try:
        page = await context.new_page()

        async def _route(route, request):
            try:
                if request.resource_type in _BLOCKED_RESOURCES:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                # The page can be torn down mid-flight (cancellation, or the
                # site aborting navigation). A route handler that raises here
                # leaves an orphaned future nobody awaits, which surfaces later
                # as "Future exception was never retrieved".
                pass

        await page.route("**/*", _route)

        # Keep navigation inside the phase budget. If goto is still running
        # when the caller's timeout fires, Playwright's own future errors with
        # ERR_ABORTED after the context is gone and prints an unretrieved
        # exception traceback over the results.
        goto_timeout = max(
            5.0, min(SETTINGS.browser_timeout, SETTINGS.browser_phase_timeout - 8.0)
        )
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=int(goto_timeout * 1000)
            )
        except Exception as exc:
            # A partial page still parses; only give up if nothing loaded.
            log.debug("navigation to %s did not complete: %s", url, exc)
            try:
                if not (await page.content()).strip():
                    raise
            except Exception:
                raise

        if wait_for:
            try:
                await page.wait_for_selector(wait_for, timeout=8_000)
            except Exception:
                log.debug("selector %r never appeared on %s", wait_for, url)

        if extra_wait_ms:
            try:
                await page.wait_for_timeout(extra_wait_ms)
            except Exception:
                pass

        return await page.content()
    finally:
        # Close the page before the context so in-flight navigations are torn
        # down in order; shielded so cancellation cannot skip the cleanup and
        # leak a browser context per failed store.
        for closer in (page, context):
            if closer is None:
                continue
            try:
                await asyncio.shield(closer.close())
            except Exception:
                pass
