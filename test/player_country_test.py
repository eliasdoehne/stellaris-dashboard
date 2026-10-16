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
