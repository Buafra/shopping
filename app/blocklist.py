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

# Kinds that mean "the store answered, and there was nothing usable in it".
# Not the same as a refusal and not necessarily permanent, so these are counted
# rather than acted on immediately — one empty page is a query with no results,
# several in a row is a store this app cannot currently read.
UNPRODUCTIVE_KINDS = {"parse", "no_results"}

# How many consecutive empty runs before a store is rested, and for how long.
# Deliberately shorter than a block: a block is a decision the store made, this
# is a guess we are making about it, so it should be revisited sooner.
REST_AFTER_FAILURES = 3
REST_HOURS = 6.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_blocks (
    store      TEXT PRIMARY KEY,
    blocked_at TEXT NOT NULL,
    reason     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS store_failures (
    store    TEXT PRIMARY KEY,
    streak   INTEGER NOT NULL,
    last_at  TEXT NOT NULL,
    reason   TEXT NOT NULL
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
            conn.execute("DELETE FROM store_failures WHERE store = ?", (store,))
        else:
            cursor = conn.execute("DELETE FROM store_blocks")
            conn.execute("DELETE FROM store_failures")
        return cursor.rowcount


# -- stores that keep answering with nothing --------------------------------

def record_outcome(
    store: str, produced: bool, reason: str = "",
    db_path: Path | str | None = None,
) -> None:
    """Note whether a store returned anything usable this run.

    One empty result is a query with no matches. Several in a row is a store
    this app cannot read — and reading it for twenty seconds on every search
    costs more than the store is currently worth.
    """
    with connect(db_path) as conn:
        _ensure(conn)
        if produced:
            conn.execute("DELETE FROM store_failures WHERE store = ?", (store,))
            return
        conn.execute(
            "INSERT INTO store_failures (store, streak, last_at, reason) "
            "VALUES (?, 1, ?, ?) "
            "ON CONFLICT(store) DO UPDATE SET "
            "  streak = store_failures.streak + 1, "
            "  last_at = excluded.last_at, reason = excluded.reason",
            (store, _now().isoformat(timespec="seconds"), reason[:200]),
        )


def resting(
    after: int = REST_AFTER_FAILURES,
    ttl_hours: float = REST_HOURS,
    db_path: Path | str | None = None,
) -> dict[str, str]:
    """Stores that have come back empty `after` times running, and are resting.

    The streak survives the rest period; only the timer expires. A store that
    is still broken goes straight back to resting after one more empty run,
    and a store that works clears its streak entirely.
    """
    cutoff = _now() - timedelta(hours=ttl_hours)
    live: dict[str, str] = {}

    with connect(db_path) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT store, streak, last_at, reason FROM store_failures "
            "WHERE streak >= ?", (after,)
        ).fetchall()
        for row in rows:
            try:
                when = datetime.fromisoformat(row["last_at"])
            except ValueError:
                continue
            if when >= cutoff:
                live[row["store"]] = (
                    f"{row['streak']} empty runs in a row: {row['reason']}"
                )
    return live
