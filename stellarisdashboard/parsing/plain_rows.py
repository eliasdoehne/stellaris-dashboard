"""Insert rows of append-only tables with one executemany per table, not as ORM objects.

Some tables of the import only ever get new rows (budget items, pop statistics): nothing
reads or changes them as objects while a save is imported. Adding them to the session costs
an object, its instrumentation and a unit-of-work entry per row. ``queue_inserts`` lets the
import queue such rows as plain values instead, given as for the model's constructor
(``add(session, BudgetItem, country_data=cd, net_energy=1.0)``), and inserts them with one
Core ``executemany`` per table and key set.

The rows are written:

* by ``write()``, which the import framework calls at the end of every processor;
* before the session's commit, nested savepoints included (``before_commit``), so rows can
  not be left behind by a code path that forgets ``write()``;
* before any ORM statement that may read a table with queued rows (or whose tables cannot
  be told), and before an ORM UPDATE/DELETE of a table the queued rows refer to, in the same
  way as ``pending_flush`` flushes before reads;

* and at ``begin_nested()``, into the enclosing transaction, as ``begin_nested()`` flushes
  pending objects there.

So the queued rows always belong to the innermost transaction, and a rollback discards
them, like the pending objects added within it; rows written before are rolled back by the
database. Leaving ``queue_inserts`` with rows that were neither written nor rolled back
raises.

The INSERT has the same values as the ORM's (persistence._collect_insert_commands): a None
value is left out, so that the column's default or server default applies, except for
columns whose type evaluates None; columns without any default that got no value are set to
NULL explicitly. Relationships (many-to-one only) are turned into their foreign key values;
the session is flushed first, so that new referenced objects have their primary keys.
Rows of one table are inserted in the order they were queued, so they get the same primary
keys as objects added in that order.

What the ORM would also do and this does not, and how it is guarded:

* Mapper, instance and attribute events of the model (``before_insert``, ``init``,
  validators, attribute ``set`` listeners): a model with any listener that is not
  SQLAlchemy's own is refused.
* The back-populated collection of a referenced object (``country_data.budget``) does not
  get the row in memory. Rows are written before the collection is loaded from the
  database; if it is already loaded, or is initialised without a load (a pending object),
  while the object's rows are queued, that raises.
* The save-update cascade along the relationship: a referenced object must already be in
  the session, and must not be deleted before the rows are written.
* An object of the same model added to the session while its rows are queued: refused, its
  primary key would not follow the queue order.
* Session-level hooks such as ``before_flush`` do not see the rows.
"""

import contextlib
import dataclasses
import itertools
from typing import Any, Dict, List, Optional, Set, Tuple

import sqlalchemy
from sqlalchemy.orm import (
    InstanceState,
    RelationshipDirection,
    Session,
    SessionTransaction,
)

from stellarisdashboard.parsing import pending_flush

_QUEUE_KEY = "plain_rows_queue"


class _ManyToOne:
    """A many-to-one relationship of a plain-row model: its foreign key column values."""

    def __init__(self, prop: sqlalchemy.orm.RelationshipProperty):
        if (
            prop.direction is not RelationshipDirection.MANYTOONE
            or prop.secondary is not None
        ):
            raise TypeError(f"{prop}: only many-to-one relationships are supported")
        self.key = prop.key
        self.target_mapper = prop.mapper
        self.local_keys = [local.key for _, local in prop.synchronize_pairs]
        primary_key = list(prop.mapper.primary_key)
        remote_columns = [remote for remote, _ in prop.synchronize_pairs]
        if all(remote in primary_key for remote in remote_columns):
            # read from the identity, which never loads anything
            self.identity_positions = [
                primary_key.index(remote) for remote in remote_columns
            ]
            self.remote_attributes = None
        else:
            self.identity_positions = None
            self.remote_attributes = [
                prop.mapper.get_property_by_column(remote).key
                for remote in remote_columns
            ]
        # the collection on the referenced object that the ORM would append the row to
        self.reverse_key = prop.back_populates or (
            prop.backref
            if isinstance(prop.backref, str)
            else (prop.backref or (None,))[0]
        )

    def check_target(self, target, session: Session) -> InstanceState:
        state = sqlalchemy.inspect(target)
        if not state.mapper.isa(self.target_mapper):
            raise TypeError(
                f"{self.key}: expected {self.target_mapper.class_.__name__}, got {target!r}"
            )
        if state.session is not session or state.deleted or state.was_deleted:
            raise RuntimeError(
                f"{self.key}: {target!r} is not in the session (add it first) or was deleted"
            )
        return state

    def foreign_key_values(self, target, session: Session) -> List[Tuple[str, Any]]:
        state = self.check_target(target, session)
        if self.identity_positions is not None:
            identity = state.identity
            if identity is None:
                raise RuntimeError(
                    f"{self.key}: {target!r} has no primary key yet; flush first"
                )
            values = [identity[i] for i in self.identity_positions]
        else:
            values = [getattr(target, key) for key in self.remote_attributes]
        return list(zip(self.local_keys, values))


def _is_sqlalchemy_listener(fn) -> bool:
    """Whether a listener is SQLAlchemy's own (cascades, backrefs, mapper set-up).

    SQLAlchemy registers its own listeners as raw functions of its modules. A listener of
    the application is either of another module, or (registered without ``raw=True``)
    wrapped in a ``wrap`` function of sqlalchemy.orm.events.
    """
    return getattr(fn, "__module__", "").startswith("sqlalchemy.") and not getattr(
        fn, "__qualname__", ""
    ).endswith("_listen.<locals>.wrap")


def _foreign_listeners(dispatch_collection) -> List[Any]:
    return [fn for fn in dispatch_collection if not _is_sqlalchemy_listener(fn)]


class _Model:
    """What is needed to insert rows of one model, checked to be insertable this way."""

    def __init__(self, model: type):
        mapper = sqlalchemy.inspect(model)
        if (
            mapper.inherits is not None
            or len(list(mapper.self_and_descendants)) != 1
            or len(mapper.tables) != 1
            or mapper.version_id_col is not None
            or dataclasses.is_dataclass(model)
        ):
            raise TypeError(
                f"{model.__name__}: plain rows need a single-table model without inheritance, "
                "versioning or dataclass defaults"
            )
        if mapper.validators:
            raise TypeError(
                f"{model.__name__}: validators would be skipped by plain rows"
            )
        self.model = model
        self.mapper = mapper
        self.table = mapper.local_table
        self.columns: Dict[str, sqlalchemy.Column] = {}
        for prop in mapper.column_attrs:
            if len(prop.columns) != 1 or prop.columns[0].table is not self.table:
                raise TypeError(f"{model.__name__}.{prop.key}: not a plain column")
            self.columns[prop.key] = prop.columns[0]
        self.relationships: Dict[str, _ManyToOne] = {
            prop.key: _ManyToOne(prop)
            for prop in mapper.relationships
            if prop.direction is RelationshipDirection.MANYTOONE
        }
        self.keys = frozenset(self.columns) | frozenset(self.relationships)
        # as in Mapper._insert_cols_evaluating_none and Mapper._insert_cols_as_none
        self.evaluates_none = frozenset(
            column.key
            for column in self.columns.values()
            if column.type.should_evaluate_none
        )
        self.null_if_missing = frozenset(
            column.key
            for column in self.table.columns
            if not column.primary_key
            and not column.server_default
            and not column.default
            and not column.type.should_evaluate_none
        )
        self.referenced_tables = frozenset(
            fk.column.table for fk in self.table.foreign_keys
        )

    def check_listeners(self) -> None:
        """Refuse a model with ORM event listeners that inserting plain rows would skip."""
        mapper = self.mapper
        collections = [
            mapper.dispatch.before_insert,
            mapper.dispatch.after_insert,
            mapper.class_manager.dispatch.init,
            mapper.class_manager.dispatch.init_failure,
        ]
        for key in self.keys:
            dispatch = mapper.class_manager[key].dispatch
            collections += [dispatch.set, dispatch.append, dispatch.bulk_replace]
        for relationship in self.relationships.values():
            if relationship.reverse_key is not None:
                dispatch = relationship.target_mapper.class_manager[
                    relationship.reverse_key
                ].dispatch
                collections += [dispatch.append, dispatch.bulk_replace]
        foreign = [
            fn for collection in collections for fn in _foreign_listeners(collection)
        ]
        if foreign:
            raise TypeError(
                f"{self.model.__name__}: plain rows would skip the ORM listeners {foreign}"
            )

    def check_keys(self, values: Dict[str, Any]) -> None:
        unknown = values.keys() - self.keys
        if unknown:
            raise TypeError(
                f"{sorted(unknown)} are not columns or many-to-one relationships of {self.model.__name__}"
            )

    def insert_params(self, values: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """The INSERT parameters of a row, keyed by column key, with the ORM's None handling."""
        params = {}
        for key, value in values.items():
            column = self.columns.get(key)
            if column is not None:
                if value is not None or column.key in self.evaluates_none:
                    params[column.key] = value
            elif value is not None:
                for column_key, fk_value in self.relationships[key].foreign_key_values(
                    value, session
                ):
                    if fk_value is not None:
                        params[column_key] = fk_value
        for column_key in self.null_if_missing:
            params.setdefault(column_key, None)
        return params


_models: Dict[type, _Model] = {}


def _model(model: type) -> _Model:
    info = _models.get(model)
    if info is None:
        info = _models[model] = _Model(model)
    return info


_guarded_collections: Set[Tuple[type, str]] = set()


def _guard_collection(relationship: _ManyToOne) -> None:
    """Raise when the referenced object's collection is initialised while its rows are queued.

    A collection loaded from the database is fine: the rows are written before the SELECT.
    It fires without a SELECT for a pending object, which would then miss the rows that the
    ORM would have appended.
    """
    target_class = relationship.target_mapper.class_
    key = relationship.reverse_key
    if (target_class, key) in _guarded_collections:
        return

    def collection_initialized(target, collection, adapter):
        state = sqlalchemy.inspect(target)
        session = state.session
        queue = session.info.get(_QUEUE_KEY) if session is not None else None
        if queue is not None and (state, key) in queue.collection_targets:
            raise RuntimeError(
                f"{target_class.__name__}.{key} of {target!r} was used while rows for it are "
                "queued as plain rows; it would not contain them"
            )

    sqlalchemy.event.listen(
        getattr(target_class, key), "init_collection", collection_initialized
    )
    _guarded_collections.add((target_class, key))


# the values of a row, and the objects it refers to with the collection the ORM would
# append the row to
_Entry = Tuple[Dict[str, Any], List[Tuple[InstanceState, Optional[str]]]]


class _Queue:
    def __init__(self, session: Session):
        self.session = session
        # per model, in queue order. All rows belong to the innermost transaction:
        # begin_nested() writes them first (after_transaction_create).
        self.rows: Dict[_Model, List[_Entry]] = {}
        self.tables: Set[sqlalchemy.Table] = set()
        self.referenced_tables: Set[sqlalchemy.Table] = set()
        self.targets: Set[InstanceState] = set()
        self.collection_targets: Set[Tuple[InstanceState, str]] = set()
        self.checked_models: Set[_Model] = set()
        self.writing = False

    def add(self, model: type, values: Dict[str, Any]) -> None:
        info = _model(model)
        if info not in self.checked_models:
            info.check_listeners()
            for relationship in info.relationships.values():
                if relationship.reverse_key is not None:
                    _guard_collection(relationship)
            self.checked_models.add(info)
        info.check_keys(values)
        targets = []
        for key, relationship in info.relationships.items():
            if key not in values:
                continue
            if not set(relationship.local_keys).isdisjoint(values):
                raise TypeError(
                    f"{model.__name__}: both {key} and its foreign key are given"
                )
            target = values[key]
            if target is None:
                continue
            state = relationship.check_target(target, self.session)
            if (
                relationship.reverse_key is not None
                and relationship.reverse_key in state.dict
            ):
                raise RuntimeError(
                    f"{target!r}.{relationship.reverse_key} is loaded and would not contain "
                    f"the plain {model.__name__} row"
                )
            targets.append((state, relationship.reverse_key))
        entries = self.rows.get(info)
        if entries is None:
            entries = self.rows[info] = []
            self.tables.add(info.table)
            self.referenced_tables.update(info.referenced_tables)
        entries.append((values, targets))
        for state, reverse_key in targets:
            self.targets.add(state)
            if reverse_key is not None:
                self.collection_targets.add((state, reverse_key))

    def write(self, transaction: Optional[SessionTransaction] = None) -> None:
        """Insert the queued rows in the session's transaction, or in the given one.

        The session is flushed first, so that new referenced objects have their primary
        keys; with a transaction given, the caller has flushed it.
        """
        if not self.rows or self.writing:
            return
        self.writing = True
        try:
            if transaction is None:
                self.session.flush()
            rows = self.rows
            self._clear()
            for info, entries in rows.items():
                if transaction is None:
                    connection = self.session.connection(
                        bind_arguments={"mapper": info.mapper}
                    )
                else:
                    connection = transaction.connection(info.mapper)
                params = [
                    info.insert_params(values, self.session) for values, _ in entries
                ]
                # executemany needs the same keys in each row; groups keep the queue order
                for _, group in itertools.groupby(params, key=dict.keys):
                    connection.execute(sqlalchemy.insert(info.table), list(group))
        finally:
            self.writing = False

    def _clear(self) -> None:
        self.rows = {}
        self.tables = set()
        self.referenced_tables = set()
        self.targets = set()
        self.collection_targets = set()

    # session events

    def before_statement(
        self, orm_execute_state: sqlalchemy.orm.ORMExecuteState
    ) -> None:
        if not self.rows or self.writing or self.session._flushing:
            # during a flush, the only statements are loads of the flushed objects'
            # relationships; before_flush refuses deleting a referenced object
            return
        tables = pending_flush.statement_tables(orm_execute_state.statement)
        if (
            not tables
            or not self.tables.isdisjoint(tables)
            or (
                (orm_execute_state.is_update or orm_execute_state.is_delete)
                and not self.referenced_tables.isdisjoint(tables)
            )
        ):
            self.write()

    def before_flush(self, session: Session, flush_context, instances) -> None:
        if self.targets and session.deleted:
            for obj in session.deleted:
                if sqlalchemy.inspect(obj) in self.targets:
                    raise RuntimeError(
                        f"{obj!r} is deleted while plain rows refer to it"
                    )

    def object_added(self, session: Session, instance) -> None:
        table = sqlalchemy.inspect(instance).mapper.local_table
        if table in self.tables:
            raise RuntimeError(
                f"{instance!r} added to the session while rows of {table.name} are queued as plain rows"
            )

    def before_commit(self, session: Session) -> None:
        self.write()

    def transaction_created(
        self, session: Session, transaction: SessionTransaction
    ) -> None:
        if transaction.nested and self.rows:
            # begin_nested() has just flushed the session into the enclosing transaction,
            # before the savepoint is emitted: the queued rows go there too, as pending
            # objects would. Then the queue only ever holds rows of the innermost
            # transaction, and any rollback discards all of them.
            self.write(transaction.parent)

    def after_soft_rollback(
        self, session: Session, previous_transaction: SessionTransaction
    ) -> None:
        # a savepoint, the whole transaction, or a failed flush (which rolls the database
        # back to the innermost savepoint): either way the rows of the innermost one
        self._clear()


@contextlib.contextmanager
def queue_inserts(session: Session):
    """Within the block, ``add(session, ...)`` queues rows, written as described above."""
    if _QUEUE_KEY in session.info:
        raise RuntimeError("queue_inserts is already active for this session")
    queue = _Queue(session)
    session.info[_QUEUE_KEY] = queue
    listeners = [
        ("do_orm_execute", queue.before_statement),
        ("before_flush", queue.before_flush),
        ("transient_to_pending", queue.object_added),
        ("before_commit", queue.before_commit),
        ("after_transaction_create", queue.transaction_created),
        ("after_soft_rollback", queue.after_soft_rollback),
    ]
    for name, fn in listeners:
        sqlalchemy.event.listen(session, name, fn)
    failed = True
    try:
        yield session
        failed = False
    finally:
        for name, fn in listeners:
            sqlalchemy.event.remove(session, name, fn)
        del session.info[_QUEUE_KEY]
        if queue.rows and not failed:
            raise RuntimeError(
                "rows queued with plain_rows.add() were neither written, committed nor rolled back"
            )


def _queue(session: Session) -> _Queue:
    queue = session.info.get(_QUEUE_KEY)
    if queue is None:
        raise RuntimeError(
            "plain rows can only be queued within plain_rows.queue_inserts()"
        )
    return queue


def add(session: Session, model: type, **values) -> None:
    """Queue a row of an append-only table, given as for the model's constructor."""
    _queue(session).add(model, values)


def write(session: Session) -> None:
    """Insert the queued rows now (flushing the session first)."""
    _queue(session).write()
