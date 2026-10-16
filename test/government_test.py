import json

import pytest
import sqlalchemy
from sqlalchemy.orm import sessionmaker

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline

EventType = datamodel.HistoricalEventType


def _country_dict(
    name="Old Name",
    civics=("civic_a",),
    ethics=("ethic_a",),
    authority="auth_democratic",
    gov_type="gov_representative_democracy",
):
    return {
        "name": {"key": name},
        "ethos": {"ethic": list(ethics)},
        "government": {
            "civics": list(civics),
            "authority": authority,
            "type": gov_type,
        },
    }


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    timeline._shared_description_cache.clear()


def _process_government(session, country, country_dict, date_in_days):
    processor = timeline.GovernmentProcessor()
    processor.initialize(
        game=None,
        gamestate_dict={"country": {1: country_dict}},
        gs=None,
        basic_info=timeline.BasicGameInfo(
            game_id="test",
            date_in_days=date_in_days,
            player_country_id=1,
            other_players=set(),
            number_of_parsed_saves=0,
        ),
        db_session=session,
    )
    processor.extract_data_from_gamestate(
        {
            timeline.CountryProcessor.ID: {1: country},
            timeline.RulerEventProcessor.ID: {},
        }
    )
    session.flush()


def _event_types(session):
    return sorted(
        e.event_type.name for e in session.query(datamodel.HistoricalEvent).all()
    )


@pytest.fixture
def country_with_initial_government(session):
    country = datamodel.Country(is_player=True)
    session.add(country)
    _process_government(session, country, _country_dict(), date_in_days=100)
    assert _event_types(session) == []  # the first government is not a reform
    return country


def test_unchanged_government_logs_nothing(session, country_with_initial_government):
    _process_government(
        session, country_with_initial_government, _country_dict(), date_in_days=130
    )
    assert _event_types(session) == []


def test_rename_only_logs_empire_renamed(session, country_with_initial_government):
    _process_government(
        session,
        country_with_initial_government,
        _country_dict(name="New Name"),
        date_in_days=130,
    )
    assert _event_types(session) == ["empire_renamed"]
    event = session.query(datamodel.HistoricalEvent).one()
    old_name, new_name = event.db_description.text.split("\n")
    assert json.loads(old_name) == {"key": "Old Name"}
    assert json.loads(new_name) == {"key": "New Name"}


@pytest.mark.parametrize(
    "changes",
    [
        dict(civics=("civic_a", "civic_b")),
        dict(ethics=("ethic_b",)),
        dict(authority="auth_oligarchic"),
        dict(gov_type="gov_executive_committee"),
    ],
)
def test_reform_only_logs_government_reform(
    session, country_with_initial_government, changes
):
    _process_government(
        session,
        country_with_initial_government,
        _country_dict(**changes),
        date_in_days=130,
    )
    assert _event_types(session) == ["government_reform"]


def test_rename_and_reform_logs_both(session, country_with_initial_government):
    _process_government(
        session,
        country_with_initial_government,
        _country_dict(name="New Name", civics=("civic_b",)),
        date_in_days=130,
    )
    assert _event_types(session) == ["empire_renamed", "government_reform"]


SEVEN_CIVICS = [f"civic_{name}" for name in "abcdefg"]


def _governments(session):
    return (
        session.query(datamodel.Government)
        .order_by(datamodel.Government.start_date_days)
        .all()
    )


def test_seven_civics_are_stored_and_seventh_change_is_a_reform(session):
    country = datamodel.Country(is_player=True)
    session.add(country)
    _process_government(
        session, country, _country_dict(civics=SEVEN_CIVICS), date_in_days=100
    )
    (government,) = _governments(session)
    assert government.civics == set(SEVEN_CIVICS)
    assert government.civic_7 is not None
    assert government.civic_8 is None

    # same civics (in a different order) in a later save: nothing changes
    _process_government(
        session, country, _country_dict(civics=SEVEN_CIVICS[::-1]), date_in_days=130
    )
    assert len(_governments(session)) == 1
    assert _event_types(session) == []

    # replacing the 7th civic (alphabetically) is a government reform
    changed_civics = SEVEN_CIVICS[:6] + ["civic_z"]
    _process_government(
        session, country, _country_dict(civics=changed_civics), date_in_days=160
    )
    governments = _governments(session)
    assert len(governments) == 2
    assert governments[-1].civics == set(changed_civics)
    assert _event_types(session) == ["government_reform"]


def test_more_than_eight_civics_keeps_first_eight(session):
    country = datamodel.Country(is_player=True)
    session.add(country)
    civics = [f"civic_{i}" for i in range(10)]
    _process_government(session, country, _country_dict(civics=civics), 100)
    _process_government(session, country, _country_dict(civics=civics), 130)
    (government,) = _governments(session)
    assert government.civics == set(sorted(civics)[:8])
    assert _event_types(session) == []
