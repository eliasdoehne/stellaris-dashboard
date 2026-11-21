"""SharedDescriptions: one per import, forgotten after a failed processor or import."""

import contextlib

import pytest
import sqlalchemy

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import timeline

GAMESTATE = {"date": "2300.01.01", "galaxy": {}, "country": {}}


def _gamestate(date):
    return dict(GAMESTATE, date=date)


class _Looks(timeline.AbstractGamestateDataProcessor):
    """Looks up the texts in TEXTS and records the description objects it got, with their
    ids (None for one that is not stored yet)."""

    ID = "looks"
    TEXTS = ()
    got = []

    def extract_data_from_gamestate(self, dependencies):
        for text in self.TEXTS:
            description = self._get_or_add_shared_description(text)
            # the same object for the same text within an import
            assert self._get_or_add_shared_description(text) is description
            # what the processor gets must belong to the session: a description from a
            # rolled-back savepoint or an earlier session would not be written
            assert description in self._session
            type(self).got.append((text, description, description.description_id))

    def data(self):
        return self.ID


class _Fails(_Looks):
    ID = "fails"
    error = RuntimeError

    def extract_data_from_gamestate(self, dependencies):
        super().extract_data_from_gamestate(dependencies)
        self._session.flush()
        raise self.error("boom")


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
    _Looks.got = []
    _Fails.got = []
    extractor = timeline.TimelineExtractor()
    extractor.session_factory = session_factory
    yield extractor
    engine.dispose()


def _stored(extractor):
    with extractor.session_factory() as session:
        return sorted(
            (d.text, d.description_id)
            for d in session.query(datamodel.SharedDescription)
        )


def _run(extractor, monkeypatch, date, *processors):
    monkeypatch.setattr(extractor, "_data_processors", lambda: iter(processors))
    extractor.process_gamestate("g", _gamestate(date))


def test_one_query_for_all_lookups_of_an_import(extractor, monkeypatch):
    _Looks.TEXTS = ("a", "b")
    _run(extractor, monkeypatch, "2300.01.01", _Looks())

    statements = []
    monkeypatch.setattr(_Looks, "TEXTS", ("a", "b", "c", "d"))
    with extractor.session_factory() as session:
        engine = session.get_bind()

    def count(conn, cursor, statement, *args):
        if "shared_description" in statement and statement.startswith("SELECT"):
            statements.append(statement)

    sqlalchemy.event.listen(engine, "before_cursor_execute", count)
    try:
        _run(extractor, monkeypatch, "2300.02.01", _Looks(), _Looks())
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == 1
    assert _stored(extractor) == [("a", 1), ("b", 2), ("c", 3), ("d", 4)]
    # both processors of the second import got the same objects
    second_import = _Looks.got[2:]
    assert [d for _, d, _ in second_import[:4]] == [d for _, d, _ in second_import[4:]]


def test_description_of_a_failed_processor_is_added_again(extractor, monkeypatch):
    _Looks.TEXTS = ("old",)
    _run(extractor, monkeypatch, "2300.01.01", _Looks())

    _Fails.TEXTS = ("old", "new")
    _Looks.TEXTS = ("new", "old")
    _run(extractor, monkeypatch, "2300.02.01", _Fails(), _Looks())
    assert [f["processor"] for f in extractor.failures] == ["fails"]
    failed = {text: d for text, d, _ in _Fails.got}
    later = {text: d for text, d, _ in _Looks.got[1:]}
    # the failed processor's "new" was rolled back with its savepoint
    assert later["new"] is not failed["new"]
    assert sqlalchemy.inspect(failed["new"]).transient
    assert _stored(extractor) == [("new", 2), ("old", 1)]

    # and a later import finds the description that the later processor added
    _Looks.got = []
    _run(extractor, monkeypatch, "2300.03.01", _Looks())
    assert {text: id_ for text, _, id_ in _Looks.got} == {"new": 2, "old": 1}
    assert _stored(extractor) == [("new", 2), ("old", 1)]


@pytest.mark.parametrize(
    "error, debug_mode",
    [(RuntimeError, True), (KeyboardInterrupt, False)],
)
def test_failed_import_leaves_nothing_for_the_next_one(
    extractor, monkeypatch, error, debug_mode
):
    _Looks.TEXTS = ("old",)
    _run(extractor, monkeypatch, "2300.01.01", _Looks())

    # the import is aborted and re-raises: its session is rolled back and closed
    monkeypatch.setattr(config.CONFIG, "debug_mode", debug_mode)
    monkeypatch.setattr(_Fails, "error", error)
    _Fails.TEXTS = ("old", "new")
    with pytest.raises(error):
        _run(extractor, monkeypatch, "2300.02.01", _Looks(), _Fails())
    failed = _Looks.got[1:] + _Fails.got
    assert _stored(extractor) == [("old", 1)]

    monkeypatch.setattr(config.CONFIG, "debug_mode", False)
    _Looks.got = []
    _Looks.TEXTS = ("old", "new")
    _run(extractor, monkeypatch, "2300.02.01", _Looks())
    assert extractor.failures == []
    fresh = [d for _, d, _ in _Looks.got]
    assert not any(d is f for d in fresh for _, f, _ in failed)
    assert _stored(extractor) == [("new", 2), ("old", 1)]


def test_existing_gamestate_is_skipped_without_lookups(extractor, monkeypatch):
    _Looks.TEXTS = ("old",)
    _run(extractor, monkeypatch, "2300.01.01", _Looks())
    _run(extractor, monkeypatch, "2300.01.01", _Looks())
    assert len(_Looks.got) == 1
    _run(extractor, monkeypatch, "2300.02.01", _Looks())
    (_, first, _), (_, second, second_id) = _Looks.got
    assert first is not second
    assert second_id == 1


def test_duplicate_stored_texts_fail_only_when_looked_up(tmp_path):
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    with sqlalchemy.orm.Session(engine) as session:
        session.add_all(
            [
                datamodel.SharedDescription(text="twice"),
                datamodel.SharedDescription(text="twice"),
                datamodel.SharedDescription(text="once"),
            ]
        )
        session.flush()
        descriptions = timeline.SharedDescriptions(session)
        assert not descriptions.loaded
        assert descriptions.get_or_add("once").description_id == 3
        assert descriptions.loaded
        with pytest.raises(sqlalchemy.exc.MultipleResultsFound):
            descriptions.get_or_add("twice")

        added = descriptions.get_or_add("new")
        assert added in session.new
        descriptions.forget()
        assert not descriptions.loaded
        # forget() keeps nothing: the next lookup reads the table again
        session.flush()
        assert descriptions.get_or_add("new") is added
        session.rollback()
        descriptions.forget()
        assert descriptions.get_or_add("new") is not added
