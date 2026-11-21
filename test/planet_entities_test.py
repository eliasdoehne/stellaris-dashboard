"""PlanetProcessor's districts, buildings, deposits and modifiers over a sequence of saves.

The stored rows of all changed planets are loaded with one query per table; the expected
rows below, including their primary keys, are what loading each planet's collections
one by one produced (the code before the bulk loads).
"""

import contextlib

import pytest
import sqlalchemy

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import timeline


def _districts(*specs):
    return {i: {"type": t, "level": level} for i, (t, level) in enumerate(specs)}


def _planet(name, districts=(), buildings=(), deposits=(), timed=(), permanent=()):
    return {
        "planet_class": "pc_continental",
        "name": {"key": name},
        "districts": list(districts),
        "buildings_cache": list(buildings),
        "deposits": list(deposits),
        "timed_modifier": [{"modifier": m, "days": days} for m, days in timed],
        "planet_modifier": list(permanent),
    }


def _gamestate(date, planets, districts, systems):
    return {
        "date": date,
        "galaxy": {},
        "country": {},
        "galactic_object": {sid: {"planet": pids} for sid, pids in systems.items()},
        "planets": {"planet": planets},
        "districts": districts,
        "buildings": {
            100: {"type": "building_capital"},
            101: {"type": "building_research_lab_1"},
            102: {"type": "building_research_lab_1"},
            103: {"type": "building_foundry_1"},
        },
        "deposit": {
            200: {"type": "d_energy_1"},
            201: {"type": "d_minerals_2"},
            202: {"type": "d_minerals_2"},
        },
    }


# planet 2 is listed in two systems; planet 3 is colonized in the second save
SAVES = [
    _gamestate(
        "2300.01.01",
        {
            1: _planet(
                "One",
                districts=[0, 1],
                buildings=[100],
                deposits=[200],
                timed=[("m_a", 100)],
                permanent=["pm_x"],
            ),
            2: _planet("Two", districts=[2], timed=[("m_b", 50)]),
        },
        _districts(
            ("district_city", 2), ("district_generator", 1), ("district_mining", 1)
        ),
        {10: [1, 2], 11: [2]},
    ),
    _gamestate(
        "2300.02.01",
        {
            # generator removed, city grows, farming new; a lab added; m_a expires later
            1: _planet(
                "One",
                districts=[0, 3],
                buildings=[100, 101],
                deposits=[200],
                timed=[("m_a", 70)],
                permanent=["pm_x"],
            ),
            # m_b removed: the modifier with the highest id, deleted before any new one
            2: _planet("Two", districts=[2]),
            3: _planet(
                "Three", districts=[4], deposits=[201, 202], timed=[("m_c", 30)]
            ),
        },
        _districts(
            ("district_city", 3),
            ("district_generator", 1),
            ("district_mining", 1),
            ("district_farming", 2),
            ("district_city", 1),
        ),
        {10: [1, 2], 11: [2, 3]},
    ),
    _gamestate(
        "2300.03.01",
        {
            # m_a removed and m_d new on the same planet; the lab count doubles
            1: _planet(
                "One",
                districts=[0, 3],
                buildings=[100, 101, 102],
                deposits=[],
                timed=[("m_d", 10)],
                permanent=["pm_x"],
            ),
            2: _planet("Two", districts=[2], buildings=[103], timed=[("m_b", 20)]),
            # m_c removed after new modifiers of other planets
            3: _planet("Three", districts=[4], deposits=[201]),
        },
        _districts(
            ("district_city", 3),
            ("district_generator", 1),
            ("district_mining", 2),
            ("district_farming", 2),
            ("district_city", 1),
        ),
        {10: [1, 2], 11: [2, 3]},
    ),
]

# per table: (primary key, planet id, description text, count or expiry date)
EXPECTED = {
    "districts": [
        (1, 1, "district_city", 3),
        (2, 1, "district_generator", 0),
        (3, 2, "district_mining", 2),
        (4, 1, "district_farming", 2),
        (5, 3, "district_city", 1),
    ],
    "buildings": [
        (1, 1, "building_capital", 1),
        (2, 1, "building_research_lab_1", 2),
        (3, 2, "building_foundry_1", 1),
    ],
    "deposits": [(1, 1, "d_energy_1", 0), (2, 3, "d_minerals_2", 1)],
    # save 1 added m_a 1, pm_x 2, m_b 3. In save 2, m_b was deleted before any new
    # modifier, so SQLite gave its id to m_c (3); m_d is 4, not 5
    "modifiers": [(2, 1, "pm_x", None), (4, 1, "m_d", 10), (5, 2, "m_b", 20)],
}


class _Systems(timeline.AbstractGamestateDataProcessor):
    """Stands in for SystemProcessor: one System row per galactic object."""

    ID = timeline.SystemProcessor.ID

    def extract_data_from_gamestate(self, dependencies):
        systems = {
            s.system_id_in_game: s for s in self._session.query(datamodel.System)
        }
        for system_id in self._gamestate_dict["galactic_object"]:
            if system_id not in systems:
                systems[system_id] = datamodel.System(
                    game=self._db_game, system_id_in_game=system_id, name="{}"
                )
                self._session.add(systems[system_id])
        self.systems = systems

    def data(self):
        return {"systems_by_ingame_id": self.systems}


_ENTITY_TABLES = {
    "districts": (datamodel.PlanetDistrict, datamodel.PlanetDistrict.count),
    "buildings": (datamodel.PlanetBuilding, datamodel.PlanetBuilding.count),
    "deposits": (datamodel.PlanetDeposit, datamodel.PlanetDeposit.count),
    "modifiers": (datamodel.PlanetModifier, datamodel.PlanetModifier.expiry_date),
}


def _stored_rows(session, model, value_column):
    pk = sqlalchemy.inspect(model).primary_key[0]
    statement = (
        sqlalchemy.select(
            pk, model.planet_id, datamodel.SharedDescription.text, value_column
        )
        .join(datamodel.SharedDescription)
        .order_by(pk)
    )
    return [tuple(row) for row in session.execute(statement)]


class _CollectionsMatchDatabase(timeline.AbstractGamestateDataProcessor):
    """Runs after PlanetProcessor in the same session: what a later reader of the planets'
    collections would see must be what is in the database."""

    ID = "check_collections"
    DEPENDENCIES = [timeline.PlanetProcessor.ID]
    seen = []

    def extract_data_from_gamestate(self, dependencies):
        planets = dependencies[timeline.PlanetProcessor.ID]
        for attribute, (model, value_column) in _ENTITY_TABLES.items():
            # the statement flushes first (pending_flush), as the lazy loads below may
            stored = sorted(
                (planet_id, text, value)
                for _, planet_id, text, value in _stored_rows(
                    self._session, model, value_column
                )
            )
            in_memory = sorted(
                (
                    planet.planet_id,
                    entity.db_description.text,
                    getattr(entity, value_column.key),
                )
                for planet in planets.values()
                for entity in getattr(planet, attribute)
            )
            assert in_memory == stored, attribute
            for planet in planets.values():
                for entity in getattr(planet, attribute):
                    assert not sqlalchemy.inspect(entity).deleted
        _CollectionsMatchDatabase.seen.append(self._basic_info.date_in_days)


@pytest.fixture
def extractor(tmp_path, monkeypatch):
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    datamodel.Base.metadata.create_all(engine)
    session_factory = sqlalchemy.orm.sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_db_session(game_id, write=False):
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(datamodel, "get_db_session", get_db_session)
    monkeypatch.setattr(config.CONFIG, "debug_mode", True)
    extractor = timeline.TimelineExtractor()
    extractor.session_factory = session_factory
    yield extractor
    engine.dispose()


def _all_rows(session):
    return {
        attribute: _stored_rows(session, model, value_column)
        for attribute, (model, value_column) in _ENTITY_TABLES.items()
    }


def test_planet_entities_over_three_saves(extractor, monkeypatch):
    monkeypatch.setattr(
        extractor,
        "_data_processors",
        lambda: iter(
            [_Systems(), timeline.PlanetProcessor(), _CollectionsMatchDatabase()]
        ),
    )
    _CollectionsMatchDatabase.seen = []
    for gamestate in SAVES:
        extractor.process_gamestate("g", gamestate)
        assert extractor.failures == []
    assert len(_CollectionsMatchDatabase.seen) == 3

    with extractor.session_factory() as session:
        rows = _all_rows(session)
        planet_ids = {
            p.planet_id_in_game: p.planet_id for p in session.query(datamodel.Planet)
        }
    assert planet_ids == {1: 1, 2: 2, 3: 3}
    assert rows == EXPECTED
