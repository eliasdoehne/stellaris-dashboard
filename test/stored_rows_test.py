"""StoredRows (stellarisdashboard/parsing/stored_rows.py) answers as the query it replaces.

The session is set up as in an import: no autoflush, the pending_flush listener and the
special-country hook. Each lookup is compared with the query it emulates, sent after a flush.
"""

import random

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import pending_flush
from stellarisdashboard.parsing.stored_rows import StoredRows, key_value, related_key

Event = datamodel.HistoricalEvent
Type = datamodel.HistoricalEventType


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    # as during an import, where nothing expires the objects of earlier processors
    session = Session(bind=engine, expire_on_commit=False)
    sqlalchemy.event.listen(
        session, "before_flush", datamodel.drop_special_country_events
    )
    with pending_flush.flush_before_reads(session):
        yield session
    session.close()


@pytest.fixture
def world(session):
    game = datamodel.Game(game_name="g")
    countries = [
        datamodel.Country(
            game=game,
            country_id_in_game=i,
            country_name=f"c{i}",
            country_type="default",
        )
        for i in range(3)
    ]
    special = datamodel.Country(
        game=game, country_id_in_game=9, country_name="s", country_type="primitive"
    )
    leaders = [
        datamodel.Leader(game=game, leader_id_in_game=i, country=countries[0])
        for i in range(3)
    ]
    session.add_all([game, special, *countries, *leaders])
    session.commit()
    return countries, special, leaders


def _event(
    country, leader=None, start=0, end=None, event_type=Type.ruled_empire, **kwargs
):
    return Event(
        event_type=event_type,
        country=country,
        leader=leader,
        start_date_days=start,
        end_date_days=end,
        **kwargs,
    )


class _FlushCounter:
    def __init__(self, session):
        self.count = 0
        sqlalchemy.event.listen(session, "after_flush", self)

    def __call__(self, *args):
        self.count += 1


def _query_latest(
    session, country, leader, event_type=Type.ruled_empire, order=Event.start_date_days
):
    session.flush()
    return (
        session.query(Event)
        .filter_by(event_type=event_type, country=country, leader=leader)
        .order_by(order.desc(), Event.historical_event_id)
        .first()
    )


def _ruler_rows(session, **kwargs):
    return StoredRows(
        session,
        [Event.country, Event.leader],
        where={Event.event_type: Type.ruled_empire},
        order_by=Event.start_date_days,
        **kwargs,
    )


def test_latest_orders_like_the_query_nulls_last_and_ties_to_the_oldest_row(
    session, world
):
    countries, _, leaders = world
    c, l = countries[0], leaders[0]
    rows = [_event(c, l, start=s) for s in (5, 7, 7, 3)]
    nulls = [_event(c, leaders[1], end=None) for _ in range(2)]
    session.add_all(rows + nulls)
    session.commit()
    with (
        StoredRows(
            session, [Event.country, Event.leader], order_by=Event.end_date_days
        ) as by_end,
        _ruler_rows(session) as by_start,
    ):
        assert by_start.latest(c, l) is rows[1]  # 7 twice: the smaller primary key
        assert by_start.latest(c, l) is _query_latest(session, c, l)
        # all ordering values NULL: the oldest row
        assert by_end.latest(c, leaders[1]) is nulls[0]
        assert by_end.latest(c, leaders[1]) is _query_latest(
            session, c, leaders[1], order=Event.end_date_days
        )
        # a value beats NULL
        nulls[1].end_date_days = 1
        assert by_end.latest(c, leaders[1]) is nulls[1]
        assert by_start.latest(c, leaders[2]) is None
        assert by_start.latest(c, l, at_most=6) is rows[0]
        assert by_start.latest(c, l, at_most=2) is None


def test_null_keys(session, world):
    countries, _, leaders = world
    without_leader = _event(countries[0], None, start=1)
    session.add(without_leader)
    session.commit()
    with _ruler_rows(session) as rows:
        assert rows.latest(countries[0], None) is without_leader
        assert rows.latest(None, None) is None
        pending = _event(None, None, start=2)
        session.add(pending)
        assert rows.latest(None, None) is pending


def test_added_objects_are_found_without_a_flush_and_rank_after_stored_rows(
    session, world
):
    countries, _, leaders = world
    c, l = countries[0], leaders[0]
    stored = _event(c, l, start=4)
    session.add(stored)
    session.commit()
    flushes = _FlushCounter(session)
    with _ruler_rows(session) as rows:
        first, second = _event(c, l, start=4), _event(c, l, start=4)
        session.add(first)
        session.add(second)
        assert rows.latest(c, l) is stored  # same date: the stored row is older
        later = _event(c, l, start=6)
        session.add(later)
        assert rows.latest(c, l) is later
        later.start_date_days = 4  # changed after it was added
        assert rows.latest(c, l) is stored
        assert flushes.count == 0
        stored.start_date_days = 1
        assert rows.latest(c, l) is first  # pending ties in the order they were added
        assert rows.latest(c, l) is _query_latest(session, c, l)
        # after the flush, primary keys decide in the same order
        assert rows.latest(c, l) is first


def test_keys_of_pending_related_objects(session, world):
    countries, _, _ = world
    with _ruler_rows(session) as rows:
        new_leader = datamodel.Leader(
            game=countries[0].game, leader_id_in_game=50, country=countries[0]
        )
        session.add(new_leader)
        event = _event(countries[0], new_leader, start=1)
        session.add(event)
        assert new_leader.leader_id is None
        assert related_key(event, Event.leader) is new_leader
        assert rows.latest(countries[0], new_leader) is event
        session.flush()  # the leader gets its primary key: the same lookup still finds the event
        assert (
            related_key(event, Event.leader)
            == new_leader.leader_id
            == key_value(new_leader)
        )
        assert rows.latest(countries[0], new_leader) is event


def test_rows_deleted_after_the_scan_are_skipped(session, world):
    countries, _, leaders = world
    c, l = countries[0], leaders[0]
    older, newer = _event(c, l, start=1), _event(c, l, start=2)
    session.add_all([older, newer])
    session.commit()
    older_id, newer_id = older.historical_event_id, newer.historical_event_id
    session.expunge_all()
    c, l = session.get(datamodel.Country, c.country_id), session.get(
        datamodel.Leader, l.leader_id
    )
    with _ruler_rows(session) as rows:
        # deleted in the database without the session holding it: found to be gone when loaded
        session.execute(
            sqlalchemy.delete(Event).where(Event.historical_event_id == newer_id)
        )
        assert rows.latest(c, l).historical_event_id == older_id
        older = session.get(Event, older_id)
        session.delete(older)  # deletion still pending
        assert rows.latest(c, l) is None
        assert _query_latest(session, c, l) is None


def test_key_order_and_criteria_changes_after_the_scan(session, world):
    countries, _, leaders = world
    events = [
        _event(countries[0], leaders[0], start=s, event_type=Type.councilor)
        for s in (1, 2)
    ]
    session.add_all(events)
    session.commit()
    with StoredRows(
        session,
        [Event.country, Event.leader],
        where={Event.event_type: Type.councilor, Event.end_date_days: None},
        order_by=Event.start_date_days,
    ) as rows:
        found = rows.latest(countries[0], leaders[0])
        assert found is events[1]
        found.end_date_days = 5  # no longer matches the criteria
        assert rows.latest(countries[0], leaders[0]) is events[0]
        events[0].leader = leaders[1]  # moved to another key
        assert rows.latest(countries[0], leaders[0]) is None
        assert rows.latest(countries[0], leaders[1]) is events[0]
        # a row that something else loaded and changed: moved into a key never looked up before
        unrelated = (
            session.query(Event)
            .filter_by(historical_event_id=events[1].historical_event_id)
            .one()
        )
        unrelated.end_date_days = None
        unrelated.country = countries[2]
        assert rows.latest(countries[2], leaders[0]) is unrelated
        assert rows.latest(countries[0], leaders[0]) is None


def test_one_or_none_and_multiple_results(session, world):
    countries, _, leaders = world
    session.add(_event(countries[0], leaders[0], event_type=Type.edict))
    session.commit()
    with StoredRows(
        session, [Event.country, Event.leader], where={Event.event_type: Type.edict}
    ) as rows:
        assert rows.one_or_none(countries[1], leaders[0]) is None
        assert rows.one_or_none(countries[0], leaders[0]) is not None
        assert rows.exists(countries[0], leaders[0])
        session.add(_event(countries[0], leaders[0], event_type=Type.edict))
        with pytest.raises(sqlalchemy.exc.MultipleResultsFound):
            rows.one_or_none(countries[0], leaders[0])
        session.flush()
        with pytest.raises(sqlalchemy.exc.MultipleResultsFound):
            session.query(Event).filter_by(
                event_type=Type.edict, country=countries[0], leader=leaders[0]
            ).one_or_none()


def test_events_that_the_flush_drops_are_not_found(session, world):
    countries, special, leaders = world
    with _ruler_rows(session) as rows:
        event = _event(special, leaders[0], start=1)
        session.add(event)
        assert rows.latest(special, leaders[0]) is None
        assert _query_latest(session, special, leaders[0]) is None
        # only by the foreign key: the same predicate looks the country up
        by_id = Event(
            event_type=Type.ruled_empire,
            country_id=special.country_id,
            start_date_days=1,
        )
        session.add(by_id)
        assert datamodel.is_dropped_special_country_event(session, by_id)
        assert rows.latest(special, None) is None
        kept = _event(countries[0], leaders[0], start=1, event_type=Type.ruled_empire)
        session.add(kept)
        assert not datamodel.is_dropped_special_country_event(session, kept)
        assert rows.latest(countries[0], leaders[0]) is kept


def test_lookups_outside_the_criteria_are_refused(session, world):
    countries, _, _ = world
    with StoredRows(
        session,
        [Event.event_type, Event.country],
        where={Event.event_type: frozenset({Type.edict, Type.councilor})},
    ) as rows:
        assert rows.one_or_none(Type.edict, countries[0]) is None
        with pytest.raises(ValueError):
            rows.one_or_none(Type.ruled_empire, countries[0])


def test_close_removes_the_listeners(session, world):
    rows = _ruler_rows(session)
    listeners = list(rows._listeners)
    assert all(sqlalchemy.event.contains(*listener) for listener in listeners)
    rows.close()
    assert not any(sqlalchemy.event.contains(*listener) for listener in listeners)
    with pytest.raises(RuntimeError):
        rows.latest(None, None)
    unclosed = _ruler_rows(session)
    listeners = list(unclosed._listeners)
    del unclosed
    assert not any(sqlalchemy.event.contains(*listener) for listener in listeners)


def test_columns_with_defaults_are_refused(session, world):
    with pytest.raises(ValueError):
        StoredRows(session, [Event.event_is_known_to_player])


@pytest.mark.parametrize("seed", range(40))
def test_random_changes_agree_with_the_query(session, world, seed):
    """Random adds, changes and deletes between lookups; every lookup is checked against the query."""
    rng = random.Random(seed)
    countries, special, leaders = world
    all_countries = [*countries, special, None]
    all_leaders = [*leaders, None]
    types = [Type.ruled_empire, Type.councilor]
    for _ in range(30):
        session.add(
            _event(
                rng.choice(all_countries),
                rng.choice(all_leaders),
                start=rng.randint(0, 4),
                end=rng.choice([None, 1, 2]),
                event_type=rng.choice(types),
            )
        )
    session.commit()
    rows = StoredRows(
        session,
        [Event.country, Event.leader],
        where={Event.event_type: Type.ruled_empire, Event.end_date_days: None},
        order_by=Event.start_date_days,
    )
    returned = []
    for _ in range(60):
        for _ in range(rng.randint(1, 4)):  # changes without a flush before the lookup
            action = rng.random()
            if action < 0.3:
                session.add(
                    _event(
                        rng.choice(all_countries),
                        rng.choice(all_leaders),
                        start=rng.randint(0, 4),
                        end=rng.choice([None, None, 1]),
                        event_type=rng.choice(types),
                    )
                )
            elif action < 0.5 and returned:
                obj = rng.choice(returned)
                if obj in session:
                    attribute = rng.choice(
                        ["start_date_days", "end_date_days", "leader", "country"]
                    )
                    value = {
                        "start_date_days": lambda: rng.randint(0, 4),
                        "end_date_days": lambda: rng.choice([None, 3]),
                        "leader": lambda: rng.choice(all_leaders),
                        "country": lambda: rng.choice(all_countries),
                    }[attribute]()
                    setattr(obj, attribute, value)
            elif action < 0.55 and returned:
                obj = rng.choice(returned)
                if obj in session and sqlalchemy.inspect(obj).persistent:
                    session.delete(obj)
            elif action < 0.6:
                session.flush()
        country, leader = rng.choice(all_countries), rng.choice(all_leaders)
        found = rows.latest(country, leader)
        session.flush()
        expected = (
            session.query(Event)
            .filter_by(
                event_type=Type.ruled_empire,
                end_date_days=None,
                country=country,
                leader=leader,
            )
            .order_by(Event.start_date_days.desc(), Event.historical_event_id)
            .first()
        )
        assert found is expected
        if found is not None:
            returned.append(found)
    rows.close()
