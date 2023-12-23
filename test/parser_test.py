import pytest
import pathlib
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

@pytest.mark.parametrize(
    "save_dir",
    [path for path in pathlib.Path(__file__).parent.glob(r"saves/*")],
    ids=lambda path: path.stem,
)
def test_real_save(save_dir, tmp_path):
    # Test a real save end to end
    from stellarisdashboard import cli, config
    debug = config.CONFIG.debug_mode
    base_path = config.CONFIG.base_output_path

    config.CONFIG.debug_mode = True
    config.CONFIG.base_output_path = tmp_path
    try:
      cli.f_parse_saves(save_path=save_dir)
    finally:
        config.CONFIG.debug_mode = debug
        config.CONFIG.base_output_path = base_path
