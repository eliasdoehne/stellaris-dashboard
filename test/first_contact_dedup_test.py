import logging

import sqlalchemy

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline

ET = datamodel.HistoricalEventType
DAY = datamodel.date_to_days("2300.01.01")


def _setup():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    session = sqlalchemy.orm.sessionmaker(bind=engine)()
    game = datamodel.Game(game_name="g")
    gamestate = datamodel.GameState(game=game, date=DAY)
    countries = {
        i: datamodel.Country(
            game=game, country_id_in_game=i, country_type="default",
            country_name=f"c{i}", is_player=False,
        )
        for i in (1, 2)
    }
    session.add_all([game, gamestate, *countries.values()])
    session.flush()
    return session, game, gamestate, countries


def _process(session, game, gamestate, countries, communicating):
    """Runs the diplomacy processor with communications on (or off) from country 1 to 2."""
    relation = (
        session.query(datamodel.DiplomaticRelation)
        .filter_by(owner=countries[1], target=countries[2])
        .one_or_none()
    )
    if relation is None:
        relation = datamodel.DiplomaticRelation(
            owner=countries[1], target=countries[2],
            **{attr: False for attr in datamodel.DiplomaticRelation._dict_key_attr_mapping.values()},
        )
        session.add(relation)
    relations = {1: {2: relation}, 2: {}}
    keys = datamodel.DiplomaticRelation._dict_key_attr_mapping
    diplo = {i: {key: set() for key in keys} for i in countries}
    if communicating:
        diplo[1]["communations"].add(2)
    processor = timeline.DiplomacyUpdatesProcessor()
    info = timeline.BasicGameInfo(
        game_id="g", date_in_days=DAY, player_country_id=None,
        other_players=set(), number_of_parsed_saves=0,
    )
    processor.initialize(game, {}, gamestate, info, session)
    processor.extract_data_from_gamestate(
        {
            timeline.DiplomacyDictProcessor.ID: {"diplomacy": diplo},
            timeline.DiplomaticRelationsProcessor.ID: relations,
            timeline.CountryProcessor.ID: countries,
            timeline.RulerEventProcessor.ID: {},
        }
    )
    session.flush()


def _events(session, event_type):
    return session.query(datamodel.HistoricalEvent).filter_by(event_type=event_type).all()


def test_communications_without_a_process_log_first_contact():
    session, game, gamestate, countries = _setup()
    _process(session, game, gamestate, countries, communicating=True)
    assert len(_events(session, ET.first_contact)) == 1


def test_communications_covered_by_a_process_log_no_first_contact(caplog):
    session, game, gamestate, countries = _setup()
    # the process is stored in the opposite direction of the communications flag
    session.add(
        datamodel.HistoricalEvent(
            event_type=ET.first_contact_process, country=countries[2],
            target_country=countries[1], start_date_days=DAY - 100,
        )
    )
    session.flush()
    _process(session, game, gamestate, countries, communicating=True)
    assert _events(session, ET.first_contact) == []

    with caplog.at_level(logging.WARNING):
        _process(session, game, gamestate, countries, communicating=False)
    assert "Could not find event" not in caplog.text
    assert _events(session, ET.first_contact) == []
