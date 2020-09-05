"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import logging

from pkg_resources import parse_version

from stellarisdashboard import datamodel

logger = logging.getLogger(__name__)

VERSION_ID = "v1.1"


def is_old_version(requested_version: str) -> bool:
    """Compares the requested version against the VERSION_ID defined above.

    :param requested_version: The version of the dashboard requested by the URL.
    :return:
    """
    try:
        return parse_version(VERSION_ID) < parse_version(requested_version)
    except Exception:
        return False


def get_most_recent_date(session):
    most_recent_gs = (
        session.query(datamodel.GameState)
        .order_by(datamodel.GameState.date.desc())
        .first()
    )
    if most_recent_gs is None:
        most_recent_date = 0
    else:
        most_recent_date = most_recent_gs.date
    return most_recent_date
