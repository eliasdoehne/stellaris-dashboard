"""State that the processors of one gamestate import share, and that ends with the import.

TimelineExtractor.process_gamestate creates an ImportContext per import and closes it in a
``finally`` block, and every processor runs inside ``processor_scope()``, which closes the
StoredRows the processor created however it ends. Nothing here outlives an import, so a
failed import cannot leave lookups behind that the next one would trust.
"""

import contextlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

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
        self._country_data: Optional[Dict[int, datamodel.CountryData]] = None

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

    def most_recent_country_data(self, country: datamodel.Country) -> Optional[datamodel.CountryData]:
        """country.get_most_recent_data() without loading all of the country's rows.

        That returns the last element of country.country_data, which is ordered by date. All
        callers run after CountryDataProcessor, whose rows its savepoint has flushed, and
        nothing loads country.country_data before: the lazy load then returns the stored rows by
        date, so the result is the row with the latest date (dates are unique per country, one
        gamestate per date). If no later gamestate is stored, that is this gamestate's row,
        found here with one query for all countries. Otherwise (an older save imported after
        newer ones, or a replaced gamestate that is not the newest), and for countries without
        a row in this gamestate, this falls back to get_most_recent_data() itself.
        """
        if self._country_data is None:
            newer_gamestate = (
                self._session.query(datamodel.GameState.gamestate_id)
                .filter(
                    datamodel.GameState.game == self._game,
                    datamodel.GameState.date > self._gamestate.date,
                )
                .first()
            )
            self._country_data = {}
            if newer_gamestate is None:
                for country_data in self._session.query(datamodel.CountryData).filter(
                    datamodel.CountryData.game_state == self._gamestate
                ):
                    self._country_data[country_data.country_id] = country_data
        country_data = self._country_data.get(country.country_id)
        if country_data is None:
            country_data = country.get_most_recent_data()
        return country_data

    def close(self) -> None:
        self._close_stored_rows()
        self._country_data = None
