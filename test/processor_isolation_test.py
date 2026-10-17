import contextlib

import pytest
import sqlalchemy

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import timeline

GAMESTATE = {
    "date": "2300.01.01",
    "galaxy": {},
    "country": {},
}


class _AddsCountry(timeline.AbstractGamestateDataProcessor):
    ID = "ok"

    def extract_data_from_gamestate(self, dependencies):
        self._session.add(
            datamodel.Country(
                game=self._db_game, country_id_in_game=1, country_name=self.ID
            )
        )

    def data(self):
        return self.ID


class _Good(_AddsCountry):
    ID = "good"


class _Bad(_AddsCountry):
    ID = "bad"

    def extract_data_from_gamestate(self, dependencies):
        super().extract_data_from_gamestate(dependencies)
        self._session.flush()
        raise RuntimeError("boom")


class _DependsOnBad(_AddsCountry):
    ID = "dependent"
    DEPENDENCIES = ["bad"]


class _Later(_AddsCountry):
    ID = "later"
    DEPENDENCIES = ["good"]


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
    monkeypatch.setattr(config.CONFIG, "debug_mode", False)
    extractor = timeline.TimelineExtractor()
    extractor.session_factory = session_factory
    return extractor


def test_failing_processor_only_loses_its_own_changes(extractor, monkeypatch, caplog):
    monkeypatch.setattr(
        extractor,
        "_data_processors",
        lambda: iter([_Good(), _Bad(), _DependsOnBad(), _Later()]),
    )
    extractor.process_gamestate("g", dict(GAMESTATE))

    with extractor.session_factory() as session:
        assert session.query(datamodel.GameState).count() == 1
        names = {c.country_name for c in session.query(datamodel.Country)}
    assert names == {"good", "later"}
    assert extractor.number_of_parsed_saves == 1
    assert "Processor bad failed" in caplog.text
    assert "Could not process dependent due to missing dependencies bad" in caplog.text


def test_failing_processor_reraises_in_debug_mode(extractor, monkeypatch):
    monkeypatch.setattr(extractor, "_data_processors", lambda: iter([_Bad()]))
    monkeypatch.setattr(config.CONFIG, "debug_mode", True)
    with pytest.raises(RuntimeError):
        extractor.process_gamestate("g", dict(GAMESTATE))
    with extractor.session_factory() as session:
        assert session.query(datamodel.GameState).count() == 0
