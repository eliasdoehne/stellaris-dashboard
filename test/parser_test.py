import pytest

from stellarisdashboard.parsing import save_parser
import parser_test_cases


@pytest.mark.parametrize(
    "test_case",
    parser_test_cases.PARSER_TEST_CASES,
)
def test_save_parser_edge_case(test_case):
    data = parser_test_cases.PARSER_TEST_CASES[test_case]
    test_input = data["input"]
    parser = save_parser.SaveFileParser()
    result = parser.parse_from_string(test_input)
    assert result == data["expected"]


def test_recursion_depth():
    parser = save_parser.SaveFileParser()
    test_case_depth = 6
    test_input = (
        "".join(f"key_{i} = {{ " for i in range(test_case_depth))
        + "value=1234"
        + ("}" * (test_case_depth))
    )
    # test where the parser's max depth is less than the depth of the object:
    parser.max_nesting_depth = 5
    result = parser.parse_from_string(test_input)
    assert parser._current_nesting_depth == 0
    expected = {"key_0": {"key_1": {"key_2": {"key_3": {"key_4": {"key_5": []}}}}}}
    assert result == expected

    parser.max_nesting_depth = 700
    result = parser.parse_from_string(test_input)
    assert parser._current_nesting_depth == 0
    expected = {
        "key_0": {"key_1": {"key_2": {"key_3": {"key_4": {"key_5": {"value": 1234}}}}}}
    }
    assert result == expected


def test_deep_recursion_depth():
    parser = save_parser.SaveFileParser()
    test_case_depth = 10000
    test_input = (
        "".join(f"key_{i} = {{ " for i in range(test_case_depth))
        + "value=1234"
        + ("}" * test_case_depth)
    )
    result = parser.parse_from_string(test_input)
    assert parser._current_nesting_depth == 0
    # Check that the result is nested as deep as the parser's max_nesting...
    inner_value = result
    for i in range(parser.max_nesting_depth + 1):
        inner_value = inner_value[f"key_{i}"]
    # and that the last value is an empty list:
    assert inner_value == []


def test_real_save():
    # Test a real save end to end
    from stellarisdashboard import cli, config
    from pathlib import Path
    output_db = Path(f"{config.CONFIG.base_output_path}/db/nexitronawareness_1329922464.db")
    cli.f_parse_saves(save_path="test/saves")
    output_db.unlink(missing_ok=True)
