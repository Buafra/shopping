"""Skipping stores that are refusing us.

A CAPTCHA is not a transient fault — the same store answers the same way an
hour later — so retrying it every search only spends the timeout budget. These
tests pin both halves: remembering a real block, and not over-reaching.
"""

import pytest

from app import blocklist


@pytest.fixture
def db(tmp_path):
    return tmp_path / "history.db"


def test_a_blocked_store_is_remembered(db):
    blocklist.remember("noon", "served a CAPTCHA challenge", db_path=db)
    assert blocklist.blocked(db_path=db) == {"noon": "served a CAPTCHA challenge"}


def test_repeat_blocks_update_rather_than_duplicate(db):
    blocklist.remember("ebay", "first reason", db_path=db)
    blocklist.remember("ebay", "second reason", db_path=db)

    entries = blocklist.blocked(db_path=db)
    assert entries == {"ebay": "second reason"}


def test_blocks_expire(db):
    """A block is a snapshot of one bad day, not a permanent verdict — stores
    are retried once the record ages out."""
    blocklist.remember("noon", "CAPTCHA", db_path=db)
    assert blocklist.blocked(ttl_hours=24, db_path=db)
    assert blocklist.blocked(ttl_hours=0, db_path=db) == {}


def test_expired_records_are_cleaned_up(db):
    blocklist.remember("noon", "CAPTCHA", db_path=db)
    blocklist.blocked(ttl_hours=0, db_path=db)      # expires and deletes
    # Nothing left to clear, because reading already tidied up.
    assert blocklist.clear(db_path=db) == 0


def test_clearing_one_store_leaves_the_others(db):
    blocklist.remember("noon", "CAPTCHA", db_path=db)
    blocklist.remember("ebay", "interstitial", db_path=db)

    assert blocklist.clear("noon", db_path=db) == 1
    assert list(blocklist.blocked(db_path=db)) == ["ebay"]


def test_clearing_everything(db):
    blocklist.remember("noon", "CAPTCHA", db_path=db)
    blocklist.remember("ebay", "interstitial", db_path=db)

    assert blocklist.clear(db_path=db) == 2
    assert blocklist.blocked(db_path=db) == {}


def test_reading_an_empty_list_is_fine(db):
    assert blocklist.blocked(db_path=db) == {}
