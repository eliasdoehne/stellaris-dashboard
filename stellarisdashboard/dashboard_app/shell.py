"""Navigation context shared by every page of the app shell (templates/layout.html)."""
import flask

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import flask_app

# (section key, label, icon, keyboard shortcut)
GAME_SECTIONS = [
    ("graphs", "Graphs", "graphs", "g"),
    ("galaxy", "Galaxy Map", "galaxy", "m"),
    ("ledger", "Event Ledger", "ledger", "l"),
    ("codex", "Codex", "codex", "c"),
]

_ENDPOINT_SECTIONS = {
    "galaxy_page": "galaxy",
    "history_page": "ledger",
    "codex_page": "codex",
    "index_page": "games",
    "import_page": "import",
    "settings_page": "settings",
}


def section_url(section: str, game_id: str, tab: str = None) -> str:
    if section == "graphs":
        args = {"game_name": game_id}
        if tab:
            args["tab"] = tab
        return flask.url_for("/timeline/", **args)
    endpoint = {"galaxy": "galaxy_page", "ledger": "history_page", "codex": "codex_page"}[section]
    return flask.url_for(endpoint, game_id=game_id)


def graph_tabs():
    return list(config.CONFIG.tab_layout)


def shell_games():
    return list(datamodel.get_available_games_dict().values())


@flask_app.context_processor
def inject_shell_context():
    return dict(
        game_sections=GAME_SECTIONS,
        endpoint_section=_ENDPOINT_SECTIONS.get(flask.request.endpoint),
        section_url=section_url,
        graph_tabs=graph_tabs,
        shell_games=shell_games,
    )
