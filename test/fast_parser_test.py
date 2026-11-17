"""The prototype parser (rust_parser.parse_save_from_string_fast) must agree with the current one."""

import pytest
from rust_parser import rust_parser

import parser_test_cases

MODES = ["direct", "tape", "value"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cache", [True, False])
@pytest.mark.parametrize("test_case", parser_test_cases.PARSER_TEST_CASES)
def test_fast_parser_edge_case(test_case, mode, cache):
    data = parser_test_cases.PARSER_TEST_CASES[test_case]
    result = rust_parser.parse_save_from_string_fast(data["input"], mode=mode, cache=cache)
    assert result == data["expected"]
    assert result == rust_parser.parse_save_from_string(data["input"])


@pytest.mark.parametrize("mode", MODES)
def test_fast_parser_quirks(mode):
    text = (
        'a=1 a=2 b={ 1 2 } b={ 3 } b={ 4 } c={ x= } d={} e= f=rgb { 1 2 3 } 7=x "8"=y'
        ' g=2200.01.01 h=-1.50 i="esc \\" \\\\" j={ k= l=m } n=nano o=nan2 p=18446744073709551615'
    )
    result = rust_parser.parse_save_from_string_fast(text, mode=mode)
    assert result == rust_parser.parse_save_from_string(text)
    assert result == {
        "a": [1, 2],
        "b": [[1, 2], [3], [4]],
        "c": {},
        "d": [],
        "f": ("rgb", 1.0, 2.0, 3.0),
        7: "x",
        8: "y",
        "g": "2200.01.01",
        "h": -1.5,
        "i": 'esc \\" \\\\',
        "j": {"l": "m"},
        "n": "nano",
        "o": "nan2",
        "p": 18446744073709551615.0,
    }


@pytest.mark.parametrize("text", ["", "a", "a={", "a=1 }", 'a="x', "a={ b c=d }", "a=1.2.3x"])
def test_fast_parser_rejects_what_the_current_parser_rejects(text):
    with pytest.raises(ValueError):
        rust_parser.parse_save_from_string(text)
    with pytest.raises(ValueError):
        rust_parser.parse_save_from_string_fast(text)


@pytest.mark.parametrize("mode", MODES)
def test_fast_parser_projection(mode):
    text = 'keep={ a=1 b={ c="}" } } skip={ y={ "{" } x=1 } date="2200.01.01" skip2=rgb { 1 2 3 } keep=2 skip3= last=1'
    full = rust_parser.parse_save_from_string(text)
    top = rust_parser.parse_save_from_string_fast(text, ["keep", "date", "last", "missing"], mode=mode)
    assert top == {k: full[k] for k in ("keep", "date", "last")}

    text = "country={ 0={ name=A big={ 1 2 3 } flag={ x=1 y=2 } } 1={ big={ } name=B } } other=1"
    nested = rust_parser.parse_save_from_string_fast(
        text, {"country": {"*": {"name": None, "flag": {"y": None}}}}, mode=mode
    )
    assert nested == {"country": {0: {"name": "A", "flag": {"y": 2}}, 1: {"name": "B"}}}
