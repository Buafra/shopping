"""Optional TLS-impersonating transport.

Stores that block this app are not reacting to load — eight requests per
search is nothing. They are identifying the *client*. Python's TLS handshake
has a different shape from Chrome's (cipher order, extensions, ALPN, HTTP/2
settings), and that fingerprint — commonly called JA3 — identifies the client
before a single byte of HTTP is sent. Noon accepting the connection and then
never answering is the classic signature.

`curl_cffi` performs the handshake with Chrome's fingerprint instead, which is
the only part of the request that headers cannot fix.

It is optional. If the package is absent, the app runs exactly as before on
httpx; nothing here is required for the stores that already work.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from .config import SETTINGS, USER_AGENTS

log = logging.getLogger(__name__)

# Newer builds tend to match current Chrome more closely; picking randomly
# from a small recent set avoids every request looking identical.
IMPERSONATE_TARGETS = ["chrome124", "chrome123", "chrome120", "chrome119"]

_available: bool | None = None


def is_available() -> bool:
    """Whether TLS impersonation can be used, logged once either way."""
    global _available
    if _available is None:
        from importlib.util import find_spec

        if find_spec("curl_cffi") is not None:
            _available = True
            log.info("TLS impersonation available (curl_cffi)")
        else:
            _available = False
            log.info(
                "curl_cffi not installed — requests use the default TLS "
                "fingerprint, which some stores reject on sight"
            )
    return _available


def enabled() -> bool:
    return SETTINGS.use_tls_impersonation and is_available()


class ImpersonatedResponse:
    """Enough of the httpx.Response surface for the rest of the app.

    Providers only ever read `.text`, `.status_code`, `.json()` and
    `.http_version`, so adapting those keeps one code path for both transports.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.status_code: int = raw.status_code
        self.text: str = raw.text
        self.headers = raw.headers
        self.url = str(getattr(raw, "url", ""))

    def json(self) -> Any:
        return self._raw.json()

    @property
    def http_version(self) -> str:
        version = getattr(self._raw, "http_version", None)
        # curl reports an enum or int depending on build; normalise to a label.
        mapping = {2: "HTTP/2", 3: "HTTP/3", 1: "HTTP/1.1", 11: "HTTP/1.1"}
        if isinstance(version, int):
            return mapping.get(version, f"HTTP/{version}")
        return str(version or "HTTP/1.1")


async def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    json_body: Any = None,
    timeout: float | None = None,
) -> ImpersonatedResponse:
    """Perform one request with a browser TLS fingerprint.

    Raises whatever curl_cffi raises; the caller classifies it exactly as it
    classifies httpx failures, so both transports report problems the same way.
    """
    from curl_cffi import requests as curl_requests

    merged = dict(headers or {})
    # The impersonation profile supplies its own User-Agent; a mismatched one
    # is itself a signal, so let curl_cffi own that header.
    merged.pop("User-Agent", None)

    async with curl_requests.AsyncSession() as session:
        raw = await session.request(
            method,
            url,
            headers=merged,
            params=params,
            json=json_body,
            timeout=timeout or SETTINGS.request_timeout,
            impersonate=random.choice(IMPERSONATE_TARGETS),
            proxies={"https": SETTINGS.proxy_url, "http": SETTINGS.proxy_url}
            if SETTINGS.proxy_url else None,
            allow_redirects=True,
        )
    return ImpersonatedResponse(raw)


def user_agent_for_headers() -> str:
    """A UA for the httpx path only; curl_cffi sets its own."""
    return random.choice(USER_AGENTS)
