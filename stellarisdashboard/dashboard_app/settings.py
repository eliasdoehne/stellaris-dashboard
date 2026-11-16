import copy
import logging
from typing import Any, Dict, List, Mapping, Optional

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


def category_intros() -> Dict[str, str]:
    """One sentence under the heading of each settings page section."""
    return {
        "game_files": _("Where the dashboard finds your saves and the files of the game."),
        "visibility": _("How much the dashboard shows about other empires."),
        "multiplayer": _("Keep the empires of other players hidden in multiplayer games."),
        "import": _("How the dashboard reads new saves while you play."),
        "interface": _("Language, graphs and other display options."),
        "server": _("Network and logging options of the dashboard app."),
    }


def setting_texts() -> Dict[str, Dict[str, str]]:
    """The label and description of every setting shown on the settings page (in_ui), in the current language."""
    return {
        "save_file_path": dict(
            label=_("Save files folder"),
            description=_("Where the dashboard will look for new Stellaris save files. It should contain a folder for each game, and each of those folders should contain one or more .sav files. Leave blank to reset to default."),
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
        "skip_saves": dict(
            label=_("Skip saves after processing"),
            description=_("After processing a save, the next X saves are skipped. Use this if the dashboard cannot keep up with autosaves."),
        ),
        "keep_failed_saves": dict(
            label=_("Keep copies of failed saves"),
            description=_("Copy saves that fail to import (fully or partially) to the failed_saves folder in the output folder, with a record of the error, so they can be imported again after a fix with \"stellarisdashboardcli reprocess-failed-saves\"."),
        ),
        "failed_saves_max_size_mb": dict(
            label=_("Failed saves folder size limit"),
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
        "plot_time_resolution": dict(
            label=_("Initial graph resolution"),
            description=_("Number of points used when first loading the graphs. Set to 0 to load full data."),
        ),
        "check_version": dict(
            label=_("Check for new versions"),
            description=_("When using the Workshop mod for in-game access, a message will be displayed if a newer version of the separate dashboard app is available."),
        ),
        "open_browser": dict(
            label=_("Open in browser on start"),
            description=_("Open the dashboard in your default web browser when the dashboard app starts."),
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




def _units() -> Dict[str, str]:
    """The unit shown after the input of number settings, by setting key."""
    return {
        # Translators: unit after a number input, short for megabytes
        "failed_saves_max_size_mb": _("MB"),
        # Translators: unit after a number input, short for pixels
        "plot_height": _("px"),
        # Translators: unit after a number input: "Skip saves after processing: [3] saves"
        "skip_saves": _("saves"),
        # Translators: unit after a number input: the number of data points loaded per graph
        "plot_time_resolution": _("points"),
    }


def _choice_labels() -> Dict[str, Dict[str, str]]:
    """Display names of choices that need translation, by setting key."""
    return {
        "dashboard_language": {i18n.AUTO: _("Same as Stellaris language")},
    }


def _folder_found_messages() -> Dict[str, str]:
    return {
        "save_file_path": _("Save files found in this folder."),
        "stellaris_install_path": _("The Stellaris game files are in this folder."),
        "stellaris_user_data_path": _("The dlc_load.json file is in this folder."),
    }


def _folder_problem_messages() -> Dict[str, str]:
    return {
        "save_file_path": _("No save files found. This folder should contain a folder for each game, which each contain .sav files."),
        "stellaris_install_path": _("The Stellaris game files were not found. This folder should contain the localisation and flags folders."),
        "stellaris_user_data_path": _("This folder should contain the dlc_load.json file."),
    }


def folder_status(key: str, raw) -> Dict[str, str]:
    """Whether a folder setting would be accepted with the value raw, and whether the
    folder looks right: {"level": "ok", "warning" or "error", "message": translated text}.

    Only tells whether the folder exists and has the expected contents, nothing else
    about the file system."""
    check = config.check_folder(key, raw)
    if check.rejected is not None:
        return dict(level="error", message=check.rejected.translated_reason())
    if check.looks_right:
        return dict(level="ok", message=_folder_found_messages()[key])
    try:
        exists = check.path.is_dir()
    except (OSError, ValueError):
        exists = False
    if not exists:
        return dict(level="warning", message=_("This folder does not exist."))
    return dict(level="warning", message=_folder_problem_messages()[key])


def _form_text(spec: config.SettingSpec, value) -> str:
    """A setting value as the form submits it."""
    if spec.kind == config.BOOL:
        return "true" if value else "false"
    return "" if value is None else str(value)


def _view_value(spec: config.SettingSpec, value) -> Any:
    if spec.kind in (config.BOOL, config.INT, config.FLOAT):
        return value
    return "" if value is None else str(value)


def _is_set(row: Dict[str, Any]) -> bool:
    value = row["value"]
    return bool(value.strip()) if isinstance(value, str) else bool(value)


# The values of the settings that need a restart, as this process uses them. A setting
# is waiting for a restart while its current value differs from the one remembered here.
_running_values: Dict[str, Any] = {}


def remember_running_values():
    """Remember the current values of the settings that need a restart as the ones in use."""
    _running_values.clear()
    _running_values.update(
        (key, copy.deepcopy(getattr(config.CONFIG, key)))
        for key, spec in config.SETTINGS.items()
        if spec.requires_restart and spec.in_ui
    )


def pending_restart() -> List[str]:
    """Settings that were changed, but only apply after restarting the dashboard."""
    return [key for key, running in _running_values.items() if getattr(config.CONFIG, key) != running]


remember_running_values()

# Settings in config.yml that were rejected at startup are listed on the settings page until dismissed.
_startup_notice_dismissed = False


def startup_problems() -> List[Dict[str, str]]:
    """The settings in config.yml that were invalid at startup and replaced by their default."""
    if _startup_notice_dismissed or config.LOAD_RESULT is None:
        return []
    texts = setting_texts()
    return [
        dict(
            key=r.key,
            label=texts.get(r.key, {}).get("label", r.key),
            reason=r.translated_reason(),
        )
        for r in config.LOAD_RESULT.rejected
    ]


def settings_view(
    submitted: Optional[Mapping[str, Any]] = None,
    rejected: List[config.RejectedSetting] = (),
) -> List[Dict[str, Any]]:
    """The view model of the settings page: the settings shown on it (in_ui), by section.

    submitted and rejected are the form and the rejected values of a save that failed:
    rejected settings then show the submitted value with the error, so it can be fixed.

    Returns a list of sections in display order, without empty ones::

        {"id": "game_files", "label": "Game Files", "intro": "One sentence.",
         "settings": [setting, ...],           # all of them, in order
         "basic": [...], "advanced": [...],    # split by the advanced flag
         "advanced_open": bool}                # an advanced setting is customized or has an error

    where each setting is a dict::

        key               setting key, also the name of the form field
        kind              "bool", "int", "float", "str", "path" or "choice"
        value             value to show: bool for "bool", int/float for numbers, str
                          otherwise; the submitted text if the value was rejected
        saved             the stored value as the form submits it (bools as "true"/"false")
        default           the default value as text (bools as "true"/"false")
        is_default        True if the stored value is the default
        label             translated name
        description       translated help text
        unit              translated unit shown after a number input, or None
        placeholder       for "path": the detected default folder; None otherwise
        min, max          bounds of numbers, or None
        choices           for "choice": list of (value, display name) pairs, including the
                          current value if it is not one of the known options (e.g. a
                          language added by a mod); None for other kinds
        requires_restart  True if a change only applies after restarting the dashboard
        pending_restart   True if the setting was changed and waits for a restart
        advanced          True if the setting belongs in the collapsed "advanced" part
        depends_on        key of the setting this one only matters for, or None
        inactive          True if depends_on is not set, so this setting has no effect
        inactive_note     translated explanation shown while inactive (also for JS)
        status            for the game folders: {"level": "ok", "warning" or "error",
                          "message": translated text}; None otherwise, and while
                          the setting has an error
        checks_folder     True for the game folders, which the page checks while typing
        error             translated reason why the submitted value was rejected, or None

    The form must submit every setting (see config.settings_from_form): a checkbox
    that is missing from the submitted form means False.
    """
    cfg = config.CONFIG
    texts = setting_texts()
    units = _units()
    choice_labels = _choice_labels()
    errors = {r.key: r for r in rejected}
    waiting = set(pending_restart())
    sections = {category: [] for category in config.CATEGORIES}
    rows = {}
    for key, spec in config.SETTINGS.items():
        if not spec.in_ui:
            continue
        stored = getattr(cfg, key)
        error = errors.get(key)
        if error is not None and submitted is not None and spec.kind != config.BOOL:
            value = str(submitted.get(key, ""))
        else:
            value = _view_value(spec, stored)
        default = config.DEFAULT_SETTINGS[key]
        choices = None
        if spec.kind == config.CHOICE:
            names = {**spec.get_choices(), **choice_labels.get(key, {})}
            if value not in names:
                names[value] = value
            choices = list(names.items())
        checks_folder = spec.kind == config.PATH and config.has_folder_check(key)
        status = None
        if checks_folder and error is None:
            status = folder_status(key, str(stored))
        row = dict(
            key=key,
            kind=spec.kind,
            value=value,
            saved=_form_text(spec, stored),
            default=_form_text(spec, default),
            is_default=not cfg.is_customized(key),
            label=texts[key]["label"],
            description=texts[key]["description"],
            unit=units.get(key),
            placeholder=str(default) if spec.kind == config.PATH else None,
            min=spec.min_value,
            max=spec.max_value,
            choices=choices,
            requires_restart=spec.requires_restart,
            pending_restart=key in waiting,
            advanced=spec.advanced,
            depends_on=spec.depends_on,
            inactive=False,
            inactive_note=None,
            status=status,
            checks_folder=checks_folder,
            error=error.translated_reason() if error is not None else None,
        )
        rows[key] = row
        sections.setdefault(spec.category, []).append(row)
    for row in rows.values():
        parent = rows.get(row["depends_on"])
        if parent is None:
            continue
        if parent["kind"] == config.BOOL:
            row["inactive_note"] = _("Only applies when “%(setting)s” is turned on.", setting=parent["label"])
        else:
            row["inactive_note"] = _("Only applies when “%(setting)s” is filled in.", setting=parent["label"])
        row["inactive"] = not _is_set(parent)
    labels = category_labels()
    intros = category_intros()
    result = []
    for category, settings in sections.items():
        if not settings:
            continue
        advanced = [s for s in settings if s["advanced"]]
        result.append(dict(
            id=category,
            label=labels.get(category, category),
            intro=intros.get(category, ""),
            settings=settings,
            basic=[s for s in settings if not s["advanced"]],
            advanced=advanced,
            advanced_open=any(not s["is_default"] or s["error"] for s in advanced),
        ))
    return result


def apply_form(form) -> config.ApplyResult:
    """Apply the settings submitted with the settings page and save them to config.yml.

    Invalid values are rejected and keep their previous value; the other settings are
    still applied. Returns what changed, what was rejected and what needs a restart."""
    result = config.CONFIG.apply_dict(config.settings_from_form(form), source=config.SOURCE_FORM)
    _after_change(result)
    return result


def reset_all() -> config.ApplyResult:
    """Reset the settings of the settings page (in_ui) to their defaults and save.

    Settings that can only be changed in config.yml (tab_layout, ...) are kept."""
    keys = [key for key, spec in config.SETTINGS.items() if spec.in_ui]
    result = config.CONFIG.reset_to_defaults(keys)
    _after_change(result)
    return result


def _after_change(result: config.ApplyResult):
    if {"log_level", "log_to_file"} & set(result.changed):
        config.configure_logger()
    config.CONFIG.write_to_file()


def _labels(keys) -> str:
    texts = setting_texts()
    return ", ".join(texts.get(key, {}).get("label", key) for key in keys)


def _toast(result: config.ApplyResult, success_message: str) -> str:
    texts = setting_texts()
    if result.rejected:
        items = "".join(
            f"<li>{escape(texts.get(r.key, {}).get('label', r.key))}: {escape(r.translated_reason())}</li>"
            for r in result.rejected
        )
        body = f"{escape(_('Some settings were not applied:'))}<ul>{items}</ul>"
        kind = "error"
    else:
        # str: adding text to Markup would escape the markup added below
        body = str(escape(success_message))
        kind = "success"
    # only settings that now wait for a restart: not one changed back to the value in use
    newly_waiting = [key for key in pending_restart() if key in result.changed]
    if newly_waiting:
        body += f"<p>{escape(_('Restart the dashboard to apply: %(settings)s', settings=_labels(newly_waiting)))}</p>"
    role = "alert" if result.rejected else "status"
    return f'<div class="toast toast--{kind}" role="{role}">{body}</div>'


README_URL = "https://github.com/eliasdoehne/stellaris-dashboard#readme"


def _page_context(submitted=None, rejected=()) -> Dict[str, Any]:
    sections = settings_view(submitted, rejected)
    rows = {s["key"]: s for section in sections for s in section["settings"]}
    return dict(
        section="settings",
        sections=sections,
        errors=[rows[r.key] for r in rejected if r.key in rows],
        restart_labels=_labels(pending_restart()),
        startup_problems=startup_problems(),
        settings_file=config.settings_file_path(),
        output_folder=config.CONFIG.base_output_path,
        readme_url=README_URL,
    )


def _htmx_reply(result: config.ApplyResult, previous_locale: str, success_message: str, submitted=None):
    if i18n.current_locale() != previous_locale and not result.rejected:
        # the page (form, menus) is in the old language: have htmx reload it
        return "", 200, {"HX-Refresh": "true"}
    # the settings (with any errors) replace #settings-content, the toast goes to #toast-slot
    content = render_template("_settings_content.html", **_page_context(submitted, result.rejected))
    return content + f'<div id="toast-slot" hx-swap-oob="innerHTML">{_toast(result, success_message)}</div>'


@flask_app.route("/settings/")
@flask_app.route("/settings")
def settings_page():
    return render_template("settings_page.html", **_page_context())


@flask_app.route("/applysettings/", methods=["POST"])
def apply_settings():
    previous_locale = i18n.current_locale()
    result = apply_form(request.form)
    if request.headers.get("HX-Request"):
        return _htmx_reply(result, previous_locale, _("Settings saved."), submitted=request.form)
    if result.rejected:
        # without JS: show the page with the errors, instead of losing what was typed
        return render_template("settings_page.html", **_page_context(request.form, result.rejected))
    return redirect("/settings/")


@flask_app.route("/settings/reset/", methods=["POST"])
def reset_settings():
    previous_locale = i18n.current_locale()
    result = reset_all()
    if request.headers.get("HX-Request"):
        return _htmx_reply(result, previous_locale, _("All settings were reset to their defaults."))
    return redirect("/settings/")


@flask_app.route("/settings/check-path/", methods=["POST"])
def check_path():
    """Check a typed folder without saving it; replies with the status line of the setting."""
    key = request.form.get("key", "")
    spec = config.SETTINGS.get(key)
    if spec is None or not spec.in_ui or not config.has_folder_check(key):
        return "", 400
    return render_template("_setting_status.html", key=key, status=folder_status(key, request.form.get(key, "")))


@flask_app.route("/settings/dismiss-startup-notice/", methods=["POST"])
def dismiss_startup_notice():
    global _startup_notice_dismissed
    _startup_notice_dismissed = True
    if request.headers.get("HX-Request"):
        return ""
    return redirect("/settings/")
