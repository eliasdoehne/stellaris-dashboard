import pytest
import pyximport

pyximport.install()

from stellarisdashboard.parsing import tokenizer_re
from stellarisdashboard.parsing.cython_ext import tokenizer

import tokenizer_test_cases


@pytest.mark.parametrize("test_data", tokenizer_test_cases.VALID_TOKEN_SEQUENCES)
def test_token_value_stream(test_data):
    _input, expected = test_data
    tok_re = list(tokenizer_re.tokenizer(_input))
    tok_cython = list(tokenizer.tokenizer(_input))

    tok_re_values_only = [val for val, pos in tok_re]
    tok_cython_values_only = [val for val, pos in tok_cython]
    expected_values_only = [val for val, pos in expected]
    assert tok_re_values_only == expected_values_only
    assert tok_cython_values_only == expected_values_only

    tok_re_lines = list(tokenizer_re.tokenizer(_input, debug=True))
    tok_cython_lines = list(tokenizer.tokenizer(_input, debug=True))
    assert tok_re_lines == expected
    assert tok_cython_lines == expected
