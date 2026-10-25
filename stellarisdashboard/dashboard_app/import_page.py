"""The import page: bulk-import the existing save files of a game in the background."""

import logging

from flask import render_template, request, redirect

from stellarisdashboard import bulk_import, config
from stellarisdashboard.dashboard_app import flask_app
from stellarisdashboard.parsing import save_copies

logger = logging.getLogger(__name__)


def _started_response(error: str):
    response = flask_app.make_response(_status_fragment(error))
    if not error:
        # makes the failed saves list start polling, so it follows the job
        response.headers["HX-Trigger"] = "reprocess-started"
    return response


def _failed_saves_fragment():
    return render_template(
        "import_failed_saves.html",
        copies=save_copies.list_kept_copies(),
        job=bulk_import.get_current_job(),
    )


def _status_fragment(error: str = ""):
    return render_template(
        "import_status.html", job=bulk_import.get_current_job(), error=error
    )


@flask_app.route("/import/")
@flask_app.route("/import")
def import_page():
    return render_template(
        "import_page.html",
        save_path=config.CONFIG.save_file_path,
        games=bulk_import.list_save_games(),
        job=bulk_import.get_current_job(),
        error="",
        failed_saves=_failed_saves_fragment(),
    )


@flask_app.route("/import/plan/<game_id>")
def import_plan(game_id):
    try:
        plan = bulk_import.get_import_plan(game_id)
    except bulk_import.ImportRefused as e:
        return render_template("import_plan.html", plan=None, error=str(e))
    return render_template(
        "import_plan.html",
        plan=plan,
        error="",
        save_name_filter=config.CONFIG.save_name_filter,
        skip_saves=config.CONFIG.skip_saves,
    )


@flask_app.route("/import/start", methods=["POST"])
def start_import():
    error = ""
    try:
        bulk_import.start_import(request.form.get("game_id", ""))
    except bulk_import.ImportRefused as e:
        error = str(e)
    if request.headers.get("HX-Request"):
        return _started_response(error)
    return redirect("/import/")


@flask_app.route("/import/cancel", methods=["POST"])
def cancel_import():
    bulk_import.cancel_import()
    if request.headers.get("HX-Request"):
        return _status_fragment()
    return redirect("/import/")


@flask_app.route("/import/status")
def import_status():
    return _status_fragment()


@flask_app.route("/import/failed")
def import_failed_saves():
    return _failed_saves_fragment()


@flask_app.route("/import/reprocess", methods=["POST"])
def start_reprocess():
    error = ""
    try:
        bulk_import.start_reprocess(request.form.get("game_id") or None)
    except bulk_import.ImportRefused as e:
        error = str(e)
    if request.headers.get("HX-Request"):
        return _started_response(error)
    return redirect("/import/")
