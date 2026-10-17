import logging

from stellarisdashboard.parsing import timeline


def _identify(gamestate):
    extractor = timeline.TimelineExtractor()
    extractor._gamestate_dict = gamestate
    return extractor._identify_player_country()


def test_player_country_present():
    assert _identify({"player": [{"name": "p", "country": 0}], "country": {0: {}}}) == 0


def test_player_country_missing_is_no_player():
    assert _identify({"player": [{"name": "unknown", "country": 0}], "country": {1: {}}}) is None


def test_player_country_not_a_dict_is_no_player():
    assert _identify({"player": [{"name": "p", "country": 0}], "country": {0: "none"}}) is None


def test_multiplayer_country_missing_is_no_player(monkeypatch):
    gamestate = {
        "player": [{"name": "a", "country": 0}, {"name": "b", "country": 1}],
        "country": {1: {}},
    }
    monkeypatch.setattr(timeline.config.CONFIG, "mp_username", "a")
    assert _identify(gamestate) is None


def test_missing_player_country_warns_once_per_game(caplog):
    timeline._warned_missing_player_games.clear()
    extractor = timeline.TimelineExtractor()
    extractor._gamestate_dict = {"player": [{"name": "p", "country": 0}], "country": {1: {}}}
    with caplog.at_level(logging.DEBUG, logger=timeline.logger.name):
        for game_id in ("a", "a", "b"):
            extractor._identify_player_country(game_id)
    levels = [r.levelno for r in caplog.records if "not in the gamestate" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG, logging.WARNING]
