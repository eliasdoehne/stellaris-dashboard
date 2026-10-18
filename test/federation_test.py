import sqlalchemy

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline

ET = datamodel.HistoricalEventType
D = datamodel.date_to_days


def _setup():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    session = sqlalchemy.orm.sessionmaker(bind=engine)()
    game = datamodel.Game(game_name="g")
    countries = {
        i: datamodel.Country(
            game=game, country_id_in_game=i, country_type="default",
            country_name=f"c{i}", is_player=False,
        )
        for i in range(1, 6)
    }
    session.add_all([game, *countries.values()])
    session.flush()
    return session, game, countries


def _fed(members, start_date, associates=()):
    return {
        "name": {"key": "Fed"},
        "members": list(members),
        "associates": list(associates),
        "leader": members[0],
        "start_date": start_date,
    }


def _save(session, game, countries, date, federations):
    """Processes one simulated save on the given date."""
    gamestate = datamodel.GameState(game=game, date=D(date))
    session.add(gamestate)
    session.flush()
    processor = timeline.FederationProcessor()
    info = timeline.BasicGameInfo(
        game_id="g", date_in_days=D(date), player_country_id=None,
        other_players=set(), number_of_parsed_saves=0,
    )
    processor.initialize(game, {"federation": federations}, gamestate, info, session)
    processor.extract_data_from_gamestate({timeline.CountryProcessor.ID: countries})
    session.flush()


def _events(session, event_type):
    events = session.query(datamodel.HistoricalEvent).filter_by(event_type=event_type)
    return sorted((e.country.country_id_in_game, e.start_date_days) for e in events)


def _ids(countries):
    return sorted(c.country_id_in_game for c in countries)


def test_founding_since_last_save():
    session, game, countries = _setup()
    _save(session, game, countries, "2300.01.01", {})
    _save(session, game, countries, "2300.06.01", {0: _fed([1, 2], "2300.03.01")})
    start = D("2300.03.01")
    assert _events(session, ET.founded_federation) == [(1, start), (2, start)]
    fed = session.query(datamodel.Federation).one()
    assert (fed.start_date_days, fed.end_date_days) == (start, None)
    assert {m.start_date_days for m in fed.memberships} == {start}
    assert all(e.federation is fed for e in session.query(datamodel.HistoricalEvent))


def test_joins_leaves_and_role_changes():
    session, game, countries = _setup()
    start = "2300.03.01"
    _save(session, game, countries, "2300.01.01", {})
    _save(session, game, countries, "2300.04.01", {0: _fed([1, 2], start)})
    _save(session, game, countries, "2300.05.01", {0: _fed([1, 2, 3], start, [4])})
    _save(session, game, countries, "2300.06.01", {0: _fed([1, 3, 4], start)})
    assert _events(session, ET.joined_federation) == [
        (3, D("2300.05.01")), (4, D("2300.05.01")), (4, D("2300.06.01")),
    ]
    assert _events(session, ET.left_federation) == [(2, D("2300.06.01"))]
    fed = session.query(datamodel.Federation).one()
    assert _ids(fed.members_at(D("2300.04.15"))) == [1, 2]
    assert _ids(fed.members_at(D("2300.05.15"))) == [1, 2, 3]
    assert _ids(fed.members_at(D("2300.05.15"), associates=True)) == [4]
    assert _ids(fed.members_at(D("2300.07.01"))) == [1, 3, 4]
    assert fed.members_at(D("2300.07.01"), associates=True) == []
    two = [m for m in fed.memberships if m.country.country_id_in_game == 2]
    assert two[0].end_date_days == D("2300.06.01") - 1


def test_dissolution():
    session, game, countries = _setup()
    _save(session, game, countries, "2300.01.01", {})
    _save(session, game, countries, "2300.04.01", {0: _fed([1, 2], "2300.03.01")})
    _save(session, game, countries, "2300.05.01", {0: "none"})
    today = D("2300.05.01")
    assert _events(session, ET.dissolved_federation) == [(1, today), (2, today)]
    fed = session.query(datamodel.Federation).one()
    assert fed.end_date_days == today - 1
    assert all(m.end_date_days == today - 1 for m in fed.memberships)


def test_id_reuse_dissolves_old_and_founds_new():
    session, game, countries = _setup()
    _save(session, game, countries, "2300.01.01", {})
    _save(session, game, countries, "2300.04.01", {0: _fed([1, 2], "2300.03.01")})
    _save(session, game, countries, "2300.08.01", {0: _fed([3, 4], "2300.07.01")})
    today = D("2300.08.01")
    assert _events(session, ET.dissolved_federation) == [(1, today), (2, today)]
    assert _events(session, ET.founded_federation) == [
        (1, D("2300.03.01")), (2, D("2300.03.01")),
        (3, D("2300.07.01")), (4, D("2300.07.01")),
    ]
    old, new = session.query(datamodel.Federation).order_by(
        datamodel.Federation.start_date_days
    )
    assert old.end_date_days == today - 1 and new.end_date_days is None
    assert _ids(new.members_at(today)) == [3, 4]


def test_federation_predating_tracking_records_memberships_without_events():
    session, game, countries = _setup()
    _save(session, game, countries, "2300.01.01", {0: _fed([1, 2], "2250.01.01", [3])})
    assert session.query(datamodel.HistoricalEvent).count() == 0
    fed = session.query(datamodel.Federation).one()
    assert fed.start_date_days == D("2250.01.01")
    assert _ids(fed.members_at(D("2300.01.01"))) == [1, 2]
    assert _ids(fed.members_at(D("2300.01.01"), associates=True)) == [3]


def test_diplomacy_processor_no_longer_logs_formed_federation():
    session, game, countries = _setup()
    gamestate = datamodel.GameState(game=game, date=D("2300.01.01"))
    session.add(gamestate)
    keys = datamodel.DiplomaticRelation._dict_key_attr_mapping
    relation = datamodel.DiplomaticRelation(
        owner=countries[1], target=countries[2],
        **{attr: False for attr in keys.values()},
    )
    session.add(relation)
    for in_federation in (True, False):
        diplo = {i: {key: set() for key in keys} for i in countries}
        if in_federation:
            diplo[1]["federations"].add(2)
        processor = timeline.DiplomacyUpdatesProcessor()
        info = timeline.BasicGameInfo(
            game_id="g", date_in_days=D("2300.01.01"), player_country_id=None,
            other_players=set(), number_of_parsed_saves=0,
        )
        processor.initialize(game, {}, gamestate, info, session)
        processor.extract_data_from_gamestate(
            {
                timeline.DiplomacyDictProcessor.ID: {"diplomacy": diplo},
                timeline.DiplomaticRelationsProcessor.ID: {
                    i: ({2: relation} if i == 1 else {}) for i in countries
                },
                timeline.CountryProcessor.ID: countries,
                timeline.RulerEventProcessor.ID: {},
            }
        )
        assert relation.federation == in_federation
    assert _events(session, ET.formed_federation) == []
