"""Remember which stores are blocking us, and stop paying for them.

A store serving a CAPTCHA does not change its mind within a search, or within
an hour. Retrying it on every query costs the full timeout budget each time —
Noon alone turned a 10-second search into 55 — and returns the same refusal.

So a store that reports `blocked` is skipped for a while. The record is kept
next to the search history, expires on its own, and never hides a store that
merely had an off day: only a definite block counts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .history import connect

log = logging.getLogger(__name__)

# Kinds that mean "this store is refusing us", as opposed to a transient fault.
BLOCKING_KINDS = {"blocked"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_blocks (
    store      TEXT PRIMARY KEY,
    blocked_at TEXT NOT NULL,
    reason     TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure(conn) -> None:
    conn.executescript(SCHEMA)


def remember(store: str, reason: str, db_path: Path | str | None = None) -> None:
    with connect(db_path) as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO store_blocks (store, blocked_at, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(store) DO UPDATE SET blocked_at = excluded.blocked_at, "
            "reason = excluded.reason",
            (store, _now().isoformat(timespec="seconds"), reason[:200]),
        )
    log.info("%s marked as blocking; it will be skipped for a while", store)


def blocked(ttl_hours: float = 24.0, db_path: Path | str | None = None) -> dict[str, str]:
    """Stores blocked within the TTL, mapped to why. Expired rows are dropped."""
    cutoff = _now() - timedelta(hours=ttl_hours)
    live: dict[str, str] = {}
    stale: list[str] = []

    with connect(db_path) as conn:
        _ensure(conn)
        for row in conn.execute("SELECT store, blocked_at, reason FROM store_blocks"):
            try:
                when = datetime.fromisoformat(row["blocked_at"])
            except ValueError:
                stale.append(row["store"])
                continue
            if when >= cutoff:
                live[row["store"]] = row["reason"]
            else:
                stale.append(row["store"])

        for store in stale:
            conn.execute("DELETE FROM store_blocks WHERE store = ?", (store,))

    return live


def clear(store: str | None = None, db_path: Path | str | None = None) -> int:
    """Forget one store's block, or all of them. Returns rows removed."""
    with connect(db_path) as conn:
        _ensure(conn)
        if store:
            cursor = conn.execute("DELETE FROM store_blocks WHERE store = ?", (store,))
        else:
            cursor = conn.execute("DELETE FROM store_blocks")
        return cursor.rowcount
