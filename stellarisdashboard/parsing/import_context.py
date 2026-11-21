"""State that the processors of one gamestate import share, and that ends with the import.

TimelineExtractor.process_gamestate creates an ImportContext per import and closes it in a
``finally`` block, and every processor runs inside ``processor_scope()``, which closes the
StoredRows the processor created however it ends. Nothing here outlives an import, so a
failed import cannot leave lookups behind that the next one would trust.
"""

import contextlib
from typing import Any, List, Mapping, Optional, Sequence

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute

from stellarisdashboard import datamodel
from stellarisdashboard.parsing.stored_rows import StoredRows


class ImportContext:
    def __init__(self, session: Session, game: datamodel.Game, gamestate: datamodel.GameState):
        self._session = session
        self._game = game
        self._gamestate = gamestate
        self._stored_rows: List[StoredRows] = []

    def stored_rows(
        self,
        key: Sequence[InstrumentedAttribute],
        where: Optional[Mapping[InstrumentedAttribute, Any]] = None,
        order_by: Optional[InstrumentedAttribute] = None,
    ) -> StoredRows:
        """A StoredRows that is closed when the current processor_scope() ends."""
        rows = StoredRows(self._session, key, where=where, order_by=order_by)
        self._stored_rows.append(rows)
        return rows

    @contextlib.contextmanager
    def processor_scope(self):
        """The run of one processor: the StoredRows it created are closed afterwards."""
        try:
            yield
        finally:
            self._close_stored_rows()

    def _close_stored_rows(self) -> None:
        rows, self._stored_rows = self._stored_rows, []
        for r in rows:
            r.close()

    def close(self) -> None:
        self._close_stored_rows()
