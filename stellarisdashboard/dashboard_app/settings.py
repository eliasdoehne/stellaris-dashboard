import logging

from flask import render_template, request, redirect

from stellarisdashboard import config
from stellarisdashboard.dashboard_app import flask_app

logger = logging.getLogger(__name__)


@flask_app.route("/settings/")
@flask_app.route("/settings")
def settings_page():
    def _bool_to_lowercase(py_bool: bool) -> str:
        return "true" if py_bool else "false"

    t_int = "int"
    t_bool = "bool"
    t_str = "str"

    current_values = config.CONFIG.get_adjustable_settings_dict()
    settings = {
        "check_version": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["check_version"]),
            "name": "Check for new versions (only when subscribed in the Steam Workshop)",
            "description": "Check for new versions (only when subscribed in the Steam Workshop)",
        },
        "save_file_path": {
            "type": t_str,
            "value": current_values["save_file_path"],
            "name": "Save file path (applies after restart, set to empty to restore default, applies after restart)",
            "description": "Where the dashboard will look for new Stellaris save files.",
        },
        "localization_file_dir": {
            "type": t_str,
            "value": current_values["localization_file_dir"],
            "name": "Stellaris localization folder",
            "description": "Path where the dashboard will look for Stellaris localization files that define how names are created. Applies after dashboard restart. See README.md for details.",
        },
        "mp_username": {
            "type": t_str,
            "value": current_values["mp_username"],
            "name": "Your Multiplayer username",
            "description": "",
        },
        "threads": {
            "type": t_int,
            "value": current_values["threads"],
            "min": 1,
            "max": config.CPU_COUNT,
            "name": "Number of threads (applies after restart)",
            "description": "Number of threads for reading save files, updates apply after restarting the dashboard.",
        },
        "show_everything": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["show_everything"]),
            "name": "Show information for all countries",
            "description": "Show information for all countries, including unknown ones.",
        },
        "show_all_country_types": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["show_all_country_types"]),
            "name": "Show all country types",
            "description": "Check to include special countries like enclaves, leviathans or the shroud.",
        },
        "filter_events_by_type": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["filter_events_by_type"]),
            "name": "Filter history ledger by event type",
            "description": "If enabled, only a selection of relevant historical events are shown in the event ledger, depending on the page.",
        },
        "save_name_filter": {
            "type": t_str,
            "value": current_values["save_name_filter"],
            "name": "Save file name filter",
            "description": "Only save files whose file names contain this string are processed.",
        },
        "read_all_countries": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["read_all_countries"]),
            "name": "Store data of all countries",
            "description": "Store budgets and internal stats of all countries. This makes a larger database and could slow things down.",
        },
        "skip_saves": {
            "type": t_int,
            "value": current_values["skip_saves"],
            "min": 0,
            "max": 100,
            "name": "Skip saves after processing (increase if save processing can't keep up with the game)",
            "description": "Number of save files skipped for each save file that is processed.",
        },
        "plot_time_resolution": {
            "type": t_int,
            "value": current_values["plot_time_resolution"],
            "min": 0,
            "max": 10000,
            "name": "Initial graph resolution (set to 0 to load the full data)",
            "description": "Number of points used when first loading the graphs.",
        },
        "plot_width": {
            "type": t_int,
            "value": current_values["plot_width"],
            "min": 300,
            "max": 2000,
            "name": "Graph width (pixels)",
            "description": "Width of graphs.",
        },
        "plot_height": {
            "type": t_int,
            "value": current_values["plot_height"],
            "min": 300,
            "max": 2000,
            "name": "Graph height (pixels)",
            "description": "Height of graphs.",
        },
    }
    return render_template("settings_page.html", current_settings=settings)


@flask_app.route("/applysettings/", methods=["POST", "GET"])
def apply_settings():
    previous_settings = config.CONFIG.get_adjustable_settings_dict()
    settings = request.form.to_dict(flat=True)
    for key in settings:
        if key in config.Config.BOOL_KEYS:
            # only checked items are included in form
            settings[key] = key in settings
        if key in config.Config.INT_KEYS:
            settings[key] = int(settings[key])
        if key in config.Config.FLOAT_KEYS:
            settings[key] = float(settings[key])
    for key in previous_settings:
        if key in config.Config.BOOL_KEYS and key not in settings:
            settings[key] = False
    for unadjustable_key in ["tab_layout", "market_fee"]:
        settings[unadjustable_key] = previous_settings[unadjustable_key]
    config.CONFIG.apply_dict(settings)
    config.CONFIG.write_to_file()
    return redirect("/")
