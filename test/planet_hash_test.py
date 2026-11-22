"""The change-detection hashes of the planets' districts, buildings, deposits and modifiers.

The values are stored in the database, so they must stay what they were: empty collections
skip the digest computation but get the same value.
"""

import collections
import types

from stellarisdashboard.parsing import timeline


def test_empty_digest_matches_digest_of_empty_collections():
    assert timeline._EMPTY_DIGEST == timeline._stable_digest(())
    assert timeline._EMPTY_DIGEST == timeline._stable_digest(
        collections.Counter().items()
    )
    assert timeline._EMPTY_DIGEST == timeline._stable_digest({}.items())


def test_check_and_update_hash_values():
    processor = timeline.PlanetProcessor()
    planet = types.SimpleNamespace(districts_hash=None, modifiers_hash=None)
    counter = collections.Counter(
        ["district_city", "district_city", "district_generator"]
    )

    assert processor._check_and_update_hash(planet, counter, "districts_hash")
    assert planet.districts_hash == timeline._stable_digest(counter.items())
    assert not processor._check_and_update_hash(planet, counter, "districts_hash")

    empty = collections.Counter()
    assert processor._check_and_update_hash(planet, empty, "districts_hash")
    assert planet.districts_hash == timeline._stable_digest(empty.items())
    assert not processor._check_and_update_hash(planet, {}, "districts_hash")

    modifiers = {"m_a": 50, "m_b": None}
    assert processor._check_and_update_hash(planet, modifiers, "modifiers_hash")
    assert planet.modifiers_hash == timeline._stable_digest(modifiers.items())
