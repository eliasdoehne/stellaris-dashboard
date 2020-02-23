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
    t_float = "float"
    current_values = config.CONFIG.get_adjustable_settings_dict()
    settings = {
        "check_version": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["check_version"]),
            "name": "Check for new versions",
            "description": "Check if new versions of the dashboard are available. This only works if you subscribe to the mod in the Steam workshop.",
        },
        "show_everything": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["show_everything"]),
            "name": "Cheat mode: Show all empires",
            "description": "Cheat mode: Show data for all empires, regardless of diplomatic status, even if you haven't met them in-game.",
        },
        "only_show_default_empires": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["only_show_default_empires"]),
            "name": "Only show default empires",
            "description": "Only show default-class empires, i.e. normal countries. Use it to exclude fallen empires and similar. Usually, this setting only matters if you have the Cheat mode enabled.",
        },
        "filter_events_by_type": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["filter_events_by_type"]),
            "name": "Filter historical events by scope",
            "description": "If enabled, only the most relevant historical events are shown to reduce clutter in the event ledger.",
        },
        "normalize_stacked_plots": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["normalize_stacked_plots"]),
            "name": "Normalize stacked area plots",
            "description": 'Default value for the "Normalize stacked plots" checkbox on the timeline page. If active, stacked plots (e.g. population stats) will show percentages instead of raw values.',
        },
        "use_two_y_axes_for_budgets": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["use_two_y_axes_for_budgets"]),
            "name": "Use separate y-axis for budget net income",
            "description": "If enabled, the net income lines of budget graphs are drawn on a separate y-axis.",
        },
        "save_name_filter": {
            "type": t_str,
            "value": current_values["save_name_filter"],
            "name": "Save file name filter",
            "description": 'Saves whose file names do not contain this string are ignored. For example, you can use this to filter yearly autosaves, by setting the value to ".01.01.sav".',
        },
        "read_non_player_countries": {
            "type": t_bool,
            "value": _bool_to_lowercase(current_values["show_everything"]),
            "name": "Read everything (Warning: might have big performance impact!)",
            "description": "Read budgets and pop stats of all countries. This will result in much larger database files and slow things down quite a bit. Consider adjusting the budget frequency parameter below to compensate.",
        },
        "read_only_every_nth_save": {
            "type": t_int,
            "value": current_values["read_only_every_nth_save"],
            "min": 1,
            "max": 10000,
            "name": "Save file import frequency (set 1 to import every save)",
            "description": "Reading every save file may result in a fairly large database file. This setting allows to skip reading save files, at the cost of storing fewer details. Set to 1 to read every save, to 2 to ignore every other save, to 3 to ignore 2/3 of saves, and so on. This filter is applied after all other filters.",
        },
        "budget_pop_stats_frequency": {
            "type": t_int,
            "value": current_values["budget_pop_stats_frequency"],
            "min": 1,
            "max": 10000,
            "name": "Pop stats/budget import frequency (set 1 to import data every time)",
            "description": "Sets the number read budgets and pop stats only for some save files. Note: this is applied on top of the save file import frequency.",
        },
        "plot_time_resolution": {
            "type": t_int,
            "value": current_values["plot_time_resolution"],
            "min": 0,
            "max": 10000,
            "name": "Graph Resolution (# of data points)",
            "description": "This setting controls the number of points used for each visualization, showing fewer details with better performance. Set to 0 to show the full data. The setting is only applied on initialization.",
        },
        "threads": {
            "type": t_int,
            "value": current_values["threads"],
            "min": 1,
            "max": config.CPU_COUNT,
            "name": "Number of threads (applies after restart)",
            "description": "Maximal number of threads used for reading save files. The new value is applied after restarting the dashboard program.",
        },
        "plot_height": {
            "type": t_int,
            "value": current_values["plot_height"],
            "min": 300,
            "max": 2000,
            "name": "Plot height (pixels)",
            "description": "Height of plots in the graph dashboard.",
        },
        "plot_width": {
            "type": t_int,
            "value": current_values["plot_width"],
            "min": 300,
            "max": 2000,
            "name": "Plot width (pixels)",
            "description": "Width of plots in the graph dashboard.",
        },
        "save_file_path": {
            "type": t_str,
            "value": current_values["save_file_path"],
            "name": "Save file path (applies after restart, submit empty to restore default)",
            "description": "This controls the path where the dashboard will look for new or updated Stellaris save files. If you leave this input empty, the value will be reset to the default value. The new value is applied after restarting the dashboard program.",
        },
        "mp_username": {
            "type": t_str,
            "value": current_values["mp_username"],
            "name": "Your Multiplayer username",
            "description": "",
        },
        "max_file_read_attempts": {
            "type": t_int,
            "value": current_values["max_file_read_attempts"],
            "name": "Maximum read attempts per save file",
            "min": 1,
            "max": 2000,
            "description": "Sometimes the dashboard may try to read a file before it is completely written to disk. The dashboard will attempt to read a bad zip file this many times (see bad save file delay setting below).",
        },
        "save_file_delay": {
            "type": t_float,
            "value": current_values["save_file_delay"],
            "name": "Bad save file delay in seconds (applies after restart)",
            "description": "This setting controls the delay between attempts to read a bad save file that is not completely written to disk.",
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
    config.CONFIG.apply_dict(settings)
    config.CONFIG.write_to_file()
    return redirect("/")
