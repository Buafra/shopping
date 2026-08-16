"""Test-session guardrails.

These must be set before `app.config` is imported, because Settings reads the
environment once at import time. conftest is loaded ahead of the test modules,
so this is the right place.

The browser fallback is disabled for the whole suite: a unit test that quietly
launches Chromium and hits a live storefront is slow, flaky, and no longer
testing the code under test.
"""

import os

os.environ["USE_BROWSER_FALLBACK"] = "0"
os.environ.setdefault("MAX_RETRIES", "0")
os.environ.setdefault("REQUEST_TIMEOUT", "5")
