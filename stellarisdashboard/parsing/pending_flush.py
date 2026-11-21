"""Flush a session only before statements that read tables with unflushed changes.

With SQLAlchemy's autoflush, every query that follows a change writes all pending
changes first: during a save import that is over a thousand flushes per save, each a
full unit-of-work run. ``flush_before_reads`` turns autoflush off and installs a
``do_orm_execute`` listener that flushes before a statement only if it may read a row
that differs between the session and the database:

* a table the statement names (FROM list, joins, subqueries, column references, the
  target of an UPDATE/DELETE, and the relationships in loader options such as
  ``joinedload``) has new, modified or deleted objects in the session;
* or a table the statement names has a foreign key to a table with new objects: a filter
  on a relationship to a new object (``filter_by(country=new_country)``) needs its
  primary key;
* or the tables cannot be told (textual SQL, loader options not known here), so the
  statement may read anything.

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
the ``path`` of each ``Load`` option). test/pending_flush_test.py covers both; if they
change in a future SQLAlchemy release the listener raises rather than skipping a flush.
"""

import contextlib
from typing import Dict, FrozenSet, Iterator, Optional, Set

import sqlalchemy
from sqlalchemy.orm import (
    Load,
    Mapper,
    ORMExecuteState,
    RelationshipProperty,
    Session,
    UserDefinedOption,
)
from sqlalchemy.orm.util import AliasedInsp
from sqlalchemy.sql import util as sql_util

_TRACKER_KEY = "pending_flush_tracker"

# per mapped class: its tables; per table: the tables it has foreign keys to
_tables_of_class: Dict[type, FrozenSet[sqlalchemy.Table]] = {}
_referenced_tables: Dict[sqlalchemy.Table, FrozenSet[sqlalchemy.Table]] = {}


def _mapped_tables(obj) -> FrozenSet[sqlalchemy.Table]:
    cls = type(obj)
    tables = _tables_of_class.get(cls)
    if tables is None:
        tables = _tables_of_class[cls] = frozenset(sqlalchemy.inspect(cls).tables)
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

    Kept up to date by the session's ``transient_to_pending`` event, so the listener needs
    neither the private ``Session._new`` nor the public ``Session.new``, which copies all
    pending objects on every access. The set may name a table whose new objects were
    expunged again (it only shrinks after a flush or rollback); that costs at most an extra
    flush, never a missed one.
    """

    def __init__(self, session: Session):
        self.new_tables: Set[sqlalchemy.Table] = set()
        self.reset(session)

    def reset(self, session: Session, *args) -> None:
        self.new_tables = {t for obj in session.new for t in _mapped_tables(obj)}

    def object_added(self, session: Session, instance) -> None:
        self.new_tables.update(_mapped_tables(instance))


def _load_path_tables(option: Load) -> Iterator[sqlalchemy.Table]:
    """The tables that a loader option such as joinedload(A.b).joinedload(B.c) loads from."""
    for element in option.context:
        # a path alternates entities and attributes: Mapper, relationship, Mapper, ...
        for token in element.path:
            if isinstance(token, RelationshipProperty):
                yield from token.mapper.tables
                if token.secondary is not None:
                    yield token.secondary
            elif isinstance(token, (Mapper, AliasedInsp)):
                yield from token.mapper.tables
            elif isinstance(token, str):
                # a wildcard, e.g. Load(A).joinedload("*"): any relationship of the entity
                raise _UnknownTables
            # column attributes (load_only, defer) read the entity's own tables


class _UnknownTables(Exception):
    pass


def statement_tables(statement) -> Optional[Set[sqlalchemy.Table]]:
    """The tables a statement may read or write, or None if that cannot be told.

    ``find_tables`` traverses the whole statement, including ORM join targets, subqueries,
    aliases and columns of WHERE/ORDER BY clauses. Tables that ORM loader options only add
    at compile time are taken from the options; options that this module does not know
    (wildcard loaders, ``with_loader_criteria``) make the tables unknown.
    """
    tables = set(sql_util.find_tables(statement, check_columns=True, include_crud=True))
    for option in statement._with_options:
        if isinstance(option, Load):
            try:
                tables.update(_load_path_tables(option))
            except _UnknownTables:
                return None
        elif not isinstance(option, UserDefinedOption):
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
        if not new_tables.isdisjoint(tables):
            return True
        for table in tables:
            if not new_tables.isdisjoint(_tables_referenced_by(table)):
                return True
    if modified or deleted:
        changed = set()
        for obj in session.dirty:
            changed.update(_mapped_tables(obj))
        for obj in deleted:
            changed.update(_mapped_tables(obj))
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
