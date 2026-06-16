import pytest

from stellarisdashboard.parsing.timeline import _extract_id


@pytest.mark.parametrize(
    "value,expected",
    [
        # Bare integer references (the historical save format).
        (7, 7),
        (0, 0),
        (-1, -1),
        (7.0, 7),
        # Newer save format: references wrapped in a dict with a "reference" key.
        ({"reference": 5}, 5),
        ({"reference": 12, "id": 9, "type": 4}, 12),
        # Anything unexpected falls back to the default rather than crashing.
        (None, -1),
        ("none", -1),
        ({}, -1),
        ({"id": 9}, -1),
    ],
)
def test_extract_id(value, expected):
    assert _extract_id(value) == expected


def test_extract_id_custom_default():
    assert _extract_id(None, default=0) == 0
    assert _extract_id({"id": 9}, default=0) == 0
