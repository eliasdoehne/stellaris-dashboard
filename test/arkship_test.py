import copy

import pytest

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import save_format, timeline

# Minimal 4.5 gamestate with one real planet (colony 0) and one nomad arkship colony (colony 1)
GAMESTATE = {
    "date": "2300.01.01",
    "galactic_object": {10: {"planet": [3]}},
    "planets": {
        "planet": {
            3: {"planet_class": "pc_continental", "name": {"key": "Earth"}, "colony": 0}
        }
    },
    "colony": {
        0: {"carrier": {"type": "planet", "reference": 3}, "stability": 50.0},
        1: {
            "carrier": {"type": "ship", "reference": 12},
            "stability": 61.5,
            "free_amenities": 2.0,
            "free_housing": 3.0,
        },
    },
    "ships": {12: {"fleet": 20}},
    "fleet": {20: {"name": {"key": "Ark One"}, "ships": [12]}},
    "country": {0: {"owned_planets": [0, 1], "capital": 1}},
    "pop_groups": {
        100: {
            "planet": 1,
            "size": 10,
            "happiness": 0.5,
            "crime": 1.0,
            "power": 2.0,
            "key": {"species": 0, "category": "worker"},
        },
    },
}
ARKSHIP_ID = save_format.arkship_planet_id(1)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path)
    (tmp_path / "db").mkdir()
    with datamodel.get_db_session("arkship_test", write=True) as session:
        yield session
    # the cache would otherwise hand descriptions of this session to later tests
    timeline._shared_description_cache.clear()


class Save:
    """Runs processors the way TimelineExtractor does, for a gamestate at a given date."""

    def __init__(self, session, gamestate, date_in_days):
        self.session = session
        self.gamestate = gamestate
        self.game = session.query(datamodel.Game).first()
        if self.game is None:
            self.game = datamodel.Game(
                game_name="arkship_test", player_country_name="Test"
            )
            session.add(self.game)
        self.gs = datamodel.GameState(game=self.game, date=date_in_days)
        session.add(self.gs)
        self.info = timeline.BasicGameInfo("arkship_test", date_in_days, 0, set(), 0)
        timeline._shared_description_cache.clear()

    def run(self, processor, dependencies=None):
        processor.initialize(
            self.game, self.gamestate, self.gs, self.info, self.session
        )
        processor.extract_data_from_gamestate(dependencies or {})
        self.session.flush()
        return processor.data()

    def planets(self):
        system = (
            self.session.query(datamodel.System).filter_by(system_id_in_game=10).first()
        )
        if system is None:
            system = datamodel.System(
                game=self.game, system_id_in_game=10, name="{}", star_class="sc_g"
            )
            self.session.add(system)
            self.session.flush()
        systems = {"systems_by_ingame_id": {10: system}}
        planets = self.run(
            timeline.PlanetProcessor(), {timeline.SystemProcessor.ID: systems}
        )
        self.run(
            timeline.PlanetUpdateProcessor(),
            {
                timeline.PlanetProcessor.ID: planets,
                timeline.SectorColonyEventProcessor.ID: None,
            },
        )
        return planets


def arkship_rows(session):
    return (
        session.query(datamodel.Planet)
        .filter(datamodel.Planet.planet_id_in_game < 0)
        .all()
    )


def test_arkship_planet_created_and_updated_not_duplicated(session):
    planets = Save(session, GAMESTATE, 100).planets()
    assert set(planets) == {3, ARKSHIP_ID}
    ark = planets[ARKSHIP_ID]
    assert ark.planet_class == save_format.ARKSHIP_PLANET_CLASS
    assert ark.system is None
    assert ark.planet_name == '{"key": "Ark One"}'
    assert planets[3].system is not None
    session.commit()

    second = copy.deepcopy(GAMESTATE)
    second["fleet"][20]["name"] = {"key": "Ark Prime"}
    planets = Save(session, second, 130).planets()
    session.commit()
    rows = arkship_rows(session)
    assert len(rows) == 1
    assert rows[0] is planets[ARKSHIP_ID]
    assert rows[0].planet_name == '{"key": "Ark Prime"}'
    assert session.query(datamodel.Planet).count() == 2


def test_arkship_pseudo_planet_is_dropped_with_its_colony(session):
    Save(session, GAMESTATE, 100).planets()
    gone = copy.deepcopy(GAMESTATE)
    del gone["colony"][1]
    planets = Save(session, gone, 130).planets()
    # the old row is kept for history, and left alone
    assert planets[ARKSHIP_ID].planet_class == save_format.ARKSHIP_PLANET_CLASS


def _country(session, save):
    country = datamodel.Country(
        game=save.game, country_id_in_game=0, country_name="Nomads"
    )
    session.add(country)
    session.flush()
    return country


def _capital_relocations(session):
    return (
        session.query(datamodel.HistoricalEvent)
        .filter_by(event_type=datamodel.HistoricalEventType.capital_relocation)
        .all()
    )


def test_arkship_is_capital_without_spurious_relocations(session):
    save = Save(session, GAMESTATE, 100)
    planets = save.planets()
    country = _country(session, save)

    for date in (100, 130, 160):
        save = Save(session, GAMESTATE, date)
        ruler_processor = timeline.RulerEventProcessor()
        ruler_processor.initialize(save.game, GAMESTATE, save.gs, save.info, session)
        ruler_processor._planet_by_ingame_id = planets
        capital = ruler_processor._history_add_or_update_capital(
            country, None, GAMESTATE["country"][0]
        )
        session.flush()
        assert capital is None  # the method records the capital on the country
        assert country.capital is planets[ARKSHIP_ID]
    assert _capital_relocations(session) == []


def test_relocating_capital_to_arkship_adds_one_event_without_system(session):
    save = Save(session, GAMESTATE, 100)
    planets = save.planets()
    country = _country(session, save)
    country.capital = planets[3]

    ruler_processor = timeline.RulerEventProcessor()
    ruler_processor.initialize(save.game, GAMESTATE, save.gs, save.info, session)
    ruler_processor._planet_by_ingame_id = planets
    for _ in range(2):
        ruler_processor._history_add_or_update_capital(
            country, None, GAMESTATE["country"][0]
        )
        session.flush()
    events = _capital_relocations(session)
    assert len(events) == 1
    assert events[0].planet is planets[ARKSHIP_ID]
    assert events[0].system is None


def test_arkship_planet_stats(session):
    save = Save(session, GAMESTATE, 100)
    planets = save.planets()
    country = _country(session, save)
    country_data = datamodel.CountryData(country=country, game_state=save.gs, date=100)
    species = datamodel.Species(game=save.game, species_id_in_game=0, species_name="{}")
    session.add_all([country_data, species])
    session.flush()

    save.run(
        timeline.PopStatsProcessor(),
        {
            timeline.CountryProcessor.ID: {0: country},
            timeline.CountryDataProcessor.ID: {0: country_data},
            timeline.SpeciesProcessor.ID: ({0: species}, set()),
            timeline.FactionProcessor.ID: {},
            timeline.PlanetProcessor.ID: planets,
        },
    )
    (stats,) = session.query(datamodel.PlanetStats).all()
    assert stats.planet is planets[ARKSHIP_ID]
    assert stats.pop_count == 10
    assert stats.stability == 61.5
    assert stats.free_amenities == 2.0
    assert stats.free_housing == 3.0
