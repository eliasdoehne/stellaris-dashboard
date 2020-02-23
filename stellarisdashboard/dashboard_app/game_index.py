"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import logging

from flask import render_template

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import (
    flask_app,
    utils,
)

logger = logging.getLogger(__name__)


@flask_app.route("/")
@flask_app.route("/checkversion/<version>/")
def index_page(version=None):
    """ Show a list of known games with database files.

    :param version: Used to check for updates.
    :return:
    """
    show_old_version_notice = False
    if config.CONFIG.check_version and version is not None:
        show_old_version_notice = utils.is_old_version(version)
    games = datamodel.get_available_games_dict().values()
    return render_template(
        "game_index.html",
        games=games,
        show_old_version_notice=show_old_version_notice,
        version=utils.VERSION_ID,
        update_version_id=version,
    )
