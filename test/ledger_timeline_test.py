import collections

import pytest

from stellarisdashboard.dashboard_app import flask_app, ledger_timeline
from stellarisdashboard.dashboard_app.event_registry import Category


@pytest.mark.parametrize(
    "value, expected",
    [
        ("12", 12),
        ("-12", -12),
        ("abc", None),
        (str(2 ** 63 - 1), 2 ** 63 - 1),
        (str(2 ** 63), None),
        (str(-(2 ** 63)), -(2 ** 63)),
        (str(-(2 ** 63) - 1), None),
    ],
)
def test_int_arg_stays_within_sqlite_integers(value, expected):
    with flask_app.test_request_context("/", query_string={"id": value}):
        assert ledger_timeline._int_arg("id") == expected
        assert ledger_timeline._int_arg("missing") is None


@pytest.mark.parametrize(
    "year, expected",
    [("2250", 2250), (str(2 ** 31), None), (str(2 ** 63 - 1), None), ("9" * 23, None)],
)
def test_year_arg_stays_small_enough_to_convert_to_days(year, expected):
    with flask_app.test_request_context("/", query_string={"year": year}):
        assert ledger_timeline.LedgerParams.from_request("game").year == expected


def test_events_from_before_the_game_count_as_its_first_year():
    assert ledger_timeline.year_of(-400) == 2200
    assert ledger_timeline.year_of(0) == 2200
    assert ledger_timeline.year_of(359) == 2200
    assert ledger_timeline.year_of(360) == 2201


def test_year_counts_cover_the_selected_categories():
    counts = collections.Counter({(2200, Category.WARFARE): 2, (2200, Category.SCIENCE): 1, (2201, Category.SCIENCE): 4})
    rows = ledger_timeline.Rows(counts=counts)
    assert rows.year_counts({Category.WARFARE}) == {2200: 2}
    assert rows.year_counts({Category.WARFARE, Category.SCIENCE}) == {2200: 3, 2201: 4}
