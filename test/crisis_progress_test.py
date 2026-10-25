from stellarisdashboard.dashboard_app.event_registry import REGISTRY
from stellarisdashboard.datamodel import HistoricalEventType, HistoricalEventScope, render_crisis_level
from stellarisdashboard.parsing.timeline import reached_crisis_levels


def test_reached_crisis_levels():
    one = {"crisis_progression": {"path": "nemesis_path", "level": "crisis_level_1"}}
    two = {
        "crisis_progression": {
            "path": "behemoth_path",
            "level": ["crisis_behemoth_level_1", "crisis_behemoth_level_2"],
        }
    }
    assert reached_crisis_levels(one) == ["crisis_level_1"]
    assert reached_crisis_levels(two) == ["crisis_behemoth_level_1", "crisis_behemoth_level_2"]
    for country in ({}, {"crisis_progression": "none"}, {"crisis_progression": {"path": "x"}}, "none", None):
        assert reached_crisis_levels(country) == []


def test_render_crisis_level_strips_colour_codes():
    assert "§" not in render_crisis_level("crisis_level_2")
    assert render_crisis_level("crisis_unknown_level_9")


def test_crisis_level_up_is_a_country_event_with_a_ledger_entry():
    assert HistoricalEventType.crisis_level_up.scope == HistoricalEventScope.country
    assert REGISTRY["crisis_level_up"].sentence
