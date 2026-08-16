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

from .config import SETTINGS, USER_AGENTS

log = logging.getLogger(__name__)


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

        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
            ],
        }

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

    try:
        page = await context.new_page()

        async def _route(route, request):
            if request.resource_type in _BLOCKED_RESOURCES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", _route)

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=int(SETTINGS.browser_timeout * 1000),
        )

        if wait_for:
            try:
                await page.wait_for_selector(wait_for, timeout=8_000)
            except Exception:
                log.debug("selector %r never appeared on %s", wait_for, url)

        if extra_wait_ms:
            await page.wait_for_timeout(extra_wait_ms)

        return await page.content()
    finally:
        await context.close()
