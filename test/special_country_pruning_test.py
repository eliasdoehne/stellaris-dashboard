import sqlalchemy

from stellarisdashboard import datamodel


def _session():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    session = sqlalchemy.orm.sessionmaker(bind=engine)()
    sqlalchemy.event.listen(session, "before_flush", datamodel.drop_special_country_events)
    return session


def _country(session, country_type, name):
    game = datamodel.Game(game_name="g")
    country = datamodel.Country(
        game=game, country_id_in_game=len(name), country_type=country_type,
        country_name=name, is_player=False,
    )
    session.add(country)
    return country


def test_routine_events_are_dropped_only_for_special_countries():
    session = _session()
    special = _country(session, "pirate", "pirates")
    real = _country(session, "default", "empire")
    ET = datamodel.HistoricalEventType
    for country in (special, real):
        for event_type in (ET.new_policy, ET.empire_renamed):
            session.add(
                datamodel.HistoricalEvent(
                    event_type=event_type, country=country, start_date_days=1
                )
            )
    session.flush()
    stored = {
        (e.country_id, e.event_type)
        for e in session.query(datamodel.HistoricalEvent)
    }
    assert stored == {
        (special.country_id, ET.empire_renamed),
        (real.country_id, ET.new_policy),
        (real.country_id, ET.empire_renamed),
    }
