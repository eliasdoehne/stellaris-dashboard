from stellarisdashboard.dashboard_app.event_registry import GALACTIC_CRISIS_PHRASES
import sqlalchemy

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import galaxy_events, timeline
from stellarisdashboard.parsing.galaxy_events import collect_galaxy_events, flag_date_days


def flag(year, month=1, day=1):
    return ((year - 2200) * 360 + (month - 1) * 30 + day - 1 + 2617000) * 24


def test_flag_date_days():
    assert flag_date_days(62808000) == 0  # game_started of a 2200.01.01 start
    assert flag_date_days(flag(2452, 6, 25)) == 252 * 360 + 5 * 30 + 24
    assert flag_date_days({"flag_date": flag(2450), "flag_days": 720}) == 250 * 360
    for value in (None, "yes", True, {"flag_days": 3}):
        assert flag_date_days(value) is None


def test_collect_galaxy_events():
    now = 260 * 360
    flags = {
        "extradimensional_invasion_happened": flag(2452, 6, 25),
        "crisis_stage_1": flag(2452, 6, 25),
        "crisis_stage_2": flag(2455),
        "voidworms_plague_crisis_started": flag(2308),
        "great_khan_dead": flag(2379, 12),
        "war_in_heaven_started": flag(2470),  # after the current date
        "unrelated_flag": flag(2300),
    }
    countries = {
        3: {"type": "awakened_fallen_empire", "last_changed_country_type": "2402.12.22"},
        5: {"type": "default", "flags": {"declared_crisis": flag(2458, 3), "other": flag(2210)}},
        4: {"type": "awakened_fallen_empire", "last_changed_country_type": "0.01.01"},
        26: {"type": "voidworms"},
        620757096: {"type": "portal_holder"},
        671088712: {"type": "extradimensional"},
        99: "none",
    }
    events = {(e.key, e.country_ids): e for e in collect_galaxy_events(flags, countries, now)}
    assert set(events) == {
        ("unbidden_arrived", (671088712,)),
        ("crisis_stage_2", (671088712,)),
        ("voidworm_plague_started", (26,)),
        ("great_khan_died", ()),
        ("war_in_heaven_started", (3, 4)),
        ("fallen_empire_awakened", (3,)),
        ("fallen_empire_awakened", (4,)),
        ("declared_crisis", (5,)),
    }
    assert events["unbidden_arrived", (671088712,)].date_days == flag_date_days(flag(2452, 6, 25))
    assert events["voidworm_plague_started", (26,)].date_days == 108 * 360
    assert events["war_in_heaven_started", (3, 4)].date_days == now
    assert events["war_in_heaven_started", (3, 4)].all_countries
    assert events["fallen_empire_awakened", (3,)].date_days == 202 * 360 + 11 * 30 + 21
    assert events["fallen_empire_awakened", (4,)].date_days == now
    assert list(collect_galaxy_events("none", "none", now)) == []


def test_every_galaxy_event_has_a_ledger_phrase():
    keys = {s.key for s in galaxy_events.SIGNALS}
    keys |= {f"crisis_stage_{n}" for n in galaxy_events.CRISIS_STAGES}
    keys |= {galaxy_events.FALLEN_EMPIRE_AWAKENED, galaxy_events.DECLARED_CRISIS}
    keys |= set(galaxy_events.TYPED_AWAKENINGS) | {key for key, _ in galaxy_events.COUNTRY_FLAG_EVENTS}
    assert keys == set(GALACTIC_CRISIS_PHRASES)


def _keys(flags, countries, now=400 * 360):
    return {(e.key, e.country_ids): e for e in collect_galaxy_events(flags, countries, now)}


def test_upstart_and_guardian_awakenings_are_told_apart():
    flags = {"sleepers_awake_happened": flag(2403), "guardians_of_the_galaxy_happened": flag(2502, 8)}
    countries = {
        1: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2403)}},
        2: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2502, 8)}},
        3: {"type": "awakened_fallen_empire", "last_changed_country_type": "2420.01.01"},
    }
    events = _keys(flags, countries, now=500 * 360)
    assert events["fallen_empire_awakened_upstart", (1,)].date_days == flag_date_days(flag(2403))
    assert events["fallen_empire_awakened_guardian", (2,)].date_days == flag_date_days(flag(2502, 8))
    assert ("fallen_empire_awakened", (3,)) in events
    assert len(events) == 3


def test_a_second_upstart_is_not_a_guardian():
    flags = {"sleepers_awake_happened": flag(2403), "guardians_of_the_galaxy_happened": flag(2502, 8)}
    countries = {
        1: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2403)}},
        2: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2450)}},
    }
    events = _keys(flags, countries, now=500 * 360)
    assert {k for k, _ in events} == {"fallen_empire_awakened_upstart"}


def test_rival_roll_events():
    flags = {
        "sleepers_awake_happened": flag(2340, 11),
        "no_war_in_heaven": flag(2340, 11),
        "sleepers_awake_rival_waking": flag(2348, 8),
    }
    countries = {
        1: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2340, 11)}},
        2: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_ancient_rival": flag(2341, 3)}},
        3: {"type": "fallen_empire"},
    }
    events = _keys(flags, countries)
    assert set(events) == {
        ("fallen_empire_awakened_upstart", (1,)),
        ("war_in_heaven_ruled_out", (1,)),
        ("war_in_heaven_rival_chosen", (2,)),
        ("ancient_rival_woke", (2,)),
    }
    assert events["war_in_heaven_rival_chosen", (2,)].date_days == flag_date_days(flag(2341, 3))
    assert events["ancient_rival_woke", (2,)].date_days == flag_date_days(flag(2348, 8))


def test_events_without_a_carrier_are_dropped():
    flags = {
        "no_war_in_heaven": flag(2340, 11),
        "sleepers_awake_rival_waking": flag(2348, 8),
        "war_in_heaven_nonaligned_league": flag(2351, 7),
        "fallen_empire_lost_war": flag(2502, 8),
    }
    countries = {1: {"type": "awakened_fallen_empire"}, 2: {"type": "fallen_empire"}}
    assert {k for k, _ in _keys(flags, countries)} == {"fallen_empire_awakened"}  # only the generic awakening


def test_one_carrier_is_picked_by_the_closest_date():
    flags = {"no_war_in_heaven": flag(2340, 11), "fallen_empire_lost_war": flag(2450, 3)}
    countries = {
        1: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2380)}},
        2: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2340, 11)}},
        5: {"type": "fallen_empire", "flags": {"fallen_empire_lost_war": flag(2455)}},
        6: {"type": "fallen_empire", "flags": {"fallen_empire_lost_war": flag(2450, 3)}},
        7: {"type": "fallen_empire", "flags": {"fallen_empire_lost_war": flag(2470)}},
    }
    events = _keys(flags, countries)
    assert ("war_in_heaven_ruled_out", (2,)) in events
    assert ("fallen_empire_lost_war", (6,)) in events
    assert len([k for k, _ in events if k in ("war_in_heaven_ruled_out", "fallen_empire_lost_war")]) == 2


def test_league_events_go_to_the_league_leader():
    flags = {
        "war_in_heaven_nonaligned_league": flag(2351, 7),
        "war_in_heaven_nonaligned_league_war": flag(2352, 5),
    }
    countries = {
        7: {"type": "default", "flags": {"war_in_heaven_league_leader": flag(2351, 7)}},
        8: {"type": "default", "flags": {"formed_nonaligned_league": flag(2351, 8)}},
    }
    events = _keys(flags, countries)
    assert set(events) == {("nonaligned_league_formed", (7,)), ("nonaligned_league_war", (7,))}
    assert all(e.all_countries for e in events.values())
    assert events["nonaligned_league_war", (7,)].date_days == flag_date_days(flag(2352, 5))


def _processor_run(session, game, countries, gamestate_dict, date):
    days = datamodel.date_to_days(date)
    gamestate = datamodel.GameState(game=game, date=days)
    session.add(gamestate)
    session.flush()
    processor = timeline.GalaxyEventProcessor()
    info = timeline.BasicGameInfo(
        game_id="g", date_in_days=days, player_country_id=None, other_players=set(), number_of_parsed_saves=0
    )
    processor.initialize(game, gamestate_dict, gamestate, info, session)
    processor.extract_data_from_gamestate({timeline.CountryProcessor.ID: countries})
    session.flush()


def _stored(session):
    events = session.query(datamodel.HistoricalEvent).filter_by(
        event_type=datamodel.HistoricalEventType.galactic_crisis
    )
    return sorted((e.db_description.text, e.country.country_id_in_game, e.event_is_known_to_player) for e in events)


def test_processor_keeps_one_awakening_per_country_and_hides_the_rival_roll():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    session = sqlalchemy.orm.sessionmaker(bind=engine)()
    game = datamodel.Game(game_name="g")
    db_countries = {
        i: datamodel.Country(
            game=game, country_id_in_game=i, country_type="awakened_fallen_empire", country_name=f"c{i}", is_player=False
        )
        for i in (1, 2)
    }
    session.add_all([game, *db_countries.values()])
    session.flush()
    # an older database stored the awakening without its kind
    session.add(
        datamodel.HistoricalEvent(
            event_type=datamodel.HistoricalEventType.galactic_crisis,
            country=db_countries[2],
            start_date_days=1,
            event_is_known_to_player=True,
            db_description=datamodel.SharedDescription(text="fallen_empire_awakened"),
        )
    )
    flags = {"sleepers_awake_happened": flag(2340, 11), "no_war_in_heaven": flag(2340, 11)}
    gamestate = {
        "flags": flags,
        "country": {
            1: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2340, 11)}},
            2: {"type": "awakened_fallen_empire", "flags": {"sleepers_awake_first_sleeper": flag(2340, 11)}},
        },
    }
    _processor_run(session, game, db_countries, gamestate, "2341.01.01")
    stored = _stored(session)
    assert [row for row in stored if row[1] == 2 and row[0].startswith("fallen_empire_awakened")] == [
        ("fallen_empire_awakened", 2, True)
    ]
    assert ("fallen_empire_awakened_upstart", 1, True) in stored
    assert ("war_in_heaven_ruled_out", 1, False) in stored
    # the carrier disappears: nothing moves to another country and nothing is added
    gamestate["country"] = {2: gamestate["country"][2]}
    _processor_run(session, game, db_countries, gamestate, "2341.02.01")
    assert _stored(session) == stored
