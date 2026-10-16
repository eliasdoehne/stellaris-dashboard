from stellarisdashboard.parsing.timeline import NO_GAME_ID, _as_game_id


def test_as_game_id_accepts_only_real_ids():
    assert _as_game_id(3466) == 3466
    assert _as_game_id(0) == 0
    for value in (NO_GAME_ID, -1, True, "none", "12", None, 1.5, [1]):
        assert _as_game_id(value) is None
