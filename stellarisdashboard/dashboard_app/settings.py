import logging
from typing import Any, Dict, List, Optional

from flask import render_template, request, redirect
from markupsafe import escape

from stellarisdashboard import config, i18n
from stellarisdashboard.i18n import gettext as _
from stellarisdashboard.dashboard_app import flask_app

logger = logging.getLogger(__name__)


def category_labels() -> Dict[str, str]:
    """The settings page section of each category id of config.CATEGORIES."""
    return {
        "game_files": _("Game Files"),
        "visibility": _("Data Visibility"),
        "multiplayer": _("Multiplayer"),
        "import": _("Import Saves"),
        "interface": _("Interface"),
        "server": _("Server"),
    }


def setting_texts() -> Dict[str, Dict[str, str]]:
    """The label and description of every setting shown on the settings page (in_ui), in the current language."""
    return {
        "save_file_path": dict(
            label=_("Save files folder"),
            description=_("Where the dashboard will look for new Stellaris save files. Should contain a folder for each game, and each of those folder should contain one or .sav files. Leave blank to reset to default."),
        ),
        "stellaris_user_data_path": dict(
            label=_("Stellaris user data folder"),
            description=_("Where the dashboard will look for user data such as enabled mods. It should contain the dlc_load.json file. Leave blank to reset to default."),
        ),
        "stellaris_install_path": dict(
            label=_("Stellaris install folder"),
            description=_("Where the dashboard will look for Stellaris game data. Leave blank to reset to default."),
        ),
        "stellaris_language": dict(
            label=_("Stellaris language"),
            description=_("The language to use for the names of empires, technologies and other text from the game. Use the same language as in the game."),
        ),
        "show_everything": dict(
            label=_("Show information for all countries"),
            description=_("Show information for all countries, including unknown ones."),
        ),
        "show_all_country_types": dict(
            label=_("Show all country types"),
            description=_("Check to include special countries like enclaves, leviathans or the shroud."),
        ),
        "filter_events_by_type": dict(
            label=_("Filter history ledger by event type"),
            description=_("If enabled, only a selection of relevant historical events are shown in the event ledger, depending on the page."),
        ),
        "mp_username": dict(
            label=_("Your multiplayer username"),
            description=_("By default, data for countries controlled by players with any other username are hidden (see below setting). This setting does not affect single-player games."),
        ),
        "hide_other_players": dict(
            label=_("Hide other players"),
            description=_("If enabled, hides information for countries controlled by players that do not match the above multiplayer Username"),
        ),
        "save_name_filter": dict(
            label=_("Save file name filter"),
            # Translators: "Skip saves after processing" must match the name of that setting.
            description=_("Only save files whose file names contain this string are processed. For example, you could use \".01.01\" so only saves from the start of the year are processed. Alternatively, use \"Skip saves after processing\" below"),
        ),
        "skip_saves": dict(
            label=_("Skip saves after processing"),
            # Translators: "Save name filter" refers to the setting "Save file name filter".
            description=_("After processing a save, the next X saves are skipped. Use this if the dashboard cannot keep up with autosaves. Alternatively, use the \"Save name filter\" above."),
        ),
        "keep_failed_saves": dict(
            label=_("Keep copies of failed saves"),
            description=_("Copy saves that fail to import (fully or partially) to the failed_saves folder in the output folder, with a record of the error, so they can be imported again after a fix with \"stellarisdashboardcli reprocess-failed-saves\"."),
        ),
        "failed_saves_max_size_mb": dict(
            label=_("Failed saves folder size limit (MB)"),
            description=_("When the failed_saves folder grows beyond this size, the oldest copies are deleted."),
        ),
        "backup_saves": dict(
            label=_("Back up all saves"),
            description=_("Copy every save file the dashboard reads to the save_backups folder in the output folder, so the full history can be imported again later. WARNING: This folder has no size limit and grows by one save file per autosave."),
        ),
        "threads": dict(
            label=_("Number of threads"),
            description=_("Number of threads for reading save files."),
        ),
        "dashboard_language": dict(
            label=_("Dashboard language"),
            description=_("The language of the dashboard's own text. By default, the same language as the game."),
        ),
        "plot_height": dict(
            label=_("Graph height"),
            description=_("Height of graphs in pixels."),
        ),
        "plot_width": dict(
            label=_("Graph width"),
            description=_("Width of graphs in pixels."),
        ),
        "plot_time_resolution": dict(
            label=_("Initial graph resolution"),
            description=_("Number of points used when first loading the graphs. Set to 0 to load full data."),
        ),
        "check_version": dict(
            label=_("Check for new versions"),
            description=_("When using the Workshop mod for in-game access, a message will be displayed if a newer version of the separate dashboard app is available."),
        ),
        "host": dict(
            label=_("Host"),
            description=_("The network address the dashboard listens on. The default, 127.0.0.1, only allows access from this computer. Use 0.0.0.0 to allow access from other devices in your network."),
        ),
        "port": dict(
            label=_("Port"),
            description=_("The port the dashboard listens on, as in http://localhost:28053/."),
        ),
        "log_level": dict(
            label=_("Log level"),
            description=_("How much detail is written to the console log. Use DEBUG when reporting a problem."),
        ),
        "log_to_file": dict(
            label=_("Write log to file"),
            description=_("Also write the log to log.txt in the output folder."),
        ),
    }


def _choice_labels() -> Dict[str, Dict[str, str]]:
    """Display names of choices that need translation, by setting key."""
    return {
        "dashboard_language": {i18n.AUTO: _("Same as Stellaris language")},
    }


def _path_status(key: str) -> Optional[Dict[str, str]]:
    cfg = config.CONFIG
    if key == "save_file_path":
        if cfg.save_file_path_is_valid:
            return dict(level="ok", message="")
        return dict(level="warning", message=_("No save files found. This folder should contain a folder for each game, which each contain .sav files."))
    if key == "stellaris_install_path":
        if cfg.stellaris_install_path_is_valid:
            return dict(level="ok", message="")
        return dict(level="warning", message=_("The Stellaris game files were not found. This folder should contain the localisation and flags folders."))
    if key == "stellaris_user_data_path":
        if cfg.stellaris_user_data_path_is_valid:
            return dict(level="ok", message="")
        return dict(level="warning", message=_("This folder should contain the dlc_load.json file."))
    return None


def _view_value(spec: config.SettingSpec, value) -> Any:
    if spec.kind in (config.BOOL, config.INT, config.FLOAT):
        return value
    return "" if value is None else str(value)


def settings_view() -> List[Dict[str, Any]]:
    """The view model of the settings page: the settings shown on it (in_ui), by section.

    Returns a list of sections in display order, without empty ones::

        {"id": "game_files", "label": "Game Files", "settings": [setting, ...]}

    where each setting is a dict::

        key               setting key, also the name of the form field
        kind              "bool", "int", "float", "str", "path" or "choice"
        value             current value: bool for "bool", int/float for numbers, str otherwise
        default           the default value as text (bools as "true"/"false")
        is_default        True if the current value is the default
        label             translated name
        description       translated help text
        min, max          bounds of numbers, or None
        choices           for "choice": list of (value, display name) pairs, including the
                          current value if it is not one of the known options (e.g. a
                          language added by a mod); None for other kinds
        requires_restart  True if a change only applies after restarting the dashboard
        advanced          True if the setting belongs in a collapsed "advanced" part
        depends_on        key of the setting this one only matters for, or None
        status            for the game folder paths: {"level": "ok" or "warning",
                          "message": translated text, empty if ok}; None otherwise

    The form must submit every setting (see config.settings_from_form): a checkbox
    that is missing from the submitted form means False.
    """
    cfg = config.CONFIG
    texts = setting_texts()
    choice_labels = _choice_labels()
    sections = {category: [] for category in config.CATEGORIES}
    for key, spec in config.SETTINGS.items():
        if not spec.in_ui:
            continue
        value = getattr(cfg, key)
        default = config.DEFAULT_SETTINGS[key]
        choices = None
        if spec.kind == config.CHOICE:
            names = {**spec.get_choices(), **choice_labels.get(key, {})}
            if value not in names:
                names[value] = value
            choices = list(names.items())
        sections.setdefault(spec.category, []).append(
            dict(
                key=key,
                kind=spec.kind,
                value=_view_value(spec, value),
                default=("true" if default else "false") if spec.kind == config.BOOL else str(default),
                is_default=not cfg.is_customized(key),
                label=texts[key]["label"],
                description=texts[key]["description"],
                min=spec.min_value,
                max=spec.max_value,
                choices=choices,
                requires_restart=spec.requires_restart,
                advanced=spec.advanced,
                depends_on=spec.depends_on,
                status=_path_status(key) if spec.kind == config.PATH else None,
            )
        )
    labels = category_labels()
    return [
        dict(id=category, label=labels.get(category, category), settings=settings)
        for category, settings in sections.items()
        if settings
    ]


def apply_form(form) -> config.ApplyResult:
    """Apply the settings submitted with the settings page and save them to config.yml.

    Invalid values are rejected and keep their previous value; the other settings are
    still applied. Returns what changed, what was rejected and what needs a restart."""
    result = config.CONFIG.apply_dict(config.settings_from_form(form), source=config.SOURCE_FORM)
    if {"log_level", "log_to_file"} & set(result.changed):
        config.configure_logger()
    config.CONFIG.write_to_file()
    return result


def _toast(result: config.ApplyResult) -> str:
    texts = setting_texts()
    if result.rejected:
        items = "".join(
            f"<li>{escape(texts.get(r.key, {}).get('label', r.key))}: {escape(r.translated_reason())}</li>"
            for r in result.rejected
        )
        body = f"{escape(_('Some settings were not applied:'))}<ul>{items}</ul>"
        kind = "error"
    else:
        body = escape(_("Settings applied."))
        kind = "success"
    if result.restart_required:
        names = ", ".join(texts.get(key, {}).get("label", key) for key in result.restart_required)
        body += f"<p>{escape(_('Restart the dashboard to apply: %(settings)s', settings=names))}</p>"
    role = "alert" if result.rejected else "status"
    return f'<div class="toast toast--{kind}" role="{role}">{body}</div>'


@flask_app.route("/settings/")
@flask_app.route("/settings")
def settings_page():
    return render_template(
        "settings_page.html",
        sections=settings_view(),
        settings_file=config.settings_file_path(),
    )


@flask_app.route("/applysettings/", methods=["POST"])
def apply_settings():
    previous_locale = i18n.current_locale()
    result = apply_form(request.form)
    if request.headers.get("HX-Request"):
        if i18n.current_locale() != previous_locale and not result.rejected:
            # the page (form, menus) is in the old language: have htmx reload it
            return "", 200, {"HX-Refresh": "true"}
        # htmx save: confirm with an out-of-band toast swapped into #toast-slot;
        # the form itself is left untouched (hx-swap="none").
        return f'<div id="toast-slot" hx-swap-oob="innerHTML">{_toast(result)}</div>'
    return redirect("/settings/")
