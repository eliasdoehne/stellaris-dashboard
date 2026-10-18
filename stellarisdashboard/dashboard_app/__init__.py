"""This file contains the code for the flask server hosting the visualizations and the event ledger."""

import logging
import threading

import flask

from stellarisdashboard import config, datamodel, game_info

logger = logging.getLogger(__name__)

# Initialize Flask, requests to /timeline/... are handled by the dash framework, all others via the flask routes defined below.
flask_app = flask.Flask(__name__, static_folder="assets")
flask_app.logger.setLevel(logging.DEBUG)

from stellarisdashboard.dashboard_app import (
    history_ledger,
    game_index,
    graph_ledger,
    galaxy_map,
    codex,
    settings,
    import_page,
)


def warm_up():
    """Load game text and migrate existing databases, so the first request is fast.

    Requests arriving earlier still work: they use the same lazy, lock-guarded
    initialisers and simply wait for the loading that is already in progress.
    """
    try:
        game_info.get_global_renderer()
    except Exception:
        logger.exception("Failed to warm up game text, it will be loaded on first use")
    try:
        datamodel.warm_up_databases()
    except Exception:
        logger.exception(
            "Failed to warm up databases, they will be opened on first use"
        )


def start_server():
    threading.Thread(target=warm_up, name="warm-up", daemon=True).start()
    graph_ledger.start_dash_app(host=config.CONFIG.host, port=config.CONFIG.port)


if __name__ == "__main__":
    start_server()
