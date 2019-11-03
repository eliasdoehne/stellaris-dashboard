import pytest
import pyximport

pyximport.install()

from stellarisdashboard import token_value_stream_re
from stellarisdashboard.cython_ext import token_value_stream

import token_value_stream_test_data


@pytest.mark.parametrize(
    "test_data", token_value_stream_test_data.VALID_TOKEN_SEQUENCE_TEST_DATA
)
def test_token_value_stream(test_data):
    input, expected = test_data
    tok_re = list(token_value_stream_re.token_value_stream(input))
    tok_cython = list(token_value_stream.token_value_stream(input))

    tok_re_values_only = [val for val, pos in tok_re]
    tok_cython_values_only = [val for val, pos in tok_cython]
    expected_values_only = [val for val, pos in expected]
    assert tok_re_values_only == expected_values_only
    assert tok_cython_values_only == expected_values_only

    tok_re_lines = list(token_value_stream_re.token_value_stream(input, debug=True))
    tok_cython_lines = list(token_value_stream.token_value_stream(input, debug=True))
    assert tok_re_lines == expected
    assert tok_cython_lines == expected
