from stellarisdashboard import country_color_cache
from stellarisdashboard.country_color_cache import LRUDict


def test_lru_dict_evicts_least_recently_used():
    d = LRUDict(2)
    d["a"] = 1
    d["b"] = 2
    assert d["a"] == 1  # reading "a" makes "b" the eviction candidate
    d["c"] = 3
    assert "b" not in d
    assert list(d) == ["a", "c"]


def test_lru_dict_overwrite_does_not_grow():
    d = LRUDict(2)
    d["a"] = 1
    d["b"] = 2
    d["a"] = 3
    assert len(d) == 2 and d["a"] == 3


def test_game_country_colors_is_bounded_and_clearable():
    cache = country_color_cache.GAME_COUNTRY_COLORS
    cache.clear()
    for i in range(cache.max_size + 5):
        cache[f"game{i}"] = object()
    assert len(cache) == cache.max_size
    country_color_cache.clear_cached_country_colors()
    assert len(cache) == 0
