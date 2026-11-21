"""Flush a session only before statements that read tables with unflushed changes.

With SQLAlchemy's autoflush, every query that follows a change writes all pending
changes first: during a save import that is over a thousand flushes per save, each a
full unit-of-work run. ``flush_before_reads`` turns autoflush off and installs a
``do_orm_execute`` listener that flushes before a statement only if it may read a row
that differs between the session and the database:

* a table the statement names (FROM list, joins, subqueries, column references, the
  target of an UPDATE/DELETE, the relationships in loader options such as
  ``joinedload`` and their ``.and_()`` criteria) may be written by the flush of a new,
  modified or deleted object in the session: its own tables, and those that its
  relationships write (the foreign keys of a one-to-many collection's members, which
  have no back reference to be dirty themselves, or are nulled when the object is
  deleted; delete-orphan members of a cleared collection; many-to-many association rows);
* or a table the statement names has a foreign key to a table with new objects: a filter
  on a relationship to a new object (``filter_by(country=new_country)``) needs its
  primary key;
* or the tables cannot be told (textual SQL, also as a fragment next to known tables,
  ``sqlalchemy.table()`` clauses without metadata, literal column expressions, loader
  options not known here), so the statement may read anything.

Any other statement reads the same rows with or without a flush first. As with
autoflush, this covers lazy loads, ``Session.get()`` and ORM bulk UPDATE/DELETE, which
all go through ``do_orm_execute``. It does NOT cover, so the code using the session has
to take care of it:

* ``Session.refresh()`` and ``Session.expire()`` discard unflushed changes of the object
  before any statement is sent; flush first where those changes must survive.
* Attributes that only a flush fills in: database-generated primary keys, foreign key
  columns set through a relationship, and column defaults. ``obj.country_id`` of an
  object added since the last flush is None, where autoflush might already have flushed
  it because some unrelated query ran in between.
* Statements sent past the ORM session (``session.connection().execute(...)``).

The pending objects are tracked with the public ``transient_to_pending`` event, modified
and deleted ones with the public ``Session.identity_map.check_modified()``,
``Session.dirty`` and ``Session.deleted``. Private SQLAlchemy API is used in two places
without a public equivalent: ``Session._flushing`` and ``Executable._with_options`` (with
the ``path`` and ``_extra_criteria`` of each ``Load`` option's elements).
test/pending_flush_test.py covers both; if they change in a future SQLAlchemy release the
listener raises, or flushes for a path token it does not know, rather than skipping a flush.
"""

import contextlib
import re
from typing import Dict, FrozenSet, Iterator, Optional, Set

import sqlalchemy
from sqlalchemy.orm import (
    ColumnProperty,
    Load,
    Mapper,
    ORMExecuteState,
    RelationshipProperty,
    Session,
    UserDefinedOption,
)
from sqlalchemy.orm.interfaces import ONETOMANY
from sqlalchemy.orm.util import AliasedInsp
from sqlalchemy.sql import visitors

_TRACKER_KEY = "pending_flush_tracker"

# per mapped class: its tables, and the tables a flush of one of its objects may write;
# per table: the tables it has foreign keys to
_tables_of_class: Dict[type, FrozenSet[sqlalchemy.Table]] = {}
_written_tables_of_class: Dict[type, FrozenSet[sqlalchemy.Table]] = {}
_referenced_tables: Dict[sqlalchemy.Table, FrozenSet[sqlalchemy.Table]] = {}


def _mapped_tables(obj) -> FrozenSet[sqlalchemy.Table]:
    cls = type(obj)
    tables = _tables_of_class.get(cls)
    if tables is None:
        tables = _tables_of_class[cls] = frozenset(sqlalchemy.inspect(cls).tables)
    return tables


def _written_tables(obj) -> FrozenSet[sqlalchemy.Table]:
    """The tables that the flush of a new, modified or deleted object may write.

    Besides its own tables: the members' tables of its one-to-many relationships (their
    foreign keys are set when they are added or moved, set to NULL when removed or when
    the object is deleted, and delete-orphan members are deleted) and the association
    tables of its many-to-many relationships. Many-to-one relationships only write the
    object's own foreign key columns.
    """
    cls = type(obj)
    tables = _written_tables_of_class.get(cls)
    if tables is None:
        mapper = sqlalchemy.inspect(cls)
        written = set(mapper.tables)
        for rel in mapper.relationships:
            if rel.viewonly:
                continue
            if rel.direction is ONETOMANY:
                written.update(rel.mapper.tables)
            if rel.secondary is not None:
                written.add(rel.secondary)
        tables = _written_tables_of_class[cls] = frozenset(written)
    return tables


def _tables_referenced_by(table) -> FrozenSet[sqlalchemy.Table]:
    referenced = _referenced_tables.get(table)
    if referenced is None:
        referenced = _referenced_tables[table] = frozenset(
            fk.column.table for fk in getattr(table, "foreign_keys", ())
        )
    return referenced


class _Tracker:
    """The tables of the objects added to the session since its last flush.

    ``new_tables`` are the objects' own tables, which get new primary keys;
    ``written_tables`` also those that their relationships write (see ``_written_tables``).

    Kept up to date by the session's ``transient_to_pending`` event, so the listener needs
    neither the private ``Session._new`` nor the public ``Session.new``, which copies all
    pending objects on every access. The set may name a table whose new objects were
    expunged again (it only shrinks after a flush or rollback); that costs at most an extra
    flush, never a missed one.
    """

    def __init__(self, session: Session):
        self.new_tables: Set[sqlalchemy.Table] = set()
        self.written_tables: Set[sqlalchemy.Table] = set()
        self.reset(session)

    def reset(self, session: Session, *args) -> None:
        new = session.new
        self.new_tables = {t for obj in new for t in _mapped_tables(obj)}
        self.written_tables = {t for obj in new for t in _written_tables(obj)}

    def object_added(self, session: Session, instance) -> None:
        self.new_tables.update(_mapped_tables(instance))
        self.written_tables.update(_written_tables(instance))


def _load_path_tables(option: Load) -> Iterator[sqlalchemy.Table]:
    """The tables that a loader option such as joinedload(A.b).joinedload(B.c) loads from.

    Raises _UnknownTables for anything it does not recognise.
    """
    for element in option.context:
        # a path alternates entities and attributes: Mapper, relationship, Mapper, ...
        for token in element.path:
            if isinstance(token, RelationshipProperty):
                yield from token.mapper.tables
                if token.secondary is not None:
                    yield token.secondary
            elif isinstance(token, Mapper):
                yield from token.tables
            elif isinstance(token, AliasedInsp):
                for mapper in token.with_polymorphic_mappers or (token.mapper,):
                    yield from mapper.tables
            elif isinstance(token, ColumnProperty):
                pass  # load_only, defer: reads the entity's own tables
            elif isinstance(token, str) and token.startswith("column:"):
                pass  # the column wildcard that load_only adds
            else:
                # e.g. a relationship wildcard, Load(A).joinedload("*"): any relationship
                # of the entity; or a token type of a newer SQLAlchemy release
                raise _UnknownTables
        # criteria of a relationship.and_(...) in the option, e.g. joinedload(A.b.and_(...))
        try:
            extra_criteria = element._extra_criteria
        except AttributeError:
            raise _UnknownTables
        for criterion in extra_criteria:
            yield from _find_tables(criterion)


class _UnknownTables(Exception):
    pass


# literal columns that cannot read a table the statement does not name: *, 1, a.b
_PLAIN_LITERAL = re.compile(r"[\w.*]+")


def _find_tables(clause) -> Set[sqlalchemy.Table]:
    """Like sqlalchemy.sql.util.find_tables(check_columns=True, include_crud=True), but
    raises _UnknownTables for SQL text whose tables cannot be told."""
    tables: Set[sqlalchemy.Table] = set()

    def visit_table(table):
        if not isinstance(table, sqlalchemy.Table):
            raise _UnknownTables  # sqlalchemy.table(): may name a table with new rows
        tables.add(table)

    def visit_column(column):
        table = column.table
        if table is None:
            if column.is_literal and not _PLAIN_LITERAL.fullmatch(column.name):
                raise _UnknownTables  # literal_column("(select ...)")
        elif isinstance(table, sqlalchemy.TableClause):
            visit_table(table)
        # columns of subqueries, aliases and joins: their tables are visited by themselves

    def visit_crud(statement):
        visit_table(statement.table)

    def visit_text(text):
        raise _UnknownTables

    visitors.traverse(
        clause,
        {},
        {
            "table": visit_table,
            "column": visit_column,
            "insert": visit_crud,
            "update": visit_crud,
            "delete": visit_crud,
            "textclause": visit_text,
        },
    )
    return tables


def statement_tables(statement) -> Optional[Set[sqlalchemy.Table]]:
    """The tables a statement may read or write, or None if that cannot be told.

    ``find_tables`` traverses the whole statement, including ORM join targets, subqueries,
    aliases and columns of WHERE/ORDER BY clauses. Tables that ORM loader options only add
    at compile time are taken from the options; options that this module does not know
    (wildcard loaders, ``with_loader_criteria``) make the tables unknown, as does SQL text
    anywhere in the statement.
    """
    try:
        tables = _find_tables(statement)
        for option in statement._with_options:
            if isinstance(option, Load):
                tables.update(_load_path_tables(option))
            elif not isinstance(option, UserDefinedOption):
                return None
    except _UnknownTables:
        return None
    return tables


def _flush_needed(session: Session, tracker: _Tracker, statement) -> bool:
    new_tables = tracker.new_tables
    modified = session.identity_map.check_modified()
    deleted = session.deleted
    if not (new_tables or modified or deleted):
        return False
    tables = statement_tables(statement)
    if not tables:
        return True  # textual SQL or unknown loader options: it may read anything
    if new_tables:
        if not tracker.written_tables.isdisjoint(tables):
            return True
        for table in tables:
            if not new_tables.isdisjoint(_tables_referenced_by(table)):
                return True
    if modified or deleted:
        changed = set()
        for obj in session.dirty:
            changed.update(_written_tables(obj))
        for obj in deleted:
            changed.update(_written_tables(obj))
        if not changed.isdisjoint(tables):
            return True
    return False


def _before_statement(orm_execute_state: ORMExecuteState) -> None:
    session = orm_execute_state.session
    if session.autoflush:
        return  # autoflush was turned back on: the session flushes by itself
    if session._flushing:
        return  # sent by a flush hook such as before_flush, during the flush
    tracker = session.info[_TRACKER_KEY]
    if _flush_needed(session, tracker, orm_execute_state.statement):
        # SQLAlchemy runs do_orm_execute listeners before its own autoflush and before an
        # ORM UPDATE/DELETE matches the session's objects (synchronize_session), so this
        # flush happens at the same point as an autoflush would
        session.flush()


_SESSION_EVENTS = (
    ("transient_to_pending", "object_added"),
    ("after_flush_postexec", "reset"),
    ("after_soft_rollback", "reset"),
    ("after_commit", "reset"),
)


@contextlib.contextmanager
def flush_before_reads(session: Session):
    """Within the block, the session flushes only before statements that read unflushed rows.

    Autoflush is turned off and restored afterwards, and the listeners are removed again:
    sessions from ``datamodel.get_db_session`` are reused by later readers in the same thread.
    """
    if _TRACKER_KEY in session.info:
        raise RuntimeError("flush_before_reads is already active for this session")
    tracker = _Tracker(session)
    session.info[_TRACKER_KEY] = tracker
    listeners = [(name, getattr(tracker, method)) for name, method in _SESSION_EVENTS]
    listeners.append(("do_orm_execute", _before_statement))
    for name, fn in listeners:
        sqlalchemy.event.listen(session, name, fn)
    autoflush = session.autoflush
    session.autoflush = False
    try:
        yield session
    finally:
        session.autoflush = autoflush
        for name, fn in listeners:
            sqlalchemy.event.remove(session, name, fn)
        del session.info[_TRACKER_KEY]
