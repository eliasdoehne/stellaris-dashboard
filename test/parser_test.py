import pytest

from stellarisdashboard.parsing import save_parser
import parser_test_data


@pytest.mark.parametrize(
    "test_case", parser_test_data.PARSER_TEST_CASES,
)
def test_save_parser_edge_case(test_case):
    data = parser_test_data.PARSER_TEST_CASES[test_case]
    test_input = data["input"]
    parser = save_parser.SaveFileParser()
    result = parser.parse_from_string(test_input)
    assert result == data["expected"]
