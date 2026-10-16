import logging

from stellarisdashboard import config
from stellarisdashboard.dashboard_app import visualization_data


def test_unknown_graph_ids_in_tab_layout_are_skipped(monkeypatch, caplog):
    layout = {"Science": ["technology_progress_graph", "survey_progress_graph"]}
    monkeypatch.setattr(config.CONFIG, "tab_layout", layout)

    with caplog.at_level(logging.WARNING):
        specs = visualization_data.get_plot_specifications_for_tab_layout()

    assert specs == {
        "Science": [visualization_data.PLOT_SPECIFICATIONS["technology_progress_graph"]]
    }
    assert "survey_progress_graph" in caplog.text
