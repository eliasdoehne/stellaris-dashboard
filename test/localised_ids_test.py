import json
import pathlib

import pytest

from stellarisdashboard import game_info


def _clear_caches():
    game_info.lookup_id_name.cache_clear()
    game_info.lookup_faction_type.cache_clear()
    game_info.render_name_or_prettify.cache_clear()
    game_info.render_name.cache_clear()


@pytest.fixture
def fixture_renderer():
    """Use the localisation test files as the global renderer for the duration of a test."""
    files = list(
        (pathlib.Path(__file__).parent / "localization_test_files/english").glob(
            "*.yml"
        )
    )
    renderer = game_info.NameRenderer(files)
    renderer.load_name_mapping()
    game_info.global_renderer = renderer
    _clear_caches()
    yield renderer
    game_info.global_renderer = None
    _clear_caches()


def test_building_found_in_localisation(fixture_renderer):
    assert (
        game_info.lookup_id_name("building_research_lab_1", "building")
        == "Research Lab"
    )


def test_building_falls_back_to_prettified_id(fixture_renderer):
    assert (
        game_info.lookup_id_name("building_unknown_thing_2", "building")
        == "Unknown Thing 2"
    )


def test_district_found_in_localisation(fixture_renderer):
    assert game_info.lookup_id_name("district_city", "district") == "City District"


def test_district_falls_back_to_prettified_id(fixture_renderer):
    assert game_info.lookup_id_name("district_unknown_hab", "district") == "Unknown Hab"


@pytest.mark.parametrize(
    "faction_name, expected",
    [
        ({"key": "secret_society_test_faction"}, "Order of the Test"),
        ("secret_society_test_faction", "Order of the Test"),
        ({"key": "secret_society_missing_faction"}, "Secret Society Missing Faction"),
        ("secret_society_missing_faction", "Secret Society Missing Faction"),
        # free text and literal names are left to the regular rendering
        ({"key": "Unnamed faction"}, "Unnamed faction"),
        ({"key": "Some_Literal_Name", "literal": "yes"}, "Some_Literal_Name"),
    ],
)
def test_faction_name(fixture_renderer, faction_name, expected):
    assert game_info.render_name_or_prettify(json.dumps(faction_name)) == expected


@pytest.mark.parametrize(
    "faction_type, expected",
    [
        ("imperialist", "Imperialist Faction"),
        # secret societies append their ethic to the type, but not to the loc key
        ("the_masquerade_xenophile", "The Masquerade"),
    ],
)
def test_faction_type(fixture_renderer, faction_type, expected):
    assert game_info.lookup_faction_type(faction_type) == expected
