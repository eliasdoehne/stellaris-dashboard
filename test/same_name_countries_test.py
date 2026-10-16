import pytest

from stellarisdashboard import config, datamodel, game_info
from stellarisdashboard.country_color_cache import GAME_COUNTRY_COLORS
from stellarisdashboard.dashboard_app import graph_ledger, visualization_data


def _country(id_in_game: int, name: str = "Mirrored Empire") -> datamodel.Country:
    return datamodel.Country(
        country_id=id_in_game + 1000,
        country_id_in_game=id_in_game,
        country_name=name,
        country_type="default",
    )


def _gamestate(date: int, *countries: datamodel.Country) -> datamodel.GameState:
    return datamodel.GameState(
        date=date,
        country_data=[
            datamodel.CountryData(
                country=c,
                country_id=c.country_id,
                date=date,
                owned_planets=10 * (i + 1),
                attitude_towards_player=datamodel.Attitude.friendly,
            )
            for i, c in enumerate(countries)
        ],
    )


@pytest.fixture
def same_name_countries(monkeypatch):
    # names are plain strings here; avoids needing the game's localization files
    monkeypatch.setattr(game_info, "render_name", lambda name: name)
    monkeypatch.setattr(config.CONFIG, "show_all_country_types", True)
    monkeypatch.setattr(config.CONFIG, "include_id_in_names", True)
    return _country(1), _country(2)


def _planet_count_container(countries):
    container = visualization_data.PlanetCountDataContainer(country_perspective=None)
    for date in (0, 360):
        container.extract_data_from_gamestate(_gamestate(date, *countries))
    return container


def test_same_name_countries_are_separate_series(same_name_countries):
    container = _planet_count_container(same_name_countries)

    traces = {
        key: (label, values) for key, label, _, values in container.iterate_traces()
    }
    assert set(traces) == {1, 2}
    assert traces[1] == ("Mirrored Empire (1)", [10, 10])
    assert traces[2] == ("Mirrored Empire (2)", [20, 20])


def test_same_name_countries_keep_separate_series_without_id_suffix(
    same_name_countries, monkeypatch
):
    monkeypatch.setattr(config.CONFIG, "include_id_in_names", False)
    container = _planet_count_container(same_name_countries)

    traces = list(container.iterate_traces())
    assert [key for key, _, _, _ in traces] == [1, 2]
    assert [label for _, label, _, _ in traces] == ["Mirrored Empire"] * 2
    assert [values for _, _, _, values in traces] == [[10, 10], [20, 20]]


def test_same_name_countries_get_distinct_colors(same_name_countries, monkeypatch):
    game_id = "same_name_test_game"
    colors = visualization_data.CountryColors()
    colors.country_id_to_primary_color = {1: (200, 0, 0), 2: (0, 0, 200)}
    monkeypatch.setitem(GAME_COUNTRY_COLORS, game_id, colors)

    assert visualization_data.get_color_vals(game_id, 1) == (200, 0, 0)
    assert visualization_data.get_color_vals(game_id, 2) == (0, 0, 200)
    assert graph_ledger.get_country_color(game_id, 1) == "rgba(200,0,0,1.0)"
    assert graph_ledger.get_country_color(game_id, 2) == "rgba(0,0,200,1.0)"
    # a country without a known color falls back to a stable per-ID color
    assert visualization_data.get_color_vals(
        game_id, 3
    ) == visualization_data.get_color_vals(game_id, 3)


def test_player_series_is_emphasized_with_id_in_names(
    same_name_countries, tmp_path, monkeypatch
):
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path)
    (tmp_path / "db").mkdir()
    game_id = "player_label_test"
    player = _country(0, name="Player Empire")
    player.is_player = True
    with datamodel.get_db_session(game_id, write=True) as session:
        session.add(player)
        session.commit()

    player_label = graph_ledger._get_player_legend_label(game_id)
    figure_data = [
        dict(name="Player Empire (0)", line={"color": "rgba(1,2,3,0.5)"}),
        dict(name="Mirrored Empire (1)", line={"color": "rgba(4,5,6,1.0)"}),
    ]
    emphasized = graph_ledger._emphasize_player_in_compact_data(
        figure_data, player_label
    )
    assert player_label == "Player Empire (0)"
    assert emphasized[-1]["name"] == "Player Empire (0)"
    assert emphasized[-1]["line"]["width"] == graph_ledger.COMPACT_PLAYER_WIDTH
