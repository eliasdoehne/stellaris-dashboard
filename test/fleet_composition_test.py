import pytest

from stellarisdashboard.parsing import timeline


@pytest.mark.parametrize(
    "ship_size, expected",
    [
        ("frigate", "frigate"),
        ("corvette", "corvette"),
        ("destroyer", "destroyer"),
        ("cruiser", "cruiser"),
        ("battleship", "battleship"),
        ("titan", "titan"),
        ("colossus", "colossus"),
        # fallen empire hulls
        ("small_ship_fallen_empire", "destroyer"),
        ("large_ship_fallen_empire", "battleship"),
        ("massive_ship_fallen_empire", "titan"),
        # NPC hulls
        ("ancient_corvette", "corvette"),
        ("ancient_destroyer", "destroyer"),
        ("pirate_corvette", "corvette"),
        ("pirate_destroyer", "destroyer"),
        ("pirate_cruiser", "cruiser"),
        ("marauder_corvette", "corvette"),
        ("marauder_galleon", "battleship"),
        ("crystal_ship_small_blue", "corvette"),
        ("crystal_ship_medium_red_elite", "destroyer"),
        ("crystal_ship_large_green", "cruiser"),
        ("crystal_splitter_ship_large_blue", "cruiser"),
        ("voidworms_medium", "destroyer"),
        ("voidworms_titan", "titan"),
        # BioGenesis and Overlord bioships
        ("mauler_stage_2", "corvette"),
        ("weaver_stage_1", "destroyer"),
        ("harbinger_stage_3", "cruiser"),
        ("stinger_stage_0", "battleship"),
        ("bio_titan", "titan"),
        ("offspring_corvette", "corvette"),
        ("offspring_harbinger_stage_2", "cruiser"),
        # not counted
        ("science", None),
        ("military_station_small", None),
        ("space_whale_3", None),
        ("space_amoeba", None),
        ("crystal_station_large", None),
        ("voidworm_nest", None),
        ("unknown_stage_1", None),
        ("npc_warship_01", None),
        ("mauler_stage_x", None),
        (None, None),
    ],
)
def test_fleet_composition_hull(ship_size, expected):
    assert timeline._fleet_composition_hull(ship_size) == expected
