"""Write sessions take SQLite's write lock when their transaction begins.

With a deferred BEGIN a write session starts as a read transaction. If another process commits
before its first write, SQLite fails the upgrade at once ("database is locked", the busy timeout
does not apply to a stale snapshot). BEGIN IMMEDIATE makes the second writer wait instead.
"""

import multiprocessing
import queue

import pytest
import sqlalchemy

from stellarisdashboard import config, datamodel

GAME = "write_lock_test"
TIMEOUT = 60
# How long the first process waits for the second one to commit. With a deferred BEGIN that is
# immediate; with BEGIN IMMEDIATE the second process is blocked until the first one commits.
PATIENCE = 3


def _texts(session):
    query = session.query(datamodel.SharedDescription.text)
    return [text for (text,) in query.order_by(datamodel.SharedDescription.description_id)]


def _write_row(text):
    with datamodel.get_db_session(GAME, write=True) as session:
        session.add(datamodel.SharedDescription(text=text))
        session.commit()


def _first_process(base_path, write, read_done, other_done, results):
    try:
        config.CONFIG.base_output_path = base_path
        with datamodel.get_db_session(GAME, write=write) as session:
            # The import opens a savepoint per data processor, and for a session that has not
            # written yet that SAVEPOINT is the first statement: pysqlite sends no BEGIN for it,
            # so SQLite starts a deferred transaction whose first read takes a snapshot.
            session.begin_nested()
            before = _texts(session)
            read_done.set()
            other_done.wait(PATIENCE if write else TIMEOUT)
            if write:
                session.add(datamodel.SharedDescription(text="first"))
            session.commit()  # also releases the savepoint
            results.put(("ok", before, _texts(session)))
    except Exception as e:
        results.put(("error", repr(e)))
    finally:
        read_done.set()


def _second_process(base_path, read_done, other_done, results):
    try:
        config.CONFIG.base_output_path = base_path
        assert read_done.wait(TIMEOUT)
        _write_row("second")
        results.put(("ok",))
    except Exception as e:
        results.put(("error", repr(e)))
    finally:
        other_done.set()


@pytest.fixture
def game_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path)
    (tmp_path / "db").mkdir()
    _write_row("seed")
    yield tmp_path
    datamodel.dispose_engines()


def _run_two_processes(base_path, first_writes):
    ctx = multiprocessing.get_context("spawn")
    read_done, other_done = ctx.Event(), ctx.Event()
    first_results, second_results = ctx.Queue(), ctx.Queue()
    processes = [
        ctx.Process(
            target=_first_process,
            args=(base_path, first_writes, read_done, other_done, first_results),
        ),
        ctx.Process(
            target=_second_process, args=(base_path, read_done, other_done, second_results)
        ),
    ]
    for p in processes:
        p.start()
    outcome = []
    try:
        for q in (first_results, second_results):
            try:
                outcome.append(q.get(timeout=TIMEOUT))
            except queue.Empty:
                outcome.append(("error", "timeout"))
    finally:
        for p in processes:
            p.join(10)
            if p.is_alive():
                p.kill()
    return outcome


def test_second_writer_waits_instead_of_failing(game_db):
    first, second = _run_two_processes(game_db, first_writes=True)
    assert first[0] == "ok", first
    assert second[0] == "ok", second
    assert first[1] == ["seed"]
    with datamodel.get_db_session(GAME) as session:
        assert sorted(_texts(session)) == ["first", "second", "seed"]


def test_read_session_does_not_block_other_writers(game_db):
    first, second = _run_two_processes(game_db, first_writes=False)
    assert first[0] == "ok", first
    assert second[0] == "ok", second
    assert first[1] == ["seed"]


def _logged_sql(game):
    datamodel._get_or_create_sessionmaker(game)
    engine = datamodel._ENGINES[game]
    statements = []

    def log(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sqlalchemy.event.listen(engine, "before_cursor_execute", log)
    return engine, statements, log


def _begins(statements):
    return [s for s in statements if s.startswith("BEGIN")]


def test_write_session_begins_immediate_for_each_transaction(game_db):
    engine, statements, log = _logged_sql(GAME)
    try:
        with datamodel.get_db_session(GAME, write=True) as session:
            session.add(datamodel.SharedDescription(text="a"))
            session.commit()
            session.add(datamodel.SharedDescription(text="b"))
            session.flush()
            session.rollback()
            session.add(datamodel.SharedDescription(text="c"))
            session.commit()
        with datamodel.get_db_session(GAME) as session:
            assert _texts(session) == ["seed", "a", "c"]
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", log)
    # one per outer transaction; the first statement of each transaction is the BEGIN
    assert _begins(statements) == ["BEGIN IMMEDIATE"] * 3
    assert not any("SAVEPOINT" in s for s in statements)


def test_read_session_begins_deferred(game_db):
    engine, statements, log = _logged_sql(GAME)
    try:
        # the same thread-local Session object serves the write session before it
        with datamodel.get_db_session(GAME, write=True) as session:
            session.add(datamodel.SharedDescription(text="written"))
            session.commit()
        with datamodel.get_db_session(GAME) as session:
            session.add(datamodel.SharedDescription(text="rolled back"))
            session.flush()
            session.rollback()
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", log)
    # only the write session's transaction took the write lock; the read session's is deferred
    assert _begins(statements) == ["BEGIN IMMEDIATE"]


def test_savepoints_work_in_write_session(game_db):
    engine, statements, log = _logged_sql(GAME)
    try:
        with datamodel.get_db_session(GAME, write=True) as session:
            session.add(datamodel.SharedDescription(text="outer"))
            nested = session.begin_nested()
            session.add(datamodel.SharedDescription(text="rolled back"))
            session.flush()
            nested.rollback()
            with session.begin_nested():
                session.add(datamodel.SharedDescription(text="kept"))
            session.commit()
        with datamodel.get_db_session(GAME) as session:
            assert _texts(session) == ["seed", "outer", "kept"]
    finally:
        sqlalchemy.event.remove(engine, "before_cursor_execute", log)
    assert _begins(statements) == ["BEGIN IMMEDIATE"]
    assert statements.index("BEGIN IMMEDIATE") < min(
        i for i, s in enumerate(statements) if s.startswith("SAVEPOINT")
    )
    assert sum(s.startswith("SAVEPOINT") for s in statements) == 2
