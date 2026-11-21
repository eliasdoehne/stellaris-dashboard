"""Imports of short synthetic save sequences through the processors that use StoredRows.

Each sequence is imported twice: as is, and with every StoredRows replaced by QueryRows, which
answers each lookup with the query it replaces (sent through the pending_flush listener, so
after a flush as before). The databases must be identical, primary keys included. The
sequences cover special-country events, a government change, a ruler change, joining and
leaving the galactic community in consecutive saves, a country listed twice, edicts, traditions,
technologies, factions, envoys and councilors, and an older save imported after newer ones.
"""

import contextlib
import copy
import json

import pytest
import sqlalchemy

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import import_context, timeline

Event = datamodel.HistoricalEvent


class QueryRows:
    """StoredRows' interface answered by a query per lookup (the code before StoredRows)."""

    def __init__(self, session, key, where=None, order_by=None):
        self._session = session
        self._key = list(key)
        self._where = dict(where or {})
        self._order = order_by
        self._model = (
            self._key[0].class_ if self._key else next(iter(self._where)).class_
        )

    @staticmethod
    def _equals(attribute, value):
        if isinstance(value, (set, frozenset)):
            return attribute.in_(value)
        return (
            attribute == value
        )  # IS NULL for None, for columns and relationships alike

    def _query(self, keys, at_most=None):
        query = self._session.query(self._model).filter(
            *(self._equals(a, v) for a, v in self._where.items())
        )
        query = query.filter(
            sqlalchemy.or_(
                *(
                    sqlalchemy.and_(
                        *(self._equals(a, v) for a, v in zip(self._key, key))
                    )
                    for key in keys
                )
            )
        )
        if at_most is not None:
            query = query.filter(self._order <= at_most)
        return query

    def latest(self, *key, at_most=None):
        return self.latest_of([key], at_most=at_most)

    def latest_of(self, keys, at_most=None):
        (primary_key,) = sqlalchemy.inspect(self._model).primary_key
        return (
            self._query(list(keys), at_most)
            .order_by(self._order.desc(), primary_key)
            .first()
        )

    def one_or_none(self, *key):
        return self._query([key]).one_or_none()

    def exists(self, *key):
        return self._query([key]).first() is not None

    def close(self):
        pass


def _country(name, country_type="default", **extra):
    country = {
        "name": {"key": name},
        "type": country_type,
        "flag": {"colors": ["red", "blue"]},
    }
    country.update(extra)
    return country


def _government(civics, ethics=("ethic_a",)):
    return {
        "ethos": {"ethic": list(ethics)},
        "government": {
            "civics": list(civics),
            "authority": "auth_democratic",
            "type": "gov_x",
        },
    }


def _tech(in_progress=None, completed=()):
    status = {"technology": list(completed), "level": [1] * len(completed)}
    if in_progress:
        status["physics_queue"] = [{"technology": in_progress, "date": "2200.01.01"}]
    return {"tech_status": status}


def _save(date, countries, leaders=None, **extra):
    gamestate = {
        "date": date,
        "galaxy": {},
        "country": countries,
        "leaders": leaders or {},
    }
    gamestate.update(extra)
    return gamestate


def _leader(country, leader_class="ruler", **extra):
    leader = {"country": country, "class": leader_class}
    leader.update(extra)
    return leader


def _edicts(*names):
    return {"edicts": [{"edict": name, "date": "2230.01.01"} for name in names]}


SAVES = [
    _save(
        "2200.01.01",
        {
            1: _country(
                "One",
                ruler=10,
                traditions=["tr_a"],
                ascension_perks=["ap_a"],
                **_edicts("edict_a", "edict_a"),  # listed twice: one event
                **_government(["civic_a"]),
                **_tech(in_progress="tech_a"),
            ),
            2: _country("Two", ruler=12, **_government(["civic_b"]), **_tech("tech_b")),
            3: _country(
                "Primitives",
                "primitive",
                ruler=30,
                traditions=["tr_a"],
                **_edicts(
                    "edict_a", "edict_a"
                ),  # routine events of a special country: dropped
                **_government(["civic_p"]),
                **_tech(in_progress="tech_a"),
            ),
        },
        leaders={
            10: _leader(1),
            12: _leader(2),
            30: _leader(3),
            20: _leader(
                1, "envoy", location={"assignment": "improve_relations", "id": 2}
            ),
            40: _leader(3, "envoy", location={"assignment": "galactic_community"}),
        },
        galactic_community={"members": [1], "council": [1]},
        pop_factions={
            5: {"country": 1, "name": {"key": "F"}, "type": "f_a", "leader": 10}
        },
        council_positions={
            "council_positions": {
                1: {"country": 1, "leader": 10, "type": "councilor_a"},
                2: {"country": 3, "leader": 30, "type": "councilor_a"},
            }
        },
    ),
    _save(
        "2201.01.01",
        {
            # new ruler, reformed government, completed and new technology
            1: _country(
                "One",
                ruler=11,
                traditions=["tr_a", "tr_b"],
                **_edicts("edict_a"),
                **_government(["civic_a", "civic_c"]),
                **_tech(in_progress="tech_c", completed=["tech_a"]),
            ),
            2: _country("Two", ruler=12, **_government(["civic_b"]), **_tech("tech_b")),
            3: _country(
                "Primitives",
                "primitive",
                ruler=31,
                **_edicts("edict_a"),
                **_government(["civic_p", "civic_q"]),
                **_tech(completed=["tech_a"]),
            ),
        },
        leaders={
            10: _leader(1),
            11: _leader(1),
            12: _leader(2),
            30: _leader(3),
            31: _leader(3),
            20: _leader(1, "envoy", location={"assignment": "idle"}),
            40: _leader(3, "envoy", location={"assignment": "galactic_community"}),
        },
        # One leaves, Two joins; Two listed twice
        galactic_community={"members": [2, 2], "council": [2]},
        pop_factions={
            5: {"country": 1, "name": {"key": "F"}, "type": "f_a", "leader": 11}
        },
        council_positions={
            "council_positions": {
                1: {"country": 1, "leader": 11, "type": "councilor_a"},
                2: {
                    "country": 1,
                    "leader": 10,
                    "type": "councilor_a",
                },  # same type twice
                3: {"country": 3, "leader": 31, "type": "councilor_a"},
            }
        },
    ),
    _save(
        "2202.01.01",
        {
            1: _country(
                "One",
                ruler=11,
                **_edicts("edict_a", "edict_b"),
                **_government(["civic_a", "civic_c"]),
                **_tech(completed=["tech_a", "tech_c"]),
            ),
            2: _country("Two", ruler=10, **_government(["civic_b"], ["ethic_b"])),
            3: _country(
                "Primitives", "primitive", ruler=31, **_government(["civic_p"])
            ),
        },
        leaders={
            10: _leader(2),
            11: _leader(1),
            12: _leader(2),
            31: _leader(3),
            20: _leader(1, "envoy", location={"assignment": "harm_relations", "id": 2}),
        },
        # One rejoins the community the save after it left
        galactic_community={"members": [1, 2], "council": [1]},
        pop_factions={
            5: {"country": 1, "name": {"key": "F"}, "type": "f_a", "leader": 11}
        },
        council_positions={"council_positions": {}},
    ),
]


class _Leaders(timeline.AbstractGamestateDataProcessor):
    """Stand-in for LeaderProcessor: a leader per gamestate leader, with its country."""

    ID = timeline.LeaderProcessor.ID
    DEPENDENCIES = [timeline.CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries = dependencies[timeline.CountryProcessor.ID]
        known = {l.leader_id_in_game: l for l in self._session.query(datamodel.Leader)}
        self._leaders = {}
        for leader_id, leader_dict in sorted(self._gamestate_dict["leaders"].items()):
            leader = known.get(leader_id)
            if leader is None:
                leader = datamodel.Leader(
                    game=self._db_game, leader_id_in_game=leader_id
                )
                self._session.add(leader)
            leader.country = countries[leader_dict["country"]]
            leader.is_active = True
            self._leaders[leader_id] = leader
        for leader_id, leader in known.items():
            if leader_id not in self._leaders:
                leader.is_active = False

    def data(self):
        return self._leaders


class _Planets(timeline.AbstractGamestateDataProcessor):
    ID = timeline.PLANET_PROCESSOR_ID

    def extract_data_from_gamestate(self, dependencies):
        pass

    def data(self):
        return {}


def _processors():
    yield timeline.CountryProcessor()
    yield _Leaders()
    yield _Planets()
    yield timeline.RulerEventProcessor()
    yield timeline.CouncilProcessor()
    yield timeline.GovernmentProcessor()
    yield timeline.FactionProcessor()
    yield timeline.GalacticCommunityProcessor()
    yield timeline.ScientistEventProcessor()
    yield timeline.EnvoyEventProcessor()


def _import(tmp_path, monkeypatch, name, saves, use_queries):
    # one in-memory database for all sessions of the sequence
    engine = sqlalchemy.create_engine("sqlite://", poolclass=sqlalchemy.pool.StaticPool)
    datamodel.Base.metadata.create_all(engine)
    session_factory = sqlalchemy.orm.sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_db_session(game_id, write=False):
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    with monkeypatch.context() as patch:
        patch.setattr(datamodel, "get_db_session", get_db_session)
        patch.setattr(config.CONFIG, "debug_mode", True)
        if use_queries:
            patch.setattr(
                import_context.ImportContext,
                "stored_rows",
                lambda self, key, where=None, order_by=None: QueryRows(
                    self._session, key, where, order_by
                ),
            )
        extractor = timeline.TimelineExtractor()
        patch.setattr(extractor, "_data_processors", _processors)
        for gamestate in saves:
            extractor.process_gamestate("g", copy.deepcopy(gamestate))
            assert extractor.failures == []
    with session_factory() as session:
        tables = {}
        for table in datamodel.Base.metadata.sorted_tables:
            columns = [
                c for c in table.columns if c.name != "db_last_updated"
            ]  # the import time
            rows = session.execute(
                sqlalchemy.select(*columns).order_by(*table.primary_key)
            ).all()
            tables[table.name] = [tuple(row) for row in rows]
        events = [
            (
                e.event_type.name,
                e.country.country_name if e.country else None,
                e.start_date_days,
                e.end_date_days,
            )
            for e in session.query(Event).order_by(Event.historical_event_id)
        ]
    return tables, events


@pytest.mark.parametrize(
    "order",
    [[0, 1, 2], [0, 2, 1], [2, 0, 1]],
    ids=["in order", "older last", "oldest last"],
)
def test_stored_rows_import_equals_query_import(tmp_path, monkeypatch, order):
    saves = [SAVES[i] for i in order]
    stored, events = _import(tmp_path, monkeypatch, "stored", saves, use_queries=False)
    queried, _ = _import(tmp_path, monkeypatch, "queried", saves, use_queries=True)
    assert stored == queried
    assert events  # the sequence did produce events


def test_sequence_events(tmp_path, monkeypatch):
    _, events = _import(tmp_path, monkeypatch, "stored", SAVES, use_queries=False)
    by_type = {}
    for event_type, country, start, end in events:
        name = country and json.loads(country)["key"]
        by_type.setdefault(event_type, []).append((name, start, end))
    # no routine events of the special country (edicts, traditions, rulers, councilors, tech)
    for event_type in (
        "edict",
        "tradition",
        "ruled_empire",
        "councilor",
        "researched_technology",
    ):
        assert all(c != "Primitives" for c, _, _ in by_type[event_type]), event_type
    # the edict listed twice in the first save is one event, and is not added again later
    assert by_type["edict"] == [("One", 0, 10800), ("One", 720, 10800)]
    # government changes in the second save (One, Primitives) and third (Two, Primitives)
    assert by_type["government_reform"] == [
        ("One", 360, 360),
        ("Primitives", 360, 360),
        ("Two", 720, 720),
        ("Primitives", 720, 720),
    ]
    # One joined, left the save after and rejoined the save after that; Two, listed twice, joined once
    assert by_type["joined_galactic_community"] == [
        ("One", 0, 360),
        ("Two", 360, None),
        ("One", 720, None),
    ]
    assert by_type["left_galactic_community"] == [("One", 360, 720)]
    # One's ruler changed in the second save: the first ruler's event ends the day before
    assert by_type["ruled_empire"][0] == ("One", 0, 359)
    # the technology researched in the first save ends when it is completed in the second
    assert ("One", 0, 359) in by_type["researched_technology"]
