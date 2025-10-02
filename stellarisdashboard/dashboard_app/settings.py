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
        "File Locations": {
            "save_file_path": {
                "type": t_str,
                "value": current_values["save_file_path"],
                "name": "Save files folder *",
                "description": "Where the dashboard will look for new Stellaris save files. Should contain a folder for each game, and each of those folder should contain one or .sav files. Leave blank to reset to default.",
            },
            "stellaris_user_data_path": {
                "type": t_str,
                "value": current_values["stellaris_user_data_path"],
                "name": "Stellaris user data folder *",
                "description": "Where the dashboard will look for user data such as enabled mods. It should contain the dlc_load.json file. Leave blank to reset to default.",
            },
            "stellaris_install_path": {
                "type": t_str,
                "value": current_values["stellaris_install_path"],
                "name": "Stellaris install folder *",
                "description": "Where the dashboard will look for Stellaris game data. Leave blank to reset to default.",
            },
        },
        "Data Visibility": {
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
            "mp_username": {
                "type": t_str,
                "value": current_values["mp_username"],
                "name": "Your multiplayer username",
                "description": "By default, data for countries controlled by players with any other username are hidden (see below setting). This setting does not affect single-player games.",
            },
            "hide_other_players": {
                "type": t_bool,
                "value": _bool_to_lowercase(current_values["hide_other_players"]),
                "name": "Hide other players",
                "description": "If enabled, hides information for countries controlled by players that do not match the above multiplayer Username",
            },
        },
        "Performance": {
            "read_all_countries": {
                "type": t_bool,
                "value": _bool_to_lowercase(current_values["read_all_countries"]),
                "name": "Store data of all countries",
                "description": "Store budgets and internal stats of all countries. This makes a larger database and could slow things down.",
            },
            "save_name_filter": {
                "type": t_str,
                "value": current_values["save_name_filter"],
                "name": "Save file name filter",
                "description": "Only save files whose file names contain this string are processed. For example, you could use \".01.01\" so only saves from the start of the year are processed. Alternatively, use \"Skip saves after processing\" below",
            },
            "skip_saves": {
                "type": t_int,
                "value": current_values["skip_saves"],
                "min": 0,
                "max": 100,
                "name": "Skip saves after processing",
                "description": "After processing a save, the next X saves are skipped. Use this if the dashboard cannot keep up with autosaves. Alternatively, use the \"Save name filter\" above.",
            },
            "plot_time_resolution": {
                "type": t_int,
                "value": current_values["plot_time_resolution"],
                "min": 0,
                "max": 10000,
                "name": "Initial graph resolution",
                "description": "Number of points used when first loading the graphs. Set to 0 to load full data.",
            },
            "threads": {
                "type": t_int,
                "value": current_values["threads"],
                "min": 1,
                "name": "Number of threads *",
                "description": "Number of threads for reading save files.",
            },
        },
        "Interface": {
            "check_version": {
                "type": t_bool,
                "value": _bool_to_lowercase(current_values["check_version"]),
                "name": "Check for new versions",
                "description": "When using the Workshop mod for in-game access, a message will be displayed if a newer version of the separate dashboard app is available.",
            },
            "stellaris_language": {
                "type": t_str,
                "value": current_values["stellaris_language"],
                "name": "Stellaris language *",
                "description": "The language to use for localizing Stellaris text. In the format l_<language> (for example, l_english)"
            },
            "plot_width": {
                "type": t_int,
                "value": current_values["plot_width"],
                "min": 300,
                "max": 2000,
                "name": "Graph width",
                "description": "Width of graphs in pixels.",
            },
            "plot_height": {
                "type": t_int,
                "value": current_values["plot_height"],
                "min": 300,
                "max": 2000,
                "name": "Graph height",
                "description": "Height of graphs in pixels.",
            },
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
