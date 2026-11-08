import contextlib
import sqlite3

import pytest
from rust_parser import rust_parser

import parser_test_cases


@pytest.mark.parametrize(
    "test_case",
    parser_test_cases.PARSER_TEST_CASES,
)
def test_save_parser_edge_case(test_case):
    data = parser_test_cases.PARSER_TEST_CASES[test_case]
    test_input = data["input"]
    result = rust_parser.parse_save_from_string(test_input)
    assert result == data["expected"]


def test_deep_recursion_depth():
    test_case_depth = 250
    test_input = (
        "".join(f"key_{i} = {{ " for i in range(test_case_depth))
        + "value=1234"
        + ("}" * test_case_depth)
    )
    rust_parser.parse_save_from_string(test_input)


def test_real_save(tmp_path):
    # Test a real save end to end
    from stellarisdashboard import cli, config
    from pathlib import Path
    config.CONFIG.debug_mode = True
    config.CONFIG.base_output_path = tmp_path
    cli.f_parse_saves(save_path="test/saves")
    db_files = list(tmp_path.glob('**/*.db'))
    assert len(db_files) > 0, f"When parsing saves, no output .db files were produced (output folder: {tmp_path})"

    # Guard against save format changes silently leaving parts of the data empty
    totals = dict(districts=0, buildings=0, ships=0, faction_support=0)
    for db_file in db_files:
        with contextlib.closing(sqlite3.connect(db_file)) as con:
            totals["districts"] += con.execute("SELECT COUNT(*) FROM planet_district").fetchone()[0]
            totals["buildings"] += con.execute("SELECT COUNT(*) FROM planet_building").fetchone()[0]
            totals["ships"] += con.execute(
                "SELECT COALESCE(SUM(ship_count_frigate + ship_count_corvette + ship_count_destroyer + ship_count_cruiser"
                " + ship_count_battleship + ship_count_titan + ship_count_colossus), 0) FROM country_data"
            ).fetchone()[0]
            totals["faction_support"] += con.execute("SELECT COUNT(*) FROM popstats_faction WHERE support > 0").fetchone()[0]
    for name, total in totals.items():
        assert total > 0, f"No {name} were extracted from the test saves"


KEY_PATH_TEST_INPUT = """
date="2300.01.01"
galaxy={ name="Milky Way" width=100 }
country={
    0={ name="Humans" flag={ icon=human } budget={ income=5 } }
    1=none
    2={ name="Blorg" budget={ income=3 } }
}
fleet={ name="First" ships={ 1 2 } }
fleet={ name="Second" ships={ 3 } }
"dotted.key"={ a=1 b=2 }
"""


@pytest.mark.parametrize(
    "key_paths,expected",
    [
        (["date"], {"date": "2300.01.01"}),
        (["galaxy.name", "date"], {"date": "2300.01.01", "galaxy": {"name": "Milky Way"}}),
        (
            ["country.*.name"],
            # "none" placeholders cannot be descended into and are returned as they are
            {"country": {0: {"name": "Humans"}, 1: "none", 2: {"name": "Blorg"}}},
        ),
        (
            # explicit keys and wildcards are combined; int keys match integer-like keys
            [["country", "*", "name"], ["country", 0, "flag"]],
            {"country": {0: {"name": "Humans", "flag": {"icon": "human"}}, 1: "none", 2: {"name": "Blorg"}}},
        ),
        (
            # a shorter path selects the full value
            ["country.0.name", "country.0"],
            {"country": {0: {"name": "Humans", "flag": {"icon": "human"}, "budget": {"income": 5}}}},
        ),
        # lists from repeated keys are traversed element by element
        (["fleet.name"], {"fleet": [{"name": "First"}, {"name": "Second"}]}),
        ([["dotted.key", "b"]], {"dotted.key": {"b": 2}}),
        (["does.not.exist", "galaxy.missing"], {"galaxy": {}}),
        ([], {}),
    ],
)
def test_key_paths(key_paths, expected):
    result = rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT, key_paths=key_paths)
    assert result == expected


def test_key_paths_none_or_empty_path_returns_everything():
    full = rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT)
    assert rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT, key_paths=None) == full
    assert rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT, key_paths=[[]]) == full
    assert rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT, key_paths=["date", []]) == full


@pytest.mark.parametrize(
    "key_paths,error",
    [
        ("date", TypeError),  # a single path must be wrapped in a list
        ([1], TypeError),
        ([["country", 1.5]], TypeError),
        (["country..name"], ValueError),
    ],
)
def test_key_paths_invalid(key_paths, error):
    with pytest.raises(error):
        rust_parser.parse_save_from_string(KEY_PATH_TEST_INPUT, key_paths=key_paths)


def test_key_paths_real_save():
    from pathlib import Path
    from stellarisdashboard.parsing import save_parser

    save_files = sorted(Path("test/saves").glob("**/*.sav"))
    if not save_files:
        pytest.skip("test saves are not available (git submodule test/saves)")
    save_file = save_files[0]
    full = save_parser.parse_save(save_file)
    subset = save_parser.parse_save(save_file, key_paths=["date", "country", "fleet.*.name"])
    assert subset.keys() == {"date", "country", "fleet"}
    assert subset["date"] == full["date"]
    assert subset["country"] == full["country"]
    assert subset["fleet"] == {
        fleet_id: {k: v for k, v in fleet.items() if k == "name"} if isinstance(fleet, dict) else fleet
        for fleet_id, fleet in full["fleet"].items()
    }
