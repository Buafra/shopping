"""Search history and price tracking.

The point of keeping history is answering "has it moved" a day later, so the
tests are mostly about the diff being right — including the cases where it
must refuse to claim movement it cannot actually see.
"""

import pytest

from app import history
from app.models import Market, Offer, SearchResponse


@pytest.fixture
def db(tmp_path):
    return tmp_path / "history.db"


def offer(price, url="https://a.ae/dp/X1", store="amazon_ae", title="Gigabyte RTX 4070 OC"):
    return Offer(
        store=store, store_label=store.replace("_", ".").title(),
        market=Market.LOCAL, country="AE", title=title, url=url,
        price=price, currency="AED", price_aed=price, landed_aed=price, score=80.0,
    )


def response(offers, query="rtx 4070"):
    return SearchResponse(query=query, offers=offers, stores=[])


# ---- recording ------------------------------------------------------------

def test_a_search_is_recorded_and_listed(db):
    history.record(response([offer(2150)]), db_path=db)
    records = history.list_searches(db_path=db)

    assert len(records) == 1
    assert records[0].query == "rtx 4070"
    assert records[0].checks == 1
    assert records[0].latest_best_aed == 2150


def test_rerunning_a_search_adds_a_check_not_a_row(db):
    """The same query re-run is the same tracked search, one timeline."""
    history.record(response([offer(2150)]), db_path=db)
    history.record(response([offer(1990)]), db_path=db)

    records = history.list_searches(db_path=db)
    assert len(records) == 1
    assert records[0].checks == 2
    assert records[0].latest_best_aed == 1990
    assert records[0].previous_best_aed == 2150
    assert records[0].best_delta == -160


def test_different_markets_are_tracked_separately(db):
    history.record(response([offer(2150)]), market="all", db_path=db)
    history.record(response([offer(2150)]), market="local", db_path=db)
    assert len(history.list_searches(db_path=db)) == 2


def test_an_empty_result_is_still_recorded(db):
    """"Nothing found today" is an observation; dropping it breaks the timeline."""
    history.record(response([]), db_path=db)
    records = history.list_searches(db_path=db)
    assert records[0].checks == 1
    assert records[0].latest_best_aed is None


# ---- diffing --------------------------------------------------------------

def test_first_run_claims_no_movement(db):
    search_id = history.record(response([offer(2150)]), db_path=db)
    changes = history.compare(search_id, db_path=db)

    assert changes.is_first_run
    assert changes.changed == []
    assert "First run" in changes.summary()


def test_price_drop_is_detected_with_direction_and_percentage(db):
    search_id = history.record(response([offer(2150)]), db_path=db)
    history.record(response([offer(1990)]), db_path=db)

    changes = history.compare(search_id, db_path=db)
    moved = changes.changed[0]

    assert moved.direction == "down"
    assert moved.delta == -160
    assert moved.pct == pytest.approx(-7.4, abs=0.1)
    assert "1 cheaper" in changes.summary()


def test_price_rise_is_detected(db):
    search_id = history.record(response([offer(2150)]), db_path=db)
    history.record(response([offer(2400)]), db_path=db)

    moved = history.compare(search_id, db_path=db).changed[0]
    assert moved.direction == "up"
    assert moved.delta == 250


def test_unchanged_prices_are_counted_not_listed(db):
    search_id = history.record(response([offer(2150)]), db_path=db)
    history.record(response([offer(2150)]), db_path=db)

    changes = history.compare(search_id, db_path=db)
    assert changes.changed == []
    assert changes.unchanged == 1
    assert "No price movement" in changes.summary()


def test_new_and_vanished_listings_are_reported(db):
    search_id = history.record(
        response([offer(2150, "https://a.ae/dp/A"), offer(2400, "https://a.ae/dp/B")]),
        db_path=db)
    history.record(
        response([offer(2150, "https://a.ae/dp/A"), offer(1900, "https://a.ae/dp/C")]),
        db_path=db)

    changes = history.compare(search_id, db_path=db)
    assert [o.url for o in changes.appeared] == ["https://a.ae/dp/C"]
    assert [o.url for o in changes.disappeared] == ["https://a.ae/dp/B"]


def test_offers_are_matched_across_runs_despite_tracking_parameters(db):
    """Listing URLs pick up campaign parameters between fetches; matching on
    the raw URL would report every listing as gone and replaced."""
    search_id = history.record(
        response([offer(2150, "https://a.ae/dp/A?ref=sr_1_1")]), db_path=db)
    history.record(
        response([offer(1990, "https://a.ae/dp/A?ref=sr_1_4&tag=x")]), db_path=db)

    changes = history.compare(search_id, db_path=db)
    assert changes.appeared == []
    assert changes.disappeared == []
    assert changes.changed[0].delta == -160


def test_cheapest_drop_picks_the_biggest_fall(db):
    search_id = history.record(
        response([offer(2150, "https://a.ae/dp/A"), offer(3000, "https://a.ae/dp/B")]),
        db_path=db)
    history.record(
        response([offer(2100, "https://a.ae/dp/A"), offer(2400, "https://a.ae/dp/B")]),
        db_path=db)

    assert history.compare(search_id, db_path=db).cheapest_drop.delta == -600


# ---- housekeeping ---------------------------------------------------------

def test_forgetting_a_search_removes_its_history(db):
    search_id = history.record(response([offer(2150)]), db_path=db)
    history.record(response([offer(1990)]), db_path=db)

    assert history.delete(search_id, db_path=db) is True
    assert history.list_searches(db_path=db) == []
    assert history.compare(search_id, db_path=db) is None


def test_forgetting_an_unknown_search_is_reported(db):
    assert history.delete(999, db_path=db) is False


def test_compare_of_an_unknown_search_returns_none(db):
    assert history.compare(999, db_path=db) is None


def test_history_survives_reopening_the_database(db):
    history.record(response([offer(2150)]), db_path=db)
    # A fresh connection, as a later CLI invocation would make.
    assert history.list_searches(db_path=db)[0].latest_best_aed == 2150
