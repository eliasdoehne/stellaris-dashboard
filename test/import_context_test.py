"""ImportContext.most_recent_country_data agrees with Country.get_most_recent_data().

The processors that ask for a country's latest data run after CountryDataProcessor, whose rows
the processor's savepoint has flushed. Then get_most_recent_data() returns the stored row with
the latest date, which is this gamestate's row only if no later gamestate is stored. Checked
for an import in order, an older save imported after newer ones, a replaced gamestate that is
not the newest, and countries without a row in the imported gamestate.
"""

import pytest
import sqlalchemy
from sqlalchemy.orm import Session

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import pending_flush
from stellarisdashboard.parsing.import_context import ImportContext

COUNTRIES = 4


@pytest.fixture
def engine():
    engine = sqlalchemy.create_engine("sqlite://", poolclass=sqlalchemy.pool.StaticPool)
    datamodel.Base.metadata.create_all(engine)
    with Session(bind=engine) as session:
        game = datamodel.Game(game_name="g")
        session.add(game)
        for i in range(COUNTRIES):
            session.add(
                datamodel.Country(game=game, country_id_in_game=i, country_name=f"c{i}")
            )
        session.commit()
    return engine


def _import(engine, date, with_rows=range(COUNTRIES - 1), replace=False):
    """An import of a gamestate up to the point after CountryDataProcessor: the result of both
    lookups for every country, and the number of SELECTs that most_recent_country_data sent.
    """
    with (
        Session(bind=engine, expire_on_commit=False) as session,
        pending_flush.flush_before_reads(session),
    ):
        game = session.query(datamodel.Game).one()
        if replace:
            for gamestate in session.query(datamodel.GameState).filter_by(date=date):
                session.delete(gamestate)
            session.flush()
        gamestate = datamodel.GameState(game=game, date=date)
        session.add(gamestate)
        countries = (
            session.query(datamodel.Country)
            .order_by(datamodel.Country.country_id)
            .all()
        )
        for i in with_rows:
            session.add(
                datamodel.CountryData(
                    country=countries[i],
                    game_state=gamestate,
                    date=date,
                    military_power=date,
                )
            )
        session.flush()  # CountryDataProcessor's savepoint
        context = ImportContext(session, game, gamestate, shared_descriptions=None)
        selects = []
        sqlalchemy.event.listen(
            session, "do_orm_execute", lambda state: selects.append(state)
        )
        fast = [context.most_recent_country_data(c) for c in countries]
        fast_selects = len(selects)
        master = [c.get_most_recent_data() for c in countries]
        result = [(f is m, f and f.date) for f, m in zip(fast, master)]
        session.commit()
        context.close()
        return result, fast_selects


def test_in_order(engine):
    for date in (10, 20, 30):
        result, selects = _import(engine, date)
        assert all(same for same, _ in result)
        assert [d for _, d in result][:-1] == [date] * (COUNTRIES - 1)
        assert result[-1] == (True, None)  # a country without any data
        # two queries for the countries with a row, one more for the one without
        assert selects == 3


def test_older_save_imported_after_newer_ones(engine):
    _import(engine, 10)
    _import(engine, 30)
    result, _ = _import(engine, 20, with_rows=[0, 1])
    assert all(same for same, _ in result)
    assert [d for _, d in result] == [30, 30, 30, None]  # not this gamestate's rows


def test_replaced_gamestate_that_is_not_the_newest(engine):
    _import(engine, 10)
    _import(engine, 20)
    _import(engine, 30, with_rows=[0])
    result, _ = _import(engine, 20, replace=True)
    assert all(same for same, _ in result)
    assert [d for _, d in result] == [30, 20, 20, None]


def test_replaced_newest_gamestate(engine):
    _import(engine, 10)
    _import(engine, 20)
    result, _ = _import(engine, 20, with_rows=[1], replace=True)
    assert all(same for same, _ in result)
    assert [d for _, d in result] == [10, 20, 10, None]
