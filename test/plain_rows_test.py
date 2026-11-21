"""Rows queued as plain values, see stellarisdashboard/parsing/plain_rows.py.

A minimal model: items referring to a parent (with a back-populated collection) and to a
description (without one); the item columns cover a Python default, a callable default, a
server default, no default and a type that stores None as a value (JSON).
"""

import contextlib

import pytest
import sqlalchemy
from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, text
from sqlalchemy.orm import Session, declarative_base, relationship, validates

from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import pending_flush, plain_rows, timeline

Base = declarative_base()


class Parent(Base):
    __tablename__ = "parent"
    parent_id = Column(Integer, primary_key=True)
    name = Column(String)
    items = relationship(
        "Item", back_populates="parent", cascade="all,delete,delete-orphan"
    )


class Description(Base):
    __tablename__ = "description"
    description_id = Column(Integer, primary_key=True)
    text = Column(String)


class Item(Base):
    __tablename__ = "item"
    item_id = Column(Integer, primary_key=True)
    parent_id = Column(ForeignKey(Parent.parent_id))
    description_id = Column(ForeignKey(Description.description_id))
    with_default = Column(Float, default=1.5)
    with_callable_default = Column(String, default=lambda: "generated")
    with_server_default = Column("server_column", Integer, server_default=text("7"))
    without_default = Column(Float)
    json = Column(JSON)

    parent = relationship(Parent, back_populates="items")
    description = relationship(Description)


class Unrelated(Base):
    __tablename__ = "unrelated"
    unrelated_id = Column(Integer, primary_key=True)


class Listened(Base):
    __tablename__ = "listened"
    listened_id = Column(Integer, primary_key=True)
    value = Column(Integer)


sqlalchemy.event.listen(
    Listened, "before_insert", lambda mapper, connection, target: None
)


class ListenedRaw(Base):
    __tablename__ = "listened_raw"
    listened_raw_id = Column(Integer, primary_key=True)
    value = Column(Integer)


sqlalchemy.event.listen(ListenedRaw.value, "set", lambda *args: None, raw=True)


class Validated(Base):
    __tablename__ = "validated"
    validated_id = Column(Integer, primary_key=True)
    value = Column(Integer)

    @validates("value")
    def _check(self, key, value):
        return value


ITEM_COLUMNS = [
    "item_id",
    "parent_id",
    "description_id",
    "with_default",
    "with_callable_default",
    "server_column",
    "without_default",
    "json",
]


def sqlite_engine(url):
    """An engine whose savepoints work: pysqlite's own transaction handling is turned off.

    Otherwise pysqlite begins no transaction before a SELECT, so that a SAVEPOINT after only
    SELECTs is the outermost transaction and its RELEASE commits (see "Serializable
    isolation / Savepoints / Transactional DDL" in SQLAlchemy's SQLite dialect docs).
    """
    engine = sqlalchemy.create_engine(url)

    @sqlalchemy.event.listens_for(engine, "connect")
    def connect(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @sqlalchemy.event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


@pytest.fixture
def session():
    engine = sqlite_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Parent(name="persistent"), Description(text="d"), Unrelated()])
        s.commit()
        yield s


@contextlib.contextmanager
def import_session(session):
    with pending_flush.flush_before_reads(session), plain_rows.queue_inserts(session):
        yield session


def item_rows(session):
    columns = ", ".join(ITEM_COLUMNS)
    return session.execute(text(f"SELECT {columns} FROM item ORDER BY item_id")).all()


def queued(session):
    return session.info[plain_rows._QUEUE_KEY].rows


def row_values(session):
    """Rows as given to the constructor: None values, missing keys, key orders, relationships."""
    persistent = session.query(Parent).filter_by(name="persistent").one()
    description = session.query(Description).one()
    new_parent = Parent(name="new")
    session.add(new_parent)
    return [
        dict(
            parent=persistent,
            description=description,
            with_default=2.0,
            without_default=3.0,
        ),
        dict(
            without_default=3.0,
            with_default=2.0,
            description=description,
            parent=persistent,
        ),
        dict(parent=new_parent),  # every column missing
        dict(
            parent=None,
            description=None,
            with_default=None,
            with_callable_default=None,
            with_server_default=None,
            without_default=None,
            json=None,
        ),
        dict(with_server_default=9, json={"a": 1}, parent=new_parent),
        dict(with_default=2.0, parent=persistent),
        dict(parent=persistent, with_default=None, json=[1, 2]),
        dict(item_id=100, parent_id=None, description_id=description.description_id),
        dict(
            parent=new_parent,
            with_default=2.0,
            without_default=3.0,
            description=description,
        ),
    ]


def test_plain_rows_are_inserted_as_the_orm_inserts_objects(session):
    engine = session.get_bind()
    with Session(engine) as orm_session:
        for values in row_values(orm_session):
            orm_session.add(Item(**values))
        orm_session.flush()
        expected = item_rows(orm_session)
        orm_session.rollback()

    with import_session(session):
        for values in row_values(session):
            plain_rows.add(session, Item, **values)
        plain_rows.write(session)
        actual = item_rows(session)
        session.rollback()

    assert actual == expected
    # the defaults applied (None was left out), None stored as JSON null, explicit NULL
    by_id = {row.item_id: row for row in actual}
    assert by_id[4].with_default == 1.5
    assert by_id[4].with_callable_default == "generated"
    assert by_id[4].server_column == 7
    assert by_id[4].without_default is None
    assert by_id[4].json == "null"
    assert by_id[3].json is None
    assert by_id[5].server_column == 9
    assert 100 in by_id
    assert len({row.parent_id for row in actual if row.parent_id is not None}) == 2


def test_rows_are_committed_without_an_explicit_write(session):
    with import_session(session):
        parent = Parent(name="new")
        session.add(parent)
        plain_rows.add(session, Item, parent=parent, without_default=1.0)
        session.commit()
        assert not queued(session)
    assert [(row.parent_id, row.without_default) for row in item_rows(session)] == [
        (parent.parent_id, 1.0)
    ]


def test_a_query_on_the_table_sees_queued_rows(session):
    with import_session(session):
        parent = Parent(name="new")
        session.add(parent)
        plain_rows.add(session, Item, parent=parent)
        assert session.query(Unrelated).count() == 1
        assert queued(session)  # an unrelated query does not write them
        assert session.query(Item).filter_by(parent=parent).count() == 1
        assert not queued(session)

        plain_rows.add(session, Item, parent=parent)
        assert (
            session.query(Parent)
            .join(Parent.items)
            .filter(Parent.name == "new")
            .count()
            == 2
        )

        plain_rows.add(session, Item, parent=parent)
        assert (
            session.execute(
                sqlalchemy.select(sqlalchemy.func.count(Item.item_id))
            ).scalar()
            == 3
        )

        plain_rows.add(session, Item, parent=parent)
        assert (
            session.execute(text("SELECT count(*) FROM item")).scalar() == 4
        )  # unknown tables

        # the lazy load of a flushed parent's collection
        persistent = session.query(Parent).filter_by(name="persistent").one()
        plain_rows.add(session, Item, parent=persistent)
        assert len(persistent.items) == 1
        session.commit()


def test_an_orm_update_of_a_referenced_table_writes_the_rows_first(session):
    with import_session(session):
        persistent = session.query(Parent).filter_by(name="persistent").one()
        plain_rows.add(session, Item, parent=persistent)
        session.execute(sqlalchemy.update(Parent).values(name="renamed"))
        assert not queued(session)
        session.commit()


def test_rollback_discards_the_queue(session):
    with import_session(session):
        persistent = session.query(Parent).one()
        plain_rows.add(session, Item, parent=persistent)
        session.rollback()
        assert not queued(session)
        session.commit()
    assert item_rows(session) == []


def savepoint_scenario(session, add):
    persistent = session.query(Parent).one()
    new_parent = Parent(name="new")
    session.add(new_parent)
    add(session, parent=new_parent, without_default=1.0)
    savepoint = session.begin_nested()  # the rows so far go to the outer transaction
    add(session, parent=persistent, without_default=2.0)
    inner = session.begin_nested()
    add(session, parent=persistent, without_default=3.0)
    inner.commit()
    savepoint.rollback()
    add(session, parent=new_parent, without_default=4.0)
    savepoint = session.begin_nested()
    add(session, parent=persistent, without_default=5.0)
    savepoint.commit()
    session.commit()
    return item_rows(session)


def test_savepoints_keep_and_discard_the_same_rows_as_for_objects(session):
    engine = session.get_bind()
    with Session(engine) as orm_session:
        expected = savepoint_scenario(
            orm_session, lambda s, **values: s.add(Item(**values))
        )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM item"))
        connection.execute(text("DELETE FROM parent WHERE name = 'new'"))
    with import_session(session):
        actual = savepoint_scenario(
            session, lambda s, **values: plain_rows.add(s, Item, **values)
        )
    assert actual == expected
    assert [row.without_default for row in actual] == [1.0, 4.0, 5.0]


def test_rows_of_a_committed_savepoint_go_with_the_outer_rollback(session):
    with import_session(session):
        persistent = session.query(Parent).one()
        savepoint = session.begin_nested()
        plain_rows.add(session, Item, parent=persistent)
        savepoint.commit()
        session.rollback()
        assert not queued(session)
    assert item_rows(session) == []


def test_leaving_with_unwritten_rows_raises(session):
    persistent = session.query(Parent).one()
    with pytest.raises(RuntimeError, match="neither written"):
        with import_session(session):
            plain_rows.add(session, Item, parent=persistent)
    assert plain_rows._QUEUE_KEY not in session.info
    assert not sqlalchemy.event.contains(
        session, "before_commit", plain_rows._Queue.before_commit
    )


def test_queue_needs_the_context(session):
    with pytest.raises(RuntimeError, match="queue_inserts"):
        plain_rows.add(session, Item)


def test_referenced_objects_are_checked(session):
    with import_session(session):
        with pytest.raises(RuntimeError, match="not in the session"):
            plain_rows.add(session, Item, parent=Parent(name="transient"))

        expunged = Parent(name="expunged")
        session.add(expunged)
        plain_rows.add(session, Item, parent=expunged)
        session.expunge(expunged)
        with pytest.raises(RuntimeError, match="not in the session"):
            plain_rows.write(session)
        session.rollback()

        deleted = session.query(Description).one()
        plain_rows.add(session, Item, description=deleted)
        session.delete(deleted)
        with pytest.raises(RuntimeError, match="deleted"):
            session.flush()
        session.rollback()


def test_a_cascade_delete_of_the_referenced_object_deletes_the_rows(session):
    """As for objects: the cascade loads parent.items, which writes the rows first."""
    with import_session(session):
        parent = session.query(Parent).one()
        plain_rows.add(session, Item, parent=parent)
        session.delete(parent)
        session.commit()
    assert item_rows(session) == []


def test_collections_that_would_miss_the_rows_raise(session):
    with import_session(session):
        loaded = session.query(Parent).one()
        assert loaded.items == []
        with pytest.raises(RuntimeError, match="is loaded"):
            plain_rows.add(session, Item, parent=loaded)

        new = Parent(name="new")
        session.add(new)
        plain_rows.add(session, Item, parent=new)
        with pytest.raises(RuntimeError, match="would not contain"):
            new.items
        session.rollback()


def test_objects_of_a_queued_model_are_refused(session):
    with import_session(session):
        plain_rows.add(session, Item)
        with pytest.raises(RuntimeError, match="queued as plain rows"):
            session.add(Item())
        session.rollback()


def test_invalid_rows_and_models_are_refused(session):
    with import_session(session):
        with pytest.raises(TypeError, match="not columns"):
            plain_rows.add(session, Item, unknown=1)
        with pytest.raises(TypeError, match="not columns"):
            plain_rows.add(
                session, Item, server_column=1
            )  # the column name, not the attribute
        with pytest.raises(TypeError, match="foreign key"):
            plain_rows.add(session, Item, parent=None, parent_id=1)
        with pytest.raises(TypeError, match="many-to-one"):
            plain_rows.add(session, Parent, items=[])
        with pytest.raises(TypeError, match="listeners"):
            plain_rows.add(session, Listened, value=1)
        with pytest.raises(TypeError, match="listeners"):
            plain_rows.add(session, ListenedRaw, value=1)
        with pytest.raises(TypeError, match="validators"):
            plain_rows.add(session, Validated, value=1)
        assert not queued(session)


@pytest.mark.parametrize(
    "model",
    [
        datamodel.BudgetItem,
        datamodel.PopStatsBySpecies,
        datamodel.PopStatsByFaction,
        datamodel.PopStatsByJob,
        datamodel.PopStatsByStratum,
        datamodel.PopStatsByEthos,
        datamodel.PlanetStats,
    ],
)
def test_the_imported_models_can_be_inserted_as_plain_rows(model):
    plain_rows._model(model).check_listeners()


# the import framework: TimelineExtractor writes or discards the rows of each processor


class _QueuesBudget(timeline.AbstractGamestateDataProcessor):
    ID = "budget"
    NAMES = ["a", "b"]

    def extract_data_from_gamestate(self, dependencies):
        country = datamodel.Country(
            game=self._db_game, country_id_in_game=1, country_name=self.ID
        )
        country_data = datamodel.CountryData(
            country=country,
            game_state=self._db_gamestate,
            date=self._basic_info.date_in_days,
        )
        self._session.add(country_data)
        for name in self.NAMES:
            self._add_plain_row(
                datamodel.BudgetItem,
                country_data=country_data,
                db_budget_item_name=self._get_or_add_shared_description(name),
                net_energy=None,  # the column default 0.0 applies, as for the ORM
                net_minerals=1.0,
            )
        # an early return: nothing here writes the rows

    def data(self):
        return self.ID


class _FailsAfterQueueing(_QueuesBudget):
    ID = "failing"
    NAMES = ["lost"]

    def extract_data_from_gamestate(self, dependencies):
        super().extract_data_from_gamestate(dependencies)
        raise RuntimeError("boom")


class _ReadsBudget(timeline.AbstractGamestateDataProcessor):
    ID = "reader"
    DEPENDENCIES = ["budget"]

    def extract_data_from_gamestate(self, dependencies):
        self.names = sorted(
            item.name for item in self._session.query(datamodel.BudgetItem).all()
        )

    def data(self):
        return self.names


@pytest.fixture
def extractor(tmp_path, monkeypatch):
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    datamodel.Base.metadata.create_all(engine)
    session_factory = sqlalchemy.orm.sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_db_session(game_id, write=False):
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(datamodel, "get_db_session", get_db_session)
    monkeypatch.setattr(config.CONFIG, "debug_mode", False)
    extractor = timeline.TimelineExtractor()
    extractor.session_factory = session_factory
    return extractor


def test_the_import_writes_each_processors_rows_and_discards_a_failed_ones(
    extractor, monkeypatch
):
    reader = _ReadsBudget()
    monkeypatch.setattr(
        extractor,
        "_data_processors",
        lambda: iter([_FailsAfterQueueing(), _QueuesBudget(), reader]),
    )
    extractor.process_gamestate(
        "g", {"date": "2300.01.01", "galaxy": {}, "country": {}}
    )

    assert [f["processor"] for f in extractor.failures] == ["failing"]
    assert reader.names == ["a", "b"]  # the next processor reads the rows
    with extractor.session_factory() as s:
        rows = s.execute(
            text(
                "SELECT b.net_energy, b.net_minerals, b.net_food, d.text, cd.country_data_id "
                "FROM budget_item b JOIN shared_description d "
                "ON b.budget_item_description_id = d.description_id "
                "JOIN country_data cd ON b.country_data_id = cd.country_data_id "
                "ORDER BY b.budget_item_id"
            )
        ).all()
    assert [tuple(row[:4]) for row in rows] == [
        (0.0, 1.0, 0.0, "a"),
        (0.0, 1.0, 0.0, "b"),
    ]
