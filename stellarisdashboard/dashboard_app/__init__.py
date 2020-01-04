"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import logging

import flask

from stellarisdashboard import config

logger = logging.getLogger(__name__)

# Initialize Flask, requests to /timeline/... are handled by the dash framework, all others via the flask routes defined below.
flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.DEBUG)

from stellarisdashboard.dashboard_app import (
    history_ledger,
    game_index,
    graph_ledger,
    settings,
)


def start_server():
    graph_ledger.start_dash_app(port=config.CONFIG.port)


if __name__ == "__main__":
    start_server()
