"""Flush the import session only before statements that read tables with unflushed changes.

With the session's own autoflush, every query that follows a ``session.add`` writes that one
object to the database: about 1,900 flushes per save, each with its own unit-of-work run. The
import session therefore runs without autoflush, and ``flush_before_reading_pending_tables``
(a ``do_orm_execute`` listener) flushes before a statement only if a table it names has
pending, modified or deleted rows, or references a table with pending rows (a filter on a
relationship to a new object needs its primary key). Other statements see the same rows either
way.
"""

import itertools
from typing import Dict, FrozenSet, Set, Tuple

import sqlalchemy
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import util as sql_util

_INFO_KEY = "pending_flush_state"
_SUSPENDED_KEY = "pending_flush_suspended"


def get_stored(session: Session, model, primary_key):
    """session.get() without a flush: a stored row is found by its primary key whatever is pending."""
    suspended = session.info.get(_SUSPENDED_KEY)
    session.info[_SUSPENDED_KEY] = True
    try:
        return session.get(model, primary_key)
    finally:
        session.info[_SUSPENDED_KEY] = suspended


def _unflushed_tables(session: Session) -> Tuple[Set[sqlalchemy.Table], Set[sqlalchemy.Table]]:
    """The tables of the session's (new, modified and deleted) objects, and of the new ones alone.

    Called before every statement, so the result is kept between calls and only the objects
    added since the last call are looked at. Between two flushes the three collections only
    grow (nothing is expunged or expired outside of a flush), a flush empties them.
    """
    new = session._new
    modified = session.identity_map._modified
    deleted = session._deleted
    sizes = (len(new), len(modified), len(deleted))
    cached = session.info.get(_INFO_KEY)
    if cached is not None and cached[0] == sizes:
        return cached[1], cached[2]
    if cached is None or any(now < before for now, before in zip(sizes, cached[0])):
        changed, added = set(), set()
        seen_new = 0
    else:
        _, changed, added = cached
        seen_new = cached[0][0]
    for state in itertools.islice(new, seen_new, None):
        added.update(state.mapper.tables)
    changed.update(added)
    for state in modified:
        changed.update(state.mapper.tables)
    for state in deleted:
        changed.update(state.mapper.tables)
    session.info[_INFO_KEY] = (sizes, changed, added)
    return changed, added


_referenced_tables: Dict[sqlalchemy.Table, FrozenSet[sqlalchemy.Table]] = {}


def _tables_referenced_by(table: sqlalchemy.Table) -> FrozenSet[sqlalchemy.Table]:
    referenced = _referenced_tables.get(table)
    if referenced is None:
        referenced = _referenced_tables[table] = frozenset(
            fk.column.table for fk in getattr(table, "foreign_keys", ())
        )
    return referenced


def _forget_unflushed_tables(session: Session, *args) -> None:
    session.info.pop(_INFO_KEY, None)


def _statement_tables(statement) -> Set[sqlalchemy.Table]:
    """The tables a statement names, anywhere: FROM list, joins, subqueries and columns."""
    tables = set(sql_util.find_tables(statement, check_columns=True, include_crud=True))
    # Query.join(Model) / join(Model.relationship) targets are not part of the FROM list yet
    for join in getattr(statement, "_setup_joins", ()):
        for element in join[:2]:
            if element is None:
                continue
            inspected = sqlalchemy.inspect(element, raiseerr=False)
            mapper = getattr(inspected, "mapper", None)
            if mapper is not None:
                tables.update(mapper.tables)
            else:
                tables.update(sql_util.find_tables(element, check_columns=True))
    return tables


def flush_before_reading_pending_tables(orm_execute_state: ORMExecuteState) -> None:
    session = orm_execute_state.session
    if (
        session.autoflush
        or session._flushing
        or orm_execute_state.is_insert
        or session.info.get(_SUSPENDED_KEY)
    ):
        return  # the session flushes by itself, or the statement comes from the flush
    changed, added = _unflushed_tables(session)
    if not changed:
        return
    tables = _statement_tables(orm_execute_state.statement)
    for mapper in orm_execute_state.all_mappers:
        tables.update(mapper.tables)
    # a statement without a known table (textual SQL) may read anything
    if not tables or not changed.isdisjoint(tables):
        session.flush()
    elif added and any(not added.isdisjoint(_tables_referenced_by(table)) for table in tables):
        # filter_by(relationship=new_object) needs the primary key of the new object
        session.flush()


def enable(session: Session) -> None:
    """Listen on the session; the listener is inactive while the session's autoflush is on."""
    sqlalchemy.event.listen(session, "do_orm_execute", flush_before_reading_pending_tables)
    for event in ("after_flush_postexec", "after_soft_rollback", "after_commit"):
        sqlalchemy.event.listen(session, event, _forget_unflushed_tables)
