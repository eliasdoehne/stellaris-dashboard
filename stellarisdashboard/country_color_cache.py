"""Process-wide cache of per-game country colors.

Lives in its own dependency-free module so the save-parsing pipeline
(`parsing.timeline`) can invalidate the cache when a country's color may have
changed, without importing the heavy visualization stack (plotly / scipy /
networkx) that `dashboard_app.visualization_data` pulls in.

`visualization_data.get_color_vals` owns the cache's contents; this module only
holds the shared dict and the invalidation hook.
"""
from collections import OrderedDict


class LRUDict(OrderedDict):
    """Dict that keeps only the `max_size` most recently read or written keys."""

    def __init__(self, max_size: int):
        super().__init__()
        self.max_size = max_size

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.max_size:
            self.popitem(last=False)


# game_id -> CountryColors (populated by visualization_data.get_color_vals)
GAME_COUNTRY_COLORS = LRUDict(16)


def clear_cached_country_colors():
    GAME_COUNTRY_COLORS.clear()
