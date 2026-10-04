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
    config.CONFIG.read_all_countries = True
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
                "SELECT COALESCE(SUM(ship_count_corvette + ship_count_destroyer + ship_count_cruiser"
                " + ship_count_battleship + ship_count_titan + ship_count_colossus), 0) FROM country_data"
            ).fetchone()[0]
            totals["faction_support"] += con.execute("SELECT COUNT(*) FROM popstats_faction WHERE support > 0").fetchone()[0]
    for name, total in totals.items():
        assert total > 0, f"No {name} were extracted from the test saves"
