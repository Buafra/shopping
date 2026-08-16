"""Search history and price tracking.

A price comparison is a snapshot of one moment. The useful question a day
later is not "what does this cost" but "has it moved" — and answering that
needs the previous answer kept.

Every search is recorded against the query that produced it, so re-running it
compares like with like. Offers are matched between runs by URL, because that
is the only stable identity a listing has: titles get re-edited, positions
shuffle, and prices are the thing being measured.

SQLite via the standard library — no new dependency, one file, and it survives
restarts.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import SearchResponse

DEFAULT_DB = Path(os.environ.get("HISTORY_DB", "history.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT    NOT NULL,
    market          TEXT    NOT NULL DEFAULT 'all',
    include_used    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    last_checked_at TEXT    NOT NULL,
    UNIQUE (query, market, include_used)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    taken_at        TEXT    NOT NULL,
    offer_count     INTEGER NOT NULL,
    best_landed_aed REAL
);

CREATE TABLE IF NOT EXISTS snapshot_offers (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    url          TEXT    NOT NULL,
    store        TEXT    NOT NULL,
    store_label  TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    landed_aed   REAL    NOT NULL,
    price_aed    REAL,
    currency     TEXT,
    rating       REAL,
    review_count INTEGER,
    score        REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_search ON snapshots(search_id, taken_at);
CREATE INDEX IF NOT EXISTS idx_offers_snapshot ON snapshot_offers(snapshot_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- records ---

@dataclass
class TrackedOffer:
    url: str
    store_label: str
    title: str
    landed_aed: float
    previous_landed_aed: float | None = None

    @property
    def delta(self) -> float | None:
        if self.previous_landed_aed is None:
            return None
        return round(self.landed_aed - self.previous_landed_aed, 2)

    @property
    def pct(self) -> float | None:
        if not self.previous_landed_aed:
            return None
        return round(
            (self.landed_aed - self.previous_landed_aed) / self.previous_landed_aed * 100, 1
        )

    @property
    def direction(self) -> str:
        delta = self.delta
        if delta is None:
            return "new"
        if delta < -0.01:
            return "down"
        if delta > 0.01:
            return "up"
        return "same"


@dataclass
class SearchRecord:
    id: int
    query: str
    market: str
    include_used: bool
    created_at: str
    last_checked_at: str
    checks: int = 0
    latest_best_aed: float | None = None
    previous_best_aed: float | None = None

    @property
    def best_delta(self) -> float | None:
        if self.latest_best_aed is None or self.previous_best_aed is None:
            return None
        return round(self.latest_best_aed - self.previous_best_aed, 2)


@dataclass
class PriceChanges:
    """What moved between the two most recent runs of one search."""

    search: SearchRecord
    changed: list[TrackedOffer] = field(default_factory=list)
    appeared: list[TrackedOffer] = field(default_factory=list)
    disappeared: list[TrackedOffer] = field(default_factory=list)
    unchanged: int = 0
    is_first_run: bool = False

    @property
    def cheapest_drop(self) -> TrackedOffer | None:
        drops = [o for o in self.changed if o.direction == "down"]
        return min(drops, key=lambda o: o.delta or 0) if drops else None

    def summary(self) -> str:
        if self.is_first_run:
            return "First run — nothing to compare against yet."
        if not (self.changed or self.appeared or self.disappeared):
            return "No price movement since the last check."

        bits = []
        drops = [o for o in self.changed if o.direction == "down"]
        rises = [o for o in self.changed if o.direction == "up"]
        if drops:
            bits.append(f"{len(drops)} cheaper")
        if rises:
            bits.append(f"{len(rises)} dearer")
        if self.appeared:
            bits.append(f"{len(self.appeared)} new")
        if self.disappeared:
            bits.append(f"{len(self.disappeared)} gone")
        return ", ".join(bits)


# ------------------------------------------------------------------ write ---

def is_total_failure(response: SearchResponse) -> bool:
    """True when nothing came back because nothing could be reached.

    "No offers because the stores had none" and "no offers because the network
    was down" look identical in the result, but only the first is an
    observation about prices.
    """
    return not response.offers and bool(response.stores) and not any(
        s.ok for s in response.stores
    )


def record(
    response: SearchResponse,
    *,
    market: str = "all",
    include_used: bool = False,
    db_path: Path | str | None = None,
) -> int | None:
    """Save this result as the newest snapshot of its search. Returns search id.

    A search that legitimately found nothing is still recorded: "the stores had
    none today" is a real observation, and dropping it would break the timeline.

    A search where every store *failed* is not recorded at all. Storing it would
    write a snapshot with no offers, so the next comparison would report every
    tracked listing as gone and the one after that would report them all as new
    — a fortnight of invented price history from one flat tyre.
    """
    if is_total_failure(response):
        return None

    with connect(db_path) as conn:
        now = _now()
        conn.execute(
            """INSERT INTO searches (query, market, include_used, created_at, last_checked_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(query, market, include_used)
               DO UPDATE SET last_checked_at = excluded.last_checked_at""",
            (response.query, market, int(include_used), now, now),
        )
        search_id = conn.execute(
            "SELECT id FROM searches WHERE query = ? AND market = ? AND include_used = ?",
            (response.query, market, int(include_used)),
        ).fetchone()["id"]

        best = min((o.landed_aed for o in response.offers if o.landed_aed), default=None)
        cursor = conn.execute(
            "INSERT INTO snapshots (search_id, taken_at, offer_count, best_landed_aed) "
            "VALUES (?, ?, ?, ?)",
            (search_id, now, len(response.offers), best),
        )
        snapshot_id = cursor.lastrowid

        conn.executemany(
            """INSERT INTO snapshot_offers
               (snapshot_id, url, store, store_label, title, landed_aed,
                price_aed, currency, rating, review_count, score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (snapshot_id, _key(o.url), o.store, o.store_label, o.title,
                 o.landed_aed or o.price_aed or o.price, o.price_aed, o.currency,
                 o.rating, o.review_count, o.score)
                for o in response.offers
            ],
        )
        return search_id


def _key(url: str) -> str:
    """Listings keep their identity in the path; query strings carry tracking
    parameters that change between fetches."""
    return (url or "").split("?")[0].rstrip("/")


# ------------------------------------------------------------------- read ---

def list_searches(db_path: Path | str | None = None) -> list[SearchRecord]:
    """Most recently checked first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM snapshots sn WHERE sn.search_id = s.id) AS checks
               FROM searches s
               ORDER BY s.last_checked_at DESC"""
        ).fetchall()

        records = []
        for row in rows:
            bests = conn.execute(
                "SELECT best_landed_aed FROM snapshots WHERE search_id = ? "
                "ORDER BY taken_at DESC, id DESC LIMIT 2",
                (row["id"],),
            ).fetchall()
            records.append(
                SearchRecord(
                    id=row["id"], query=row["query"], market=row["market"],
                    include_used=bool(row["include_used"]),
                    created_at=row["created_at"],
                    last_checked_at=row["last_checked_at"],
                    checks=row["checks"],
                    latest_best_aed=bests[0]["best_landed_aed"] if bests else None,
                    previous_best_aed=bests[1]["best_landed_aed"] if len(bests) > 1 else None,
                )
            )
        return records


def get_search(search_id: int, db_path: Path | str | None = None) -> SearchRecord | None:
    for record_ in list_searches(db_path):
        if record_.id == search_id:
            return record_
    return None


def compare(search_id: int, db_path: Path | str | None = None) -> PriceChanges | None:
    """Diff the two most recent snapshots of a search."""
    record_ = get_search(search_id, db_path)
    if record_ is None:
        return None

    with connect(db_path) as conn:
        snapshots = conn.execute(
            "SELECT id FROM snapshots WHERE search_id = ? ORDER BY taken_at DESC, id DESC LIMIT 2",
            (search_id,),
        ).fetchall()

        if not snapshots:
            return PriceChanges(search=record_, is_first_run=True)

        latest = _offers_of(conn, snapshots[0]["id"])
        if len(snapshots) < 2:
            return PriceChanges(
                search=record_,
                appeared=[TrackedOffer(**o) for o in latest.values()],
                is_first_run=True,
            )

        previous = _offers_of(conn, snapshots[1]["id"])

    changes = PriceChanges(search=record_)
    for url, current in latest.items():
        before = previous.get(url)
        if before is None:
            changes.appeared.append(TrackedOffer(**current))
            continue
        tracked = TrackedOffer(**current, previous_landed_aed=before["landed_aed"])
        if tracked.direction == "same":
            changes.unchanged += 1
        else:
            changes.changed.append(tracked)

    for url, gone in previous.items():
        if url not in latest:
            changes.disappeared.append(TrackedOffer(**gone))

    changes.changed.sort(key=lambda o: o.delta or 0)
    return changes


def _offers_of(conn, snapshot_id: int) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT url, store_label, title, landed_aed FROM snapshot_offers "
        "WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {
        row["url"]: {
            "url": row["url"], "store_label": row["store_label"],
            "title": row["title"], "landed_aed": row["landed_aed"],
        }
        for row in rows
    }


def delete(search_id: int, db_path: Path | str | None = None) -> bool:
    with connect(db_path) as conn:
        # Snapshots and their offers go with it — the FK cascade needs the
        # pragma set on every connection, which connect() does.
        cursor = conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        return cursor.rowcount > 0
