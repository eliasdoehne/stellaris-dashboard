"""This file contains the code for the flask server hosting the visualizations and the event ledger."""
import functools
import logging
import flask
import itertools as it
from stellarisdashboard import datamodel

logger = logging.getLogger(__name__)

VERSION = "v4.3"


def parse_version(version: str):
    main_version, _, prerelease = version.lstrip("v").partition("-")
    result = []
    for v in main_version.split("."):
        try:
            result.append(int(v))
        except ValueError:
            pass
    if prerelease:
        result.append(prerelease)
    return result


def is_old_version(requested_version: str, actual_version=VERSION) -> bool:
    """Compares the requested version against the VERSION defined above."""
    try:
        actual_parsed = parse_version(actual_version)
        requested_parsed = parse_version(requested_version)
        for a, r in it.zip_longest(actual_parsed, requested_parsed):
            if a is None or a < r:
                return True
            elif r is None or a > r:
                return False
        return False
    except Exception:
        return False


def get_most_recent_date(session):
    most_recent_gs = (
        session.query(datamodel.GameState).order_by(datamodel.GameState.date.desc()).first()
    )
    if most_recent_gs is None:
        most_recent_date = 0
    else:
        most_recent_date = most_recent_gs.date
    return most_recent_date


@functools.lru_cache()
def preformat_history_url(text, game_id, a_class="textlink", **kwargs):
    href = flask.url_for("history_page", game_id=game_id, **kwargs)
    return f'<a class="{a_class}" href={href}>{text}</a>'
