"""Look up a model's rows by key columns with one query for all lookups instead of one each.

A processor that looks up a row per entity of the gamestate (the newest ownership of each
system, the open councilor event of each council position, ...) sent one query per lookup,
and with autoflush a flush before most of them, because the processor had added rows to the
same table since. ``StoredRows`` replaces those queries: one query reads the primary
key, key columns and ordering column of all rows of the model that match fixed criteria, and a
lookup returns what the query would have returned at that moment, without a statement unless
it has to load the row it returns.

What a lookup emulates::

    session.query(model).filter_by(**where, **dict(zip(key, values)))     # after a flush
        .order_by(order_by.desc(), primary_key).first()                    # latest()
        .one_or_none()                                                     # one_or_none()

Invariants that make the answers equal to such a query:

* Rows of the model that are stored when the StoredRows is created are known from its scan;
  the scan itself is a query, so autoflush writes the session's pending changes first.
* Objects of the model added to the session afterwards (``Session.add``, or a cascade during
  a flush) are registered through the session's ``transient_to_pending`` event. Call sites do
  not register anything by hand. An object that is only attached to a session object's
  collection and not added reaches the session at the next flush, as with a query, but is
  not seen before that flush.
* Keys, criteria and ordering values are read from the object's current attribute values for
  every object that the session holds and that changed, was added or was returned: class-level
  attribute ``set`` events on the key, criteria and ordering attributes mark such an object
  for re-indexing, and so does every flush (which assigns primary keys and fills in foreign
  keys). Rows that were never loaded cannot have changed in the session, so their scanned
  values are current. Changes made past the ORM session (bulk UPDATE statements) are not seen.
* What the next flush would delete or discard is not found: a deleted or expunged object, a
  pending object that ``datamodel.drop_special_country_events`` discards (the same predicate,
  ``datamodel.is_dropped_special_country_event``), and a stored row taken out of a parent's
  delete-orphan collection. A stored row that is gone when it is loaded (deleted since the
  scan) is skipped, and the next candidate is returned.
* Keys are compared in one form: a column value as is, and a many-to-one relationship by the
  primary key of the related object, or by the object itself while it has none (a pending
  object, which no stored row can reference). Lookups pass the related object or None for a
  relationship attribute and the column value for a column attribute.
* ``latest()`` ranks like ``ORDER BY order_by DESC, primary key`` in SQLite: the largest value
  first, NULLs last, and among equal values the oldest row (smallest primary key; a pending
  object ranks after all stored rows, in the order the objects were added, which is the order
  in which a flush assigns their primary keys).

Private SQLAlchemy API: ``Mapper._is_orphan()`` and ``Mapper._delete_orphans``, which
``Session.flush()`` uses to delete orphans; test/stored_rows_test.py covers them.

A StoredRows must be closed (``close()``, or use it as a context manager) when the processor is
done: until then its listeners run for every change of the model's watched attributes in any
session. An unclosed StoredRows removes them when it is garbage collected.
"""

import itertools
import weakref
from typing import (
    Any,
    Dict,
    Hashable,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

import sqlalchemy
from sqlalchemy.orm import RelationshipProperty, Session
from sqlalchemy.orm.attributes import InstrumentedAttribute, instance_state
from sqlalchemy.orm.interfaces import MANYTOONE

from stellarisdashboard import datamodel


def key_value(value: Any) -> Hashable:
    """The form in which StoredRows compares a key value: a mapped object by its primary key, or
    the object itself if it has none yet; any other value as is."""
    if isinstance(value, datamodel.Base):
        identity = instance_state(value).identity
        if identity is not None:
            return identity[0]
    return value


def related_key(obj: Any, relationship: InstrumentedAttribute) -> Hashable:
    """key_value() of the object that a many-to-one relationship of obj refers to, without
    loading it: the related object if it is loaded or set, otherwise the foreign key column.

    For a pending object whose foreign key column only a flush fills in, this gives the
    related object's primary key where obj.<foreign key> would still be None."""
    return _Field(relationship).current(obj)


class _Field:
    """A key, criteria or ordering attribute: a column, or a many-to-one relationship that is
    compared by its single foreign key column."""

    def __init__(self, attribute: InstrumentedAttribute):
        prop = attribute.property
        self.attribute = attribute
        self.name = attribute.key
        if isinstance(prop, RelationshipProperty):
            if prop.direction is not MANYTOONE or len(prop.local_columns) != 1:
                raise ValueError(
                    f"{attribute} is not a many-to-one relationship with one column"
                )
            (column,) = prop.local_columns
            self.fk_name = prop.parent.get_property_by_column(column).key
            self.column = column
            self.watched = [attribute, getattr(prop.parent.class_, self.fk_name)]
        else:
            (column,) = prop.columns
            if column.default is not None or column.server_default is not None:
                # a pending object reads None until a flush fills in the default
                raise ValueError(f"{attribute} has a column default")
            self.fk_name = None
            self.column = column
            self.watched = [attribute]

    def current(self, obj) -> Hashable:
        if self.fk_name is None:
            return getattr(obj, self.name)
        dict_ = obj.__dict__
        if self.name in dict_:
            return key_value(dict_[self.name])
        return getattr(obj, self.fk_name)


class StoredRows:
    """Lookups of one model's rows by key without a query per lookup, see the module docstring.

    key: the attributes whose values a lookup gives, in this order (columns or many-to-one
    relationships of one model). where: fixed criteria, {column attribute: value}, or a set of
    values for an IN criterion; a criterion on a key attribute restricts which values a lookup
    may ask for. order_by: the column that latest() orders by.
    """

    def __init__(
        self,
        session: Session,
        key: Sequence[InstrumentedAttribute],
        where: Optional[Mapping[InstrumentedAttribute, Any]] = None,
        order_by: Optional[InstrumentedAttribute] = None,
    ):
        where = dict(where or {})
        attributes = [*key, *where, *([order_by] if order_by is not None else [])]
        models = {a.class_ for a in attributes}
        if len(models) != 1:
            raise ValueError(f"the attributes belong to several models: {models}")
        (self._model,) = models
        mapper = sqlalchemy.inspect(self._model)
        (primary_key,) = mapper.primary_key
        self._session = session
        self._session_key = session.hash_key
        self._has_orphan_rule = bool(mapper._delete_orphans)
        self._key = [_Field(a) for a in key]
        self._where = [(_Field(a), value) for a, value in where.items()]
        self._order = _Field(order_by) if order_by is not None else None
        self._allowed = {
            i: value
            for i, field in enumerate(self._key)
            for where_field, value in self._where
            if where_field.attribute is field.attribute
            and isinstance(value, (set, frozenset))
        }

        # key -> [(order value, primary key)] of the rows read by the scan
        self._scanned: Dict[tuple, List[Tuple[Any, int]]] = {}
        criteria = []
        for field, value in self._where:
            if isinstance(value, (set, frozenset)):
                criteria.append(field.column.in_(sorted(value, key=repr)))
            elif value is None:
                criteria.append(field.column.is_(None))
            else:
                criteria.append(field.column == key_value(value))
        order_column = self._order.column if self._order is not None else primary_key
        query = session.query(primary_key, order_column, *(f.column for f in self._key))
        for row_id, order_value, *values in query.filter(*criteria):
            self._scanned.setdefault(tuple(values), []).append((order_value, row_id))

        # objects whose current values decide: added, loaded by a lookup, or changed
        self._live: Dict[int, Any] = {}
        self._live_key: Dict[int, tuple] = {}
        self._live_by_key: Dict[tuple, Dict[int, Any]] = {}
        self._live_row_ids = set()  # primary keys of stored rows that are live
        self._added_order: Dict[int, int] = {}
        self._counter = itertools.count()
        self._touched: Dict[int, Any] = {}
        self._rekey_all = False

        self._listeners = self._listen()
        self._finalizer = weakref.finalize(self, _remove_listeners, self._listeners)

    # -- listeners ----------------------------------------------------------------------------

    def _listen(self) -> list:
        this = weakref.ref(self)

        def on_set(target, value, oldvalue, initiator):
            rows = this()
            if rows is not None:
                rows._changed(target)

        def on_added(session, instance):
            rows = this()
            if rows is not None and isinstance(instance, rows._model):
                rows._register(instance, added=True)

        def on_flushed(session, flush_context):
            rows = this()
            if rows is not None:
                rows._rekey_all = True

        listeners = [(self._session, "transient_to_pending", on_added)]
        listeners.append((self._session, "after_flush_postexec", on_flushed))
        watched = {}
        for field in [*self._key, *(f for f, _ in self._where), self._order]:
            if field is not None:
                for attribute in field.watched:
                    watched[attribute.key] = attribute
        listeners.extend((attribute, "set", on_set) for attribute in watched.values())
        try:
            for target, name, fn in listeners:
                sqlalchemy.event.listen(target, name, fn)
        except BaseException:
            _remove_listeners(listeners)
            raise
        return listeners

    def close(self) -> None:
        """Remove the listeners; lookups are no longer valid afterwards."""
        self._finalizer()

    def __enter__(self) -> "StoredRows":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _changed(self, obj) -> None:
        oid = id(obj)
        if oid in self._live:
            self._touched[oid] = obj
            return
        state = instance_state(obj)
        if state.session_id == self._session_key and state.key is not None:
            # a stored row that something else loaded is changed: its scanned values are stale
            self._register(obj)

    def _register(self, obj, added: bool = False) -> None:
        oid = id(obj)
        if oid not in self._live:
            self._live[oid] = obj
            if added:
                self._added_order[oid] = next(self._counter)
        self._touched[oid] = obj

    def _refresh(self) -> None:
        if self._closed():
            raise RuntimeError("StoredRows used after close()")
        if self._rekey_all:
            self._rekey_all = False
            self._touched.update(self._live)
        touched, self._touched = self._touched, {}
        for oid, obj in touched.items():
            identity = instance_state(obj).identity
            if identity is not None:
                self._live_row_ids.add(identity[0])
            new_key = tuple(field.current(obj) for field in self._key)
            old_key = self._live_key.get(oid)
            if old_key == new_key and oid in self._live_by_key.get(new_key, ()):
                continue
            if old_key is not None:
                self._live_by_key[old_key].pop(oid, None)
            self._live_key[oid] = new_key
            self._live_by_key.setdefault(new_key, {})[oid] = obj

    def _closed(self) -> bool:
        return not self._finalizer.alive

    # -- lookups ------------------------------------------------------------------------------

    def _candidates(self, keys: Iterable[Sequence[Any]], at_most=None) -> list:
        """[(order value, position, row id or None, object or None)] of the rows with these keys."""
        self._refresh()
        deleted = self._session.deleted
        result = []
        for key in keys:
            if len(key) != len(self._key):
                raise TypeError(f"expected {len(self._key)} key values, got {len(key)}")
            for i, allowed in self._allowed.items():
                if key[i] not in allowed:
                    raise ValueError(
                        f"{key[i]!r} is excluded by the criteria of this StoredRows"
                    )
            key = tuple(key_value(v) for v in key)
            for order_value, row_id in self._scanned.get(key, ()):
                if row_id not in self._live_row_ids:
                    result.append((order_value, (0, row_id), row_id, None))
            for oid, obj in self._live_by_key.get(key, {}).items():
                if not self._is_present(obj, deleted) or not self._matches_where(obj):
                    continue
                order_value = (
                    self._order.current(obj) if self._order is not None else None
                )
                identity = instance_state(obj).identity
                if identity is not None:
                    position = (0, identity[0])
                else:
                    position = (1, self._added_order.get(oid, -1))
                result.append((order_value, position, None, obj))
        if at_most is not None:
            # as SQL's "order_by <= at_most", which no NULL satisfies
            result = [c for c in result if c[0] is not None and c[0] <= at_most]
        return result

    def _is_present(self, obj, deleted) -> bool:
        state = instance_state(obj)
        if state.session_id != self._session_key or state.deleted or obj in deleted:
            return False  # expunged (e.g. discarded by a flush hook) or deleted
        if state.key is not None:
            # a stored row taken out of a parent's delete-orphan collection (e.g. event.country
            # set to None) is deleted by the next flush; private API, as Session.flush() uses it
            return not (
                self._has_orphan_rule and state.manager.mapper._is_orphan(state)
            )
        if datamodel.is_dropped_special_country_event(self._session, obj):
            return False
        return True

    def _matches_where(self, obj) -> bool:
        for field, value in self._where:
            current = field.current(obj)
            if isinstance(value, (set, frozenset)):
                if current not in value:
                    return False
            elif current != key_value(value):
                return False
        return True

    def _load(self, row_id):
        """The stored row's object, or None if the row was deleted since the scan."""
        obj = self._session.get(self._model, row_id)
        self._live_row_ids.add(row_id)
        if obj is None:
            return None
        self._register(obj)
        self._refresh()
        if not self._is_present(obj, self._session.deleted):
            return None
        return obj

    def _rows(
        self, keys: List[Sequence[Any]], at_most=None, ranked=False, limit=None
    ) -> list:
        """The objects of the rows with these keys, in ORDER BY order DESC, primary key order
        if ranked; at most limit of them. Loads stored rows as needed."""
        # Without autoflush: the answers already account for the session's pending changes, and
        # a stored row that is not in the session has no unflushed changes, so loading it by
        # primary key needs no flush either.
        with self._session.no_autoflush:
            while True:
                candidates = self._candidates(keys, at_most)
                if ranked:
                    # NULLs last, ties by position: sort by position, then a stable sort by value
                    # (reverse=True keeps the order of equal values)
                    candidates.sort(key=lambda c: c[1])
                    candidates.sort(
                        key=lambda c: (True, c[0]) if c[0] is not None else (False, 0),
                        reverse=True,
                    )
                found = []
                for order_value, position, row_id, obj in candidates:
                    if row_id is not None:
                        obj = self._load(row_id)
                        if obj is None:
                            continue  # deleted since the scan
                        if not self._is_scanned_as(obj, keys, order_value):
                            break  # changed past the session: look again with its current values
                    found.append(obj)
                    if limit is not None and len(found) == limit:
                        return found
                else:
                    return found

    def _is_scanned_as(self, obj, keys, order_value) -> bool:
        """Whether a just loaded row still has the key, criteria and ordering value that the
        scan read. Only a change past the ORM session (a bulk UPDATE) makes them differ.
        """
        return (
            self._live_key[id(obj)]
            in {tuple(key_value(v) for v in key) for key in keys}
            and self._matches_where(obj)
            and (self._order is None or self._order.current(obj) == order_value)
        )

    def latest(self, *key, at_most=None):
        """The row with this key that has the largest ordering value (NULLs last, ties to the
        oldest row); with at_most, only rows whose ordering value is at most that. None if
        there is none."""
        return self.latest_of([key], at_most=at_most)

    def latest_of(self, keys: Iterable[Sequence[Any]], at_most=None):
        """As latest() over the rows of any of these keys (a filter with IN)."""
        if self._order is None:
            raise TypeError("latest() needs order_by")
        found = self._rows(list(keys), at_most=at_most, ranked=True, limit=1)
        return found[0] if found else None

    def one_or_none(self, *key):
        """As Query.one_or_none(): the row with this key, None if there is none, and
        sqlalchemy.exc.MultipleResultsFound if there are several."""
        found = self._rows([key])
        if len(found) > 1:
            raise sqlalchemy.exc.MultipleResultsFound(
                f"Multiple {self._model.__name__} rows were found when one or none was required"
            )
        return found[0] if found else None

    def exists(self, *key) -> bool:
        """Whether a row with this key exists."""
        return bool(self._rows([key], limit=1))


def _remove_listeners(listeners) -> None:
    for target, name, fn in listeners:
        if sqlalchemy.event.contains(target, name, fn):
            sqlalchemy.event.remove(target, name, fn)
