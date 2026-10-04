import pytest

from stellarisdashboard.parsing import save_format

# The gamestate fragments below mirror the structures found in real saves of the
# respective game versions, reduced to the fields the helpers read.

GAMESTATE_3X = {
    "planets": {
        "planet": {
            3: {
                "planet_class": "pc_continental",
                "district": ["district_city", "district_mining", "district_mining"],
                "buildings": [10, 11],
            },
        }
    },
    "buildings": {10: {"type": "building_capital"}, 11: {"type": "building_foundry_1"}},
    "ship_design": {50: {"ship_size": "corvette"}},
}

GAMESTATE_40 = {
    "planets": {
        "planet": {
            3: {"planet_class": "pc_continental", "districts": [7, 8], "stability": 50.0},
        }
    },
    "districts": {
        7: {"type": "district_city", "level": 3, "zones": [20, 21]},
        8: {"type": "district_mining", "level": 2, "zones": [4294967295]},
    },
    "zones": {
        20: {"type": "zone_government", "buildings": [30]},
        21: {"type": "zone_research_unity", "buildings": [31, 32]},
    },
    "buildings": {
        30: {"type": "building_capital"},
        31: {"type": "building_research_lab_1"},
        32: {"type": "building_research_lab_1"},
    },
    "ship_design": {
        60: {"growth_stages": [{"ship_size": "corvette"}]},
        61: {"growth_stages": {"ship_size": "science"}},
        62: {"growth_stages": [{"ship_size": "bio_small"}, {"ship_size": "bio_large"}]},
    },
}

GAMESTATE_44 = {
    "planets": {
        "planet": {
            0: {"planet_class": "pc_g_star"},
            3: {"planet_class": "pc_continental", "colony": 0, "timed_modifier": []},
        }
    },
    "colony": {
        0: {
            "carrier": {"type": "planet", "reference": 3},
            "districts": [7],
            "buildings_cache": [30],
            "stability": 61.5,
            "governor": 4,
        },
        1: {"carrier": {"type": "ship", "reference": 12}},
    },
    "districts": {7: {"type": "district_city", "level": 1, "zones": [20]}},
    "zones": {20: {"type": "zone_government", "buildings": [30]}},
    "buildings": {30: {"type": "building_capital"}},
}


def test_resolve_colony_to_planet_id():
    # before 4.4, these fields already hold planet IDs
    assert save_format.resolve_colony_to_planet_id(GAMESTATE_40, 3) == 3
    # since 4.4, they hold colony IDs, which are resolved through the colony's carrier
    assert save_format.resolve_colony_to_planet_id(GAMESTATE_44, 0) == 3
    # colonies carried by ships (nomad arkships) and unknown colonies have no planet
    assert save_format.resolve_colony_to_planet_id(GAMESTATE_44, 1) is None
    assert save_format.resolve_colony_to_planet_id(GAMESTATE_44, 99) is None


def test_get_planet():
    assert save_format.get_planet(GAMESTATE_40, 3)["stability"] == 50.0
    assert save_format.get_planet(GAMESTATE_40, 99) is None

    planet = save_format.get_planet(GAMESTATE_44, 3)
    # colony fields are visible on the merged view, planet fields win on conflicts
    assert planet["stability"] == 61.5
    assert planet["governor"] == 4
    assert planet["planet_class"] == "pc_continental"
    assert planet["timed_modifier"] == []
    # planets without a colony are returned unchanged
    assert save_format.get_planet(GAMESTATE_44, 0) == {"planet_class": "pc_g_star"}


@pytest.mark.parametrize(
    "gamestate,expected",
    [
        (GAMESTATE_3X, ["district_city", "district_mining", "district_mining"]),
        (
            GAMESTATE_40,
            ["district_city"] * 3 + ["district_mining"] * 2,
        ),
        (GAMESTATE_44, ["district_city"]),
    ],
)
def test_get_district_types(gamestate, expected):
    planet = save_format.get_planet(gamestate, 3)
    assert save_format.get_district_types(gamestate, planet) == expected


@pytest.mark.parametrize(
    "gamestate,expected",
    [
        (GAMESTATE_3X, ["building_capital", "building_foundry_1"]),
        (
            GAMESTATE_40,
            ["building_capital", "building_research_lab_1", "building_research_lab_1"],
        ),
        (GAMESTATE_44, ["building_capital"]),
    ],
)
def test_get_building_types(gamestate, expected):
    planet = save_format.get_planet(gamestate, 3)
    assert save_format.get_building_types(gamestate, planet) == expected


@pytest.mark.parametrize(
    "gamestate,ship,expected",
    [
        (GAMESTATE_3X, {"ship_design": 50}, "corvette"),
        (GAMESTATE_3X, {"ship_design": 99}, None),
        (GAMESTATE_40, {"ship_design_implementation": {"design": 60, "growth_stage": 0}}, "corvette"),
        # a single growth stage is parsed as a dict rather than a list
        (GAMESTATE_40, {"ship_design_implementation": {"design": 61, "growth_stage": 0}}, "science"),
        (GAMESTATE_40, {"ship_design_implementation": {"design": 62, "growth_stage": 1}}, "bio_large"),
        # out-of-range stages fall back to the last stage
        (GAMESTATE_40, {"ship_design_implementation": {"design": 62, "growth_stage": 5}}, "bio_large"),
        (GAMESTATE_40, {"ship_design_implementation": {"design": 99, "growth_stage": 0}}, None),
    ],
)
def test_get_ship_size(gamestate, ship, expected):
    assert save_format.get_ship_size(gamestate, ship) == expected


def test_get_budget_value():
    values = {"energy": 5.0, "sr_living_metal": 2.5, "nanites": 1.0}
    assert save_format.get_budget_value(values, "living_metal") == 2.5
    assert save_format.get_budget_value(values, "nanites") == 1.0
    assert save_format.get_budget_value(values, "zro") == 0.0
    # unprefixed names are still read for older saves
    assert save_format.get_budget_value({"zro": 3.0}, "zro") == 3.0


@pytest.mark.parametrize(
    "pop_group,expected",
    [
        # before 4.5: a single ethic in the key
        (
            {"size": 182, "key": {"category": "specialist", "ethos": {"ethic": "ethic_materialist"}}},
            [("ethic_materialist", 182)],
        ),
        ({"size": 50, "key": {"category": "complex_drone"}}, [(save_format.NO_ETHOS, 50)]),
        # since 4.5: pop counts per ethic
        (
            {
                "size": 2000,
                "key": {"category": "worker"},
                "ethos": {"ethics": ["ethic_egalitarian", "ethic_materialist"], "pops": [1200, 800]},
            },
            [("ethic_egalitarian", 1200), ("ethic_materialist", 800)],
        ),
        (
            {"size": 100, "key": {"category": "worker"}, "ethos": {"ethics": "ethic_xenophobe", "pops": 60}},
            [("ethic_xenophobe", 60), (save_format.NO_ETHOS, 40)],
        ),
        ({"size": 30, "key": {"category": "simple_drone"}}, [(save_format.NO_ETHOS, 30)]),
    ],
)
def test_get_pop_group_ethics(pop_group, expected):
    assert save_format.get_pop_group_ethics(pop_group) == expected


@pytest.mark.parametrize(
    "pop_group,expected",
    [
        # before 4.5: at most one faction in the key
        ({"size": 100, "key": {"pop_faction": 33554455}}, [(33554455, 100)]),
        ({"size": 100, "key": {"category": "worker"}}, [(None, 100)]),
        # since 4.5: no faction data yet (an empty "factions={ }" is parsed as a list)
        ({"size": 100, "key": {"category": "worker"}, "factions": []}, [(None, 100)]),
        # since 4.5: the accepted layouts for pops spread over several factions
        (
            {"size": 100, "key": {}, "factions": {"factions": [5, 6], "pops": [60, 30]}},
            [(5, 60), (6, 30), (None, 10)],
        ),
        ({"size": 100, "key": {}, "factions": {5: 70, 6: 30}}, [(5, 70), (6, 30)]),
        # implausible data is ignored rather than producing wrong numbers
        ({"size": 100, "key": {}, "factions": {5: 70, 6: 70}}, [(None, 100)]),
        ({"size": 100, "key": {}, "factions": {-1: 0.5}}, [(None, 100)]),
    ],
)
def test_get_pop_group_factions(pop_group, expected):
    assert save_format.get_pop_group_factions(pop_group) == expected
