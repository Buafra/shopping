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


# ---- stores that answer, but never with anything readable ----------------
#
# Jumbo and Microless spent ~23 seconds each on every search, over many runs,
# and never produced a single listing. A block is the store's decision; this is
# a guess about the store, so it rests for less time and clears on success.

def test_a_store_rests_only_after_a_run_of_empty_results():
    from app import blocklist

    for _ in range(blocklist.REST_AFTER_FAILURES - 1):
        blocklist.record_outcome("microless", False, "no cards")
        assert "microless" not in blocklist.resting()

    blocklist.record_outcome("microless", False, "no cards")
    resting = blocklist.resting()
    assert "microless" in resting
    assert "3 empty runs in a row" in resting["microless"]


def test_one_good_run_clears_the_streak():
    """A store having an off day, or a query with genuinely no matches, must
    not accumulate towards being rested."""
    from app import blocklist

    blocklist.record_outcome("emax", False, "no cards")
    blocklist.record_outcome("emax", False, "no cards")
    blocklist.record_outcome("emax", True)
    blocklist.record_outcome("emax", False, "no cards")

    assert "emax" not in blocklist.resting()


def test_resting_expires_sooner_than_a_block():
    from app import blocklist

    assert blocklist.REST_HOURS < 24.0

    for _ in range(blocklist.REST_AFTER_FAILURES):
        blocklist.record_outcome("jumbo_ae", False, "no cards")

    assert "jumbo_ae" in blocklist.resting()
    # Long past the rest window, it is tried again.
    assert "jumbo_ae" not in blocklist.resting(ttl_hours=0)


def test_unblocking_a_store_also_clears_its_failure_streak():
    from app import blocklist

    for _ in range(blocklist.REST_AFTER_FAILURES):
        blocklist.record_outcome("gear_up", False, "no cards")
    assert "gear_up" in blocklist.resting()

    blocklist.clear("gear_up")
    assert "gear_up" not in blocklist.resting()


def test_a_refusal_and_an_empty_page_are_counted_separately():
    from app import blocklist

    assert "blocked" in blocklist.BLOCKING_KINDS
    assert "blocked" not in blocklist.UNPRODUCTIVE_KINDS
    assert blocklist.UNPRODUCTIVE_KINDS == {"parse", "no_results"}
