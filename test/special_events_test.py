from stellarisdashboard.datamodel import split_packed_description
from stellarisdashboard.parsing.timeline import NO_GAME_ID, _as_dict_list, _as_game_id


def test_as_game_id_accepts_only_real_ids():
    assert _as_game_id(3466) == 3466
    assert _as_game_id(0) == 0
    for value in (NO_GAME_ID, -1, True, "none", "12", None, 1.5, [1]):
        assert _as_game_id(value) is None


def test_as_dict_list_wraps_lone_entry_and_drops_junk():
    assert _as_dict_list({"a": 1}) == [{"a": 1}]
    assert _as_dict_list([{"a": 1}, "none", 3, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert _as_dict_list("none") == []
    assert _as_dict_list(None) == []


def test_split_packed_description():
    assert split_packed_description("site_ice_trauma|3") == ("site_ice_trauma", "3")
    assert split_packed_description("astral_rift.1100|a|b") == ("astral_rift.1100", "a|b")
    assert split_packed_description("r_dragon_trophy") == ("r_dragon_trophy", "")
