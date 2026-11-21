"""The import session's flush listener, see stellarisdashboard/parsing/pending_flush.py.

A minimal model: parents with children (one-to-many), tags linked to parents through an
association table (many-to-many), and an unrelated table.
"""

import pytest
import sqlalchemy
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import (
    Session,
    aliased,
    declarative_base,
    joinedload,
    relationship,
    selectinload,
)

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import pending_flush

Base = declarative_base()

parent_tag = Table(
    "parent_tag",
    Base.metadata,
    Column("parent_id", ForeignKey("parent.parent_id"), primary_key=True),
    Column("tag_id", ForeignKey("tag.tag_id"), primary_key=True),
)


class Parent(Base):
    __tablename__ = "parent"
    parent_id = Column(Integer, primary_key=True)
    name = Column(String)
    children = relationship("Child", back_populates="parent", cascade="all")
    tags = relationship("Tag", secondary=parent_tag)


class Child(Base):
    __tablename__ = "child"
    child_id = Column(Integer, primary_key=True)
    parent_id = Column(ForeignKey("parent.parent_id"))
    name = Column(String)
    parent = relationship("Parent", back_populates="children")


class Tag(Base):
    __tablename__ = "tag"
    tag_id = Column(Integer, primary_key=True)
    name = Column(String)


class Unrelated(Base):
    __tablename__ = "unrelated"
    unrelated_id = Column(Integer, primary_key=True)
    name = Column(String)


@pytest.fixture
def session():
    engine = sqlalchemy.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Parent(name="p1", children=[Child(name="c1")]), Unrelated(name="u")])
        s.commit()
        yield s


@pytest.fixture
def flushes(session):
    """The number of flushes so far, as a list of one element."""
    count = [0]

    def count_flush(*args):
        count[0] += 1

    sqlalchemy.event.listen(session, "after_flush", count_flush)
    return count


def test_new_row_is_visible_to_a_query_on_its_table(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.add(Child(name="new"))
        names = {c.name for c in session.query(Child)}
    assert names == {"c1", "new"}
    assert flushes[0] == 1


def test_query_on_an_unrelated_table_does_not_flush(session, flushes):
    with pending_flush.flush_before_reads(session):
        new = Child(name="new")
        session.add(new)
        assert session.query(Unrelated).count() == 1
        assert session.query(Tag).all() == []
        assert flushes[0] == 0
        assert new.child_id is None  # still pending
    assert flushes[0] == 0


def test_autoflush_is_restored_and_listeners_removed(session, flushes):
    assert session.autoflush
    with pending_flush.flush_before_reads(session):
        assert not session.autoflush
    assert session.autoflush
    assert pending_flush._TRACKER_KEY not in session.info
    # the plain autoflush behaviour again: any query flushes
    session.add(Child(name="new"))
    session.query(Unrelated).all()
    assert flushes[0] == 1


def test_listeners_are_removed_after_an_exception(session):
    with pytest.raises(ValueError):
        with pending_flush.flush_before_reads(session):
            raise ValueError
    assert session.autoflush
    assert not sqlalchemy.event.contains(
        session, "do_orm_execute", pending_flush._before_statement
    )


def test_filter_on_a_relationship_to_a_new_object(session, flushes):
    with pending_flush.flush_before_reads(session):
        parent = Parent(name="p2")
        session.add(parent)
        child = Child(name="c2", parent=parent)
        session.add(child)
        assert session.query(Child).filter_by(parent=parent).one() is child
        assert session.query(Child).filter(Child.parent == parent).one() is child
    assert flushes[0] == 1


def test_query_referencing_a_table_with_new_rows_flushes(session, flushes):
    """Child references parent: a new parent's primary key may be needed by the query."""
    with pending_flush.flush_before_reads(session):
        parent = Parent(name="p2")
        session.add(parent)
        assert session.query(Child).filter(Child.parent == parent).all() == []
        assert parent.parent_id is not None
    assert flushes[0] == 1


def test_modified_row_is_respected(session, flushes):
    with pending_flush.flush_before_reads(session):
        child = session.query(Child).one()
        child.name = "renamed"
        session.query(Unrelated).all()
        assert flushes[0] == 0
        assert session.query(Child.name).filter(Child.name == "renamed").scalar()
        assert flushes[0] == 1


def test_deleted_row_is_respected(session, flushes):
    with pending_flush.flush_before_reads(session):
        child = session.query(Child).one()
        session.delete(child)
        session.query(Unrelated).all()
        assert flushes[0] == 0
        assert session.query(Child).count() == 0
        assert flushes[0] == 1


def test_lazy_load_sees_new_child(session, flushes):
    with pending_flush.flush_before_reads(session):
        parent = session.query(Parent).one()
        session.add(Child(name="c2", parent_id=parent.parent_id))
        assert {c.name for c in parent.children} == {"c1", "c2"}
        assert flushes[0] == 1


def test_get_finds_a_new_object(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.add(Unrelated(unrelated_id=42, name="new"))
        assert session.get(Unrelated, 42).name == "new"


def test_join_targets_are_found(session, flushes):
    with pending_flush.flush_before_reads(session):
        parent = session.query(Parent).one()
        parent.name = "renamed"
        q = session.query(Child).join(Parent).filter(Parent.name == "renamed")
        assert q.one().name == "c1"
        assert flushes[0] == 1
        parent.name = "renamed again"
        # the join target is the only place that names the parent table
        q = session.query(Child.child_id).join(Child.parent)
        assert {t.name for t in pending_flush.statement_tables(q.statement)} >= {
            "parent"
        }
        q.all()
        assert flushes[0] == 2


def test_subquery_and_alias_tables_are_found(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.add(Child(name="c2", parent_id=1))
        sub = sqlalchemy.select(Child.parent_id).scalar_subquery()
        assert session.query(Parent).filter(Parent.parent_id.in_(sub)).count() == 1
        assert flushes[0] == 1
        session.add(Child(name="c3", parent_id=1))
        child_alias = aliased(Child)
        assert session.query(child_alias).count() == 3
        assert flushes[0] == 2
        session.add(Child(name="c4", parent_id=1))
        exists = sqlalchemy.exists().where(Child.parent_id == Parent.parent_id)
        assert session.query(Parent.name).filter(exists).count() == 1
        assert flushes[0] == 3


def test_many_to_many(session, flushes):
    with pending_flush.flush_before_reads(session):
        parent = session.query(Parent).one()
        tag = Tag(name="t")
        parent.tags.append(tag)
        assert session.query(Parent).filter(Parent.tags.contains(tag)).one() is parent
        assert flushes[0] == 1


def test_eager_loader_options_name_their_tables(session, flushes):
    with pending_flush.flush_before_reads(session):
        child = session.query(Child).one()
        child.parent_id = None  # moved away from the parent, in memory only
        assert flushes[0] == 0
        session.expunge_all()  # not loaded yet, so the eager load fills the collection
        child = session.get(Child, child.child_id)
        child.parent_id = None
        for loader in (joinedload, selectinload):
            parent = (
                session.query(Parent)
                .options(loader(Parent.children))
                .populate_existing()
                .one()
            )
            assert parent.children == []
        assert flushes[0] == 1
        statement = session.query(Parent).options(joinedload(Parent.tags)).statement
        names = {t.name for t in pending_flush.statement_tables(statement)}
        assert names == {"parent", "parent_tag", "tag"}
        # options it does not know make the tables unknown: such a statement always flushes
        statement = session.query(Parent).options(joinedload("*")).statement
        assert pending_flush.statement_tables(statement) is None
        session.add(Unrelated(name="new"))
        session.query(Parent).options(joinedload("*")).all()
        assert flushes[0] == 2


def _bulk_dml_scenario(session):
    """A new and a modified child, then bulk UPDATE and DELETE statements on children."""
    new = Child(name="new")
    session.add(new)
    old = session.query(Child).filter_by(name="c1").one()
    old.name = "old"
    session.query(Child).filter(Child.name == "old").update({"name": "updated"})
    session.query(Child).filter(Child.name == "new").delete()
    return (
        old.name,
        new in session,
        sorted(name for (name,) in session.query(Child.name)),
    )


def test_bulk_update_and_delete_match_autoflush(session, flushes):
    """synchronize_session sees the same objects as with autoflush."""
    expected = _bulk_dml_scenario(session)
    session.rollback()
    with pending_flush.flush_before_reads(session):
        assert _bulk_dml_scenario(session) == expected
    assert expected == ("updated", False, ["updated"])


def test_bulk_delete_sees_new_rows(session, flushes):
    with pending_flush.flush_before_reads(session):
        child = Child(name="c2")
        session.add(child)
        session.query(Child).delete()
        assert flushes[0] == 1
        assert child not in session
        assert session.query(Child).count() == 0


def test_textual_sql_flushes(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.add(Unrelated(name="new"))
        assert (
            session.execute(sqlalchemy.text("select count(*) from unrelated")).scalar()
            == 2
        )
        assert flushes[0] == 1


def test_no_flush_without_changes(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.query(Child).all()
        session.query(Parent).filter(Parent.children.any()).all()
    assert flushes[0] == 0


def test_tracking_after_flush_and_rollback(session, flushes):
    with pending_flush.flush_before_reads(session):
        session.add(Child(name="c2"))
        session.flush()
        tracker = session.info[pending_flush._TRACKER_KEY]
        assert not tracker.new_tables
        session.query(Child).all()
        assert flushes[0] == 1
        savepoint = session.begin_nested()
        session.add(Child(name="c3"))
        savepoint.rollback()
        assert not tracker.new_tables
        assert session.query(Child).count() == 2
        session.add(Child(name="c4"))
        session.rollback()
        assert not tracker.new_tables
        assert session.query(Child).count() == 1


def test_expunged_object_still_counts_until_the_next_flush(session, flushes):
    """Expunging a new object and adding one of another table must not hide the new table."""
    with pending_flush.flush_before_reads(session):
        unrelated = Unrelated(name="gone")
        session.add(unrelated)
        session.expunge(unrelated)
        session.add(Child(name="c2"))
        assert session.query(Child).count() == 2


def test_statements_from_flush_hooks_do_not_flush(session, flushes):
    """before_flush hooks may query (see datamodel.drop_special_country_events)."""
    seen = []

    def before_flush(s, flush_context, instances):
        seen.append(s.get(Parent, 1).name)

    sqlalchemy.event.listen(session, "before_flush", before_flush)
    with pending_flush.flush_before_reads(session):
        session.expire_all()
        session.add(Child(name="c2"))
        assert session.query(Child).count() == 2
    assert seen == ["p1"]


def test_refresh_discards_unflushed_changes(session):
    """Documented limitation: refresh() expires before any statement is sent."""
    with pending_flush.flush_before_reads(session):
        parent = session.query(Parent).one()
        parent.name = "renamed"
        session.refresh(parent)
        assert parent.name == "p1"


def test_cannot_be_nested(session):
    with pending_flush.flush_before_reads(session):
        with pytest.raises(RuntimeError):
            with pending_flush.flush_before_reads(session):
                pass


def test_import_datamodel_relationship_filter():
    """The real datamodel: filtering events by a new country, with the special-country hook."""
    engine = sqlalchemy.create_engine("sqlite://")
    datamodel.Base.metadata.create_all(engine)
    with Session(engine) as session:
        sqlalchemy.event.listen(
            session, "before_flush", datamodel.drop_special_country_events
        )
        with pending_flush.flush_before_reads(session):
            game = datamodel.Game(game_name="g")
            country = datamodel.Country(
                game=game,
                country_id_in_game=1,
                country_type="default",
                country_name="empire",
                is_player=False,
            )
            session.add(country)
            event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.empire_renamed,
                country=country,
                start_date_days=0,
            )
            session.add(event)
            found = (
                session.query(datamodel.HistoricalEvent)
                .filter_by(country=country)
                .one()
            )
            assert found is event
            assert country.country_id is not None
