import collections

from stellarisdashboard.dashboard_app import ledger_timeline
from stellarisdashboard.dashboard_app.event_registry import Category


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
