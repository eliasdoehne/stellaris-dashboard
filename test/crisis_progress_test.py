from stellarisdashboard.dashboard_app.event_registry import REGISTRY
from stellarisdashboard.datamodel import HistoricalEventType, HistoricalEventScope, render_crisis_level
from stellarisdashboard.parsing.timeline import new_crisis_levels, reached_crisis_levels


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
    assert reached_crisis_levels({"crisis_progression": {"path": "hyperthermia_path"}}) == []
    for country in ({}, {"crisis_progression": "none"}, "none", None):
        assert reached_crisis_levels(country) is None


def test_new_crisis_levels():
    levels = ["l1", "l2", "l3"]
    assert new_crisis_levels(None, levels) == ([], 3)  # first sighting: baseline only
    assert new_crisis_levels(2, levels) == (["l3"], 3)
    assert new_crisis_levels(1, levels) == (["l2", "l3"], 3)  # skipped between saves
    assert new_crisis_levels(3, levels) == ([], 3)
    assert new_crisis_levels(None, None) == ([], None)
    assert new_crisis_levels(2, None) == ([], 2)
    assert new_crisis_levels(None, []) == ([], 0)  # path block without levels
    assert new_crisis_levels(0, ["l1"]) == (["l1"], 1)  # level 1 unlocked later


def test_render_crisis_level():
    assert "§" not in render_crisis_level("crisis_level_2")
    assert render_crisis_level("crisis_level_2").endswith("-level threat to the galaxy")
    assert render_crisis_level("crisis_defender_level_2").endswith("-rank Defender of the Galaxy")


def test_crisis_level_up_is_a_country_event_with_a_ledger_entry():
    assert HistoricalEventType.crisis_level_up.scope == HistoricalEventScope.country
    assert REGISTRY["crisis_level_up"].sentence
