import json

import pytest

from stellarisdashboard.dashboard_app import overview


def _kpi(style="count", higher_is_better=True):
    return overview.Kpi("k", "K", None, "show_economic_info", "plot", style, higher_is_better)


@pytest.mark.parametrize(
    "value, style, expected",
    [
        (1284, "count", "1,284"),
        (12_940, "count", "12.9K"),
        (412_000, "count", "412K"),
        (4_200_000, "count", "4.2M"),
        (48.84, "income", "+48.8"),
        (-304.8, "income", "−305"),
        (0.01, "income", "0"),
        (3, "rank", "#3"),
    ],
)
def test_format_value(value, style, expected):
    assert overview.format_value(value, style) == expected


def test_delta_of_a_count_is_a_percentage():
    assert overview.format_delta(_kpi(), 200, 250) == dict(text="+25%", arrow="up", tone="good")
    assert overview.format_delta(_kpi(), 200, 195) == dict(text="−2.5%", arrow="down", tone="bad")


def test_delta_of_an_income_is_absolute():
    assert overview.format_delta(_kpi("income"), -20, 30) == dict(text="+50", arrow="up", tone="good")


def test_a_smaller_rank_is_better():
    assert overview.format_delta(_kpi("rank", False), 5, 3) == dict(text="2 places", arrow="up", tone="good")
    assert overview.format_delta(_kpi("rank", False), 3, 4)["tone"] == "bad"


def test_neutral_and_unchanged_deltas():
    assert overview.format_delta(_kpi(higher_is_better=None), 100, 120)["tone"] == "neutral"
    assert overview.format_delta(_kpi(), 100, 100) == dict(text="±0", arrow="", tone="neutral")


def test_sparkline_breaks_at_unknown_values():
    spark = overview.sparkline([(0, 1.0), (360, 2.0), (720, None), (1080, 3.0)], "count")
    assert spark["path"].count("M") == 2
    assert spark["end"][0] == 100
    assert len(json.loads(spark["points"])) == 3
    assert overview.sparkline([(0, 1.0), (360, None)], "count") is None
