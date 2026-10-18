from stellarisdashboard.datamodel import StorylineState
from stellarisdashboard.game_info import parse_top_level_fields
from stellarisdashboard.parsing.timeline import (
    FIRST_CONTACT_STORYLINE_STATES,
    astral_rift_state,
    dig_site_state,
)


def test_dig_site_state_completed_when_all_chapters_done():
    assert dig_site_state(3, 3, False) == StorylineState.completed
    assert dig_site_state(4, 3, True) == StorylineState.completed


def test_dig_site_state_active_or_paused_while_unfinished():
    assert dig_site_state(1, 3, True) == StorylineState.active
    assert dig_site_state(1, 3, False) == StorylineState.paused


def test_dig_site_state_without_stage_count_is_never_completed():
    assert dig_site_state(5, None, True) == StorylineState.active
    assert dig_site_state(5, None, False) == StorylineState.paused


def test_astral_rift_state():
    assert astral_rift_state("in_progress") == StorylineState.active
    assert astral_rift_state("finished") == StorylineState.completed
    assert astral_rift_state("waiting_for_explorer") == StorylineState.paused
    assert astral_rift_state(None) == StorylineState.paused


def test_first_contact_storyline_states():
    assert FIRST_CONTACT_STORYLINE_STATES == {
        "in_progress": StorylineState.active,
        "finished": StorylineState.completed,
        "aborted": StorylineState.abandoned,
    }


SITE_SCRIPT = """
# a comment line
site_one = {
    stages = 3 # trailing comment
    stage = { difficulty = 5 }
    icon = "GFX_one"
}
site_two = {
    stage = {
        stages = 9
    }
    stages = 4
}
site_three = { icon = x }
"""


def test_parse_top_level_fields_reads_own_fields_only():
    stages = parse_top_level_fields(SITE_SCRIPT.splitlines(), "stages")
    assert stages == {"site_one": "3", "site_two": "4"}


def test_parse_top_level_fields_ignores_nested_and_strips_quotes():
    lines = SITE_SCRIPT.splitlines()
    assert parse_top_level_fields(lines, "difficulty") == {}
    assert parse_top_level_fields(lines, "icon") == {"site_one": "GFX_one"}
