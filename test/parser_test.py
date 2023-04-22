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


def test_real_save():
    # Test a real save end to end
    from stellarisdashboard import cli, config
    from pathlib import Path

    output_db = Path(
        f"{config.CONFIG.base_output_path}/db/nexitronawareness_1329922464.db"
    )
    cli.f_parse_saves(save_path="test/saves")
    output_db.unlink(missing_ok=True)
