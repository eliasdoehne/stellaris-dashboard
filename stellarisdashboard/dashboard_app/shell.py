"""Navigation context shared by every page of the app shell (templates/layout.html)."""
import flask

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import flask_app
from stellarisdashboard.i18n import N_, gettext as _, pgettext

# (section key, label, icon, keyboard shortcut); the labels are translated in inject_shell_context
GAME_SECTIONS = [
    ("overview", N_("Overview"), "overview", "o"),
    ("graphs", N_("Graphs"), "graphs", "g"),
    ("galaxy", N_("Galaxy Map"), "galaxy", "m"),
    ("ledger", N_("Event Ledger"), "ledger", "l"),
    ("codex", N_("Codex"), "codex", "c"),
]

# section key -> name of the section, in the page title and the breadcrumb
SECTION_LABELS = {
    **{key: label for key, label, _icon, _shortcut in GAME_SECTIONS},
    "games": N_("Games"),
    "import": N_("Import Saves"),
    "settings": N_("Settings"),
}

_ENDPOINT_SECTIONS = {
    "overview_page": "overview",
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
    endpoint = {"overview": "overview_page", "galaxy": "galaxy_page", "ledger": "history_page", "codex": "codex_page"}[section]
    return flask.url_for(endpoint, game_id=game_id)


def section_label(section: str) -> str:
    label = SECTION_LABELS.get(section)
    return _(label) if label else "Stellaris Dashboard"


def graph_tabs():
    return list(config.CONFIG.tab_layout)


def _get_tab_labels() -> dict:
    """Display names of the default graph tabs in the current dashboard language."""
    return {
        config.MARKET_TAB: pgettext("graph tab", "Markets"),
        "Budget": pgettext("graph tab", "Budget"),
        "Economy": pgettext("graph tab", "Economy"),
        "Demographics": pgettext("graph tab", "Demographics"),
        # Translators: name of a tab with graphs about the pops (population units)
        "Pops": pgettext("graph tab", "Pops"),
        "Planets": pgettext("graph tab", "Planets"),
        "Science": pgettext("graph tab", "Science"),
        "Military": pgettext("graph tab", "Military"),
        "Victory": pgettext("graph tab", "Victory"),
    }


def graph_tab_label(tab: str) -> str:
    """The display name of a graph tab. The tab names are the keys of the tab layout
    setting and identify the tabs (also in URLs), so they stay English; customised
    tabs that are not among the defaults are displayed as they are."""
    return _get_tab_labels().get(tab, tab)


def shell_games():
    return list(datamodel.get_available_games_dict().values())


@flask_app.context_processor
def inject_shell_context():
    return dict(
        game_sections=[(key, _(label), icon, shortcut) for key, label, icon, shortcut in GAME_SECTIONS],
        section_label=section_label,
        graph_tab_label=graph_tab_label,
        endpoint_section=_ENDPOINT_SECTIONS.get(flask.request.endpoint),
        section_url=section_url,
        graph_tabs=graph_tabs,
        shell_games=shell_games,
    )
