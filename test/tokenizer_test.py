import pytest
from stellarisdashboard.parsing.cython_ext import tokenizer

import tokenizer_test_cases
from stellarisdashboard.parsing import tokenizer_re


@pytest.mark.parametrize(
    "tokenizer_impl", [tokenizer_re.tokenizer, tokenizer.tokenizer]
)
@pytest.mark.parametrize("test_data", tokenizer_test_cases.VALID_TOKEN_SEQUENCES)
def test_token_value_stream(tokenizer_impl, test_data):
    _input, expected = test_data
    tokens = list(tokenizer_impl(_input))

    values_only = [val for val, pos in tokens]
    expected_values_only = [val for val, pos in expected]
    assert values_only == expected_values_only

    tokens_with_lines = list(tokenizer_impl(_input, debug=True))
    assert tokens_with_lines == expected
