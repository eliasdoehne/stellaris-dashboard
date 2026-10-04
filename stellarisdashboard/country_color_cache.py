"""Process-wide cache of per-game country colors.

Lives in its own dependency-free module so the save-parsing pipeline
(`parsing.timeline`) can invalidate the cache when a country's color may have
changed, without importing the heavy visualization stack (plotly / scipy /
networkx) that `dashboard_app.visualization_data` pulls in.

`visualization_data.get_color_vals` owns the cache's contents; this module only
holds the shared dict and the invalidation hook.
"""

# game_id -> CountryColors (populated by visualization_data.get_color_vals)
GAME_COUNTRY_COLORS = {}


def clear_cached_country_colors():
    GAME_COUNTRY_COLORS.clear()
