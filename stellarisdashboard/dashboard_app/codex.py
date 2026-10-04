"""Flask route for the Codex — a browsable index of a game's in-game entities.

The event ledger is organized around *events*; reaching a specific empire or war
means scrolling its feed or following a cross-link. The Codex is the complementary
view: an organized, at-a-glance list of the entities themselves, each linking to
its filtered ledger page. It is reachable from the sidebar.

This is an initial, deliberately small version (empires + wars) so the concept can
be evaluated for usefulness before expanding to other entity kinds.
"""
import logging

from flask import render_template

from stellarisdashboard import config, datamodel, game_info
from stellarisdashboard.dashboard_app import flask_app, utils

logger = logging.getLogger(__name__)


@flask_app.route("/codex")
@flask_app.route("/codex/<game_id>")
def codex_page(game_id=""):
    matches = datamodel.get_known_games(game_id)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    if game_id not in games_dict:
        # known DB file, but not a fully-available game (e.g. an empty/invalid db)
        logger.warning(f"Game {game_id} is not available")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    country = games_dict[game_id]["country_name"]

    with datamodel.get_db_session(game_id) as session:
        current_date = datamodel.days_to_date(utils.get_most_recent_date(session))
        empires = _gather_empires(session, game_id)
        wars = _gather_wars(session, game_id)

    return render_template(
        "codex_page.html",
        game_name=game_id,
        country=country,
        current_date=current_date,
        empires=empires,
        wars=wars,
    )


def _gather_empires(session, game_id):
    """Empires the player can see, player's own first, then alphabetical.

    Mirrors the ledger's country visibility rules: skip country types the
    ledger hides (unless ``show_all_country_types``) and skip hidden countries
    (un-met / other-player, per ``show_everything`` / ``hide_other_players``)."""
    empires = []
    for c in session.query(datamodel.Country).all():
        if not config.CONFIG.show_all_country_types and not c.is_real_country():
            continue
        if c.is_hidden_country():
            continue
        empires.append(c)
    empires.sort(key=lambda c: (not c.is_player, c.rendered_name.lower()))
    return [
        dict(
            link=utils.preformat_history_url(
                c.rendered_name, country=c.country_id, game_id=game_id
            ),
            country_type=game_info.convert_id_to_name(c.country_type),
            is_player=bool(c.is_player),
        )
        for c in empires
    ]


def _gather_wars(session, game_id):
    """All wars in the game, earliest first."""
    wars = (
        session.query(datamodel.War)
        .order_by(datamodel.War.start_date_days.asc())
        .all()
    )
    return [
        dict(
            link=utils.preformat_history_url(
                w.rendered_name, war=w.war_id, game_id=game_id
            ),
            start_date=datamodel.days_to_date(w.start_date_days),
            end_date=(
                datamodel.days_to_date(w.end_date_days)
                if w.end_date_days is not None
                else None
            ),
        )
        for w in wars
    ]
