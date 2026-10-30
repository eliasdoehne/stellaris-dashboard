"""
Import the existing save files of one game in the background, started from the import page.

The import runs through the same code path as the `parse-saves` CLI command (cli.f_parse_saves).
Only one import runs at a time. While it runs, the save watcher leaves new saves of the same game
to the import, which picks them up after the saves it started with, so the game's saves are
written to the database by one importer in order.

Reprocessing the kept copies of failed saves (save_copies) is a job of the same kind: it uses the
same single job slot, import path and process-wide lock, so it never runs alongside another import.
"""

import dataclasses
import logging
import pathlib
import re
import threading
import time
import zipfile
from typing import Dict, List, Optional, Set, Tuple

from stellarisdashboard import config, datamodel
from stellarisdashboard.i18n import gettext as _
from stellarisdashboard.parsing import save_copies, save_parser

logger = logging.getLogger(__name__)

_META_DATE_RE = re.compile(r'^date="(\d+\.\d+\.\d+)"', re.MULTILINE)
_META_NAME_RE = re.compile(r'^name="(.*)"', re.MULTILINE)
_FILENAME_DATE_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2})")

_MAX_LISTED_ERRORS = 50

# game_id of a job that reprocesses the kept copies of several games; shown as "all games"
_ALL_GAMES_ID = "all games"


class ImportRefused(Exception):
    """The import cannot be started; the message is shown to the user."""


@dataclasses.dataclass(frozen=True)
class SaveInfo:
    path: pathlib.Path
    date: Optional[int]  # days since 2200.01.01, None if unknown
    empire_name: str = ""

    @property
    def date_str(self) -> str:
        return datamodel.days_to_date(self.date) if self.date is not None else "?"


# path -> (mtime_ns, size, SaveInfo); reading the meta of every save on each page view adds up
_save_info_cache: Dict[pathlib.Path, Tuple[int, int, SaveInfo]] = {}


def read_save_info(path: pathlib.Path) -> SaveInfo:
    """Read the in-game date and empire name from the save's small meta file, without parsing the gamestate."""
    stat = path.stat()
    cached = _save_info_cache.get(path)
    if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    date_str, empire_name = None, ""
    try:
        with zipfile.ZipFile(path) as zf:
            meta = zf.read("meta").decode("utf-8", errors="replace")
        if m := _META_DATE_RE.search(meta):
            date_str = m.group(1)
        if m := _META_NAME_RE.search(meta):
            empire_name = m.group(1)
    except (OSError, KeyError, zipfile.BadZipFile):
        logger.warning(f"Could not read the meta data of save file {path}")
    if date_str is None and (m := _FILENAME_DATE_RE.search(path.stem)):
        date_str = m.group(1)
    date = None
    if date_str is not None:
        try:
            date = datamodel.date_to_days(date_str)
        except ValueError:
            pass
    info = SaveInfo(path=path, date=date, empire_name=empire_name)
    _save_info_cache[path] = (stat.st_mtime_ns, stat.st_size, info)
    return info


def _save_files_by_game(save_path: pathlib.Path) -> Dict[str, List[pathlib.Path]]:
    # same file discovery and game ID as the save watcher (SavePathMonitor)
    games: Dict[str, List[pathlib.Path]] = {}
    for f in save_path.glob("**/*.sav"):
        games.setdefault(f.parent.stem, []).append(f)
    return games


def _imported_dates(game_id: str) -> Set[int]:
    if not (config.CONFIG.db_path / f"{game_id}.db").exists():
        return set()  # don't create a database just by looking
    with datamodel.get_db_session(game_id) as session:
        return {date for (date,) in session.query(datamodel.GameState.date)}


@dataclasses.dataclass
class SaveGameFolder:
    game_id: str
    num_saves: int
    num_imported: int  # gamestates in the game's database


def list_save_games(save_path: Optional[pathlib.Path] = None) -> List[SaveGameFolder]:
    """The games in the save folder, most recently saved first."""
    save_path = save_path or config.CONFIG.save_file_path
    if save_path is None or not save_path.is_dir():
        return []
    db_games = datamodel.get_available_games_dict()
    games = _save_files_by_game(save_path)
    last_saved = {
        game_id: max(f.stat().st_mtime for f in files) for game_id, files in games.items()
    }
    return [
        SaveGameFolder(
            game_id=game_id,
            num_saves=len(files),
            num_imported=db_games.get(game_id, {}).get("num_saves", 0),
        )
        for game_id, files in sorted(games.items(), key=lambda kv: -last_saved[kv[0]])
    ]


@dataclasses.dataclass
class ImportPlan:
    game_id: str
    empire_name: str
    saves: List[SaveInfo]  # all considered save files of the game, in chronological order
    num_imported: int  # of those, saves whose date is already in the database
    num_filtered_out: int  # not imported due to the save_name_filter / skip_saves settings
    to_import: List[SaveInfo]  # in chronological order
    newest_imported_date: Optional[int]

    @property
    def num_saves(self) -> int:
        return len(self.saves)

    @property
    def num_older_than_imported(self) -> int:
        """Saves to import that are older than the newest imported save, so they are imported out of order."""
        if self.newest_imported_date is None:
            return 0
        return sum(
            1
            for s in self.to_import
            if s.date is not None and s.date < self.newest_imported_date
        )

    @property
    def newest_imported_date_str(self) -> str:
        return datamodel.days_to_date(self.newest_imported_date) if self.newest_imported_date is not None else ""


def get_import_plan(
    game_id: str,
    save_path: Optional[pathlib.Path] = None,
    exclude: Optional[Set[pathlib.Path]] = None,
    save_filter: Optional[save_parser.SavePathMonitor] = None,
) -> ImportPlan:
    """
    Work out which saves of the game still need to be imported. Saves are selected with the same
    save_name_filter and skip_saves settings as the CLI, then saves whose date is already in the
    database are dropped (one save per date).

    :param exclude: Save files to ignore entirely, e.g. those already seen by a running import.
    :param save_filter: Monitor that applies the settings; reuse one so skip_saves counts across calls.
    """
    save_path = save_path or config.CONFIG.save_file_path
    files = _save_files_by_game(save_path).get(game_id) if save_path else None
    if not files:
        raise ImportRefused(
            _("No save files found for game %(game_id)s in %(save_path)s.", game_id=game_id, save_path=save_path)
        )
    saves = sorted(
        (read_save_info(f) for f in files if not exclude or f not in exclude),
        key=lambda s: (s.date if s.date is not None else float("inf"), s.path),
    )
    imported = _imported_dates(game_id)
    save_filter = save_filter or save_parser.BatchSavePathMonitor(save_path)
    selected_paths = set(save_filter.apply_save_filters([s.path for s in saves]))
    to_import = []
    planned_dates = set()
    for s in saves:
        if s.path not in selected_paths or s.date in imported:
            continue
        if s.date is not None:
            if s.date in planned_dates:
                continue
            planned_dates.add(s.date)
        to_import.append(s)
    empire_name = next((s.empire_name for s in reversed(saves) if s.empire_name), "")
    return ImportPlan(
        game_id=game_id,
        empire_name=empire_name,
        saves=saves,
        num_imported=sum(1 for s in saves if s.date in imported),
        num_filtered_out=len(saves) - len(selected_paths),
        to_import=to_import,
        newest_imported_date=max(imported, default=None),
    )


class ImportJob:
    """A background import of one game's saves. Its fields are read by the import page while it runs."""

    RUNNING = "running"
    CANCELLING = "cancelling"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"

    def __init__(
        self,
        plan: ImportPlan,
        save_path: pathlib.Path,
        reprocess_game_ids: Optional[List[str]] = None,
    ):
        """:param reprocess_game_ids: Reprocess the kept copies of these games (the plan's saves) instead of importing."""
        self.reprocess_game_ids = reprocess_game_ids
        self.game_id = plan.game_id
        self.empire_name = plan.empire_name
        self.save_path = save_path
        self.status = ImportJob.RUNNING
        self.total = len(plan.to_import)
        self.done = 0
        self.current_date: Optional[str] = None
        self.errors: List[str] = []
        self.num_errors = 0
        self.num_deferred_by_watcher = 0
        self.started = time.time()
        self.finished: Optional[float] = None
        self._stop_event = threading.Event()
        self._batch: List[pathlib.Path] = [s.path for s in plan.to_import]
        self._batch_index = 0
        self._seen: Set[pathlib.Path] = {s.path for s in plan.saves}
        self._save_filter = save_parser.BatchSavePathMonitor(save_path)
        # a reprocessing job runs alongside the watcher, which is serialized by the process-wide lock
        self._accepts_deferred_saves = not self.is_reprocess  # guarded by _jobs_lock
        self._has_deferred_saves = False  # guarded by _jobs_lock
        self._thread = threading.Thread(
            target=self._run, name=f"bulk-import-{self.game_id}", daemon=True
        )

    @property
    def game_label(self) -> str:
        """The game ID as shown on the import page."""
        if self.game_id == _ALL_GAMES_ID and self.is_reprocess:
            return _("all games")
        return self.game_id

    @property
    def is_reprocess(self) -> bool:
        return self.reprocess_game_ids is not None

    @property
    def is_active(self) -> bool:
        return self.status in (ImportJob.RUNNING, ImportJob.CANCELLING)

    @property
    def elapsed_seconds(self) -> float:
        return (self.finished or time.time()) - self.started

    def cancel(self) -> None:
        with _jobs_lock:
            self._accepts_deferred_saves = False
            if self.status == ImportJob.RUNNING:
                self.status = ImportJob.CANCELLING
        self._stop_event.set()

    def _current_save(self) -> Optional[pathlib.Path]:
        if self._batch_index < len(self._batch):
            return self._batch[self._batch_index]
        return None

    def _record_error(self, message: str) -> None:
        self.num_errors += 1
        if len(self.errors) < _MAX_LISTED_ERRORS:
            self.errors.append(message)

    def _on_save_processed(self, game_name, gamestate_dict) -> None:
        self._batch_index += 1
        self.done += 1
        if gamestate_dict is not None:
            self.current_date = gamestate_dict.get("date") or self.current_date

    def _next_batch(self) -> List[pathlib.Path]:
        """Saves of the game written since the import started; once there are none, stop accepting more."""
        while not self._stop_event.is_set():
            try:
                plan = get_import_plan(
                    self.game_id, self.save_path, exclude=self._seen, save_filter=self._save_filter
                )
            except ImportRefused:
                plan = None
            if plan is not None:
                self._seen.update(s.path for s in plan.saves)
                if plan.to_import:
                    self.total += len(plan.to_import)
                    return [s.path for s in plan.to_import]
            with _jobs_lock:
                # a save the watcher deferred after the scan above needs another scan
                if not self._has_deferred_saves:
                    self._accepts_deferred_saves = False
                    return []
                self._has_deferred_saves = False
        return []

    def _run(self) -> None:
        from stellarisdashboard import cli  # cli imports this module
        from stellarisdashboard.dashboard_app import visualization_data

        game_ids = self.reprocess_game_ids if self.is_reprocess else [self.game_id]
        error_handler = _ErrorCollector(self)
        logging.getLogger().addHandler(error_handler)
        status = ImportJob.FINISHED
        try:
            action = "Reprocessing" if self.is_reprocess else "Importing"
            logger.info(f"{action} {self.total} saves of {self.game_id}")
            if self.is_reprocess:
                cli.f_parse_saves(
                    save_path=self.save_path,
                    save_files=self._batch,
                    replace_existing=True,
                    use_worker_processes=True,
                    stop_event=self._stop_event,
                    on_save_processed=self._on_save_processed,
                )
            else:
                while self._batch:
                    cli.f_parse_saves(
                        save_path=self.save_path,
                        save_files=self._batch,
                        use_worker_processes=True,
                        stop_event=self._stop_event,
                        on_save_processed=self._on_save_processed,
                    )
                    if self._stop_event.is_set():
                        break
                    self._batch = self._next_batch()
                    self._batch_index = 0
        except Exception:
            logger.exception(f"Import of game {self.game_id} failed")
            status = ImportJob.FAILED
        finally:
            logging.getLogger().removeHandler(error_handler)
            # older saves are not picked up by the incremental plot data update
            for game_id in game_ids:
                visualization_data.drop_cached_plot_data(game_id)
            if status == ImportJob.FINISHED and self._stop_event.is_set():
                status = ImportJob.CANCELLED
            with _jobs_lock:
                self._accepts_deferred_saves = False
                self.finished = time.time()
                self.status = status
            logger.info(
                f"Import of game {self.game_id} {status}: {self.done} of {self.total} saves, {self.num_errors} errors"
            )


class _ErrorCollector(logging.Handler):
    """Collects errors logged by the import thread, e.g. saves that failed to parse or were rolled back."""

    def __init__(self, job: ImportJob):
        super().__init__(level=logging.ERROR)
        self._job = job

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._job._thread.ident:
            return
        message = " ".join(record.getMessage().split())
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            message = f"{message} {type(exc).__name__}: {exc}"
        save = self._job._current_save()
        if save is not None:
            message = f"{save.name}: {message.replace(str(save), save.name)}"
        self._job._record_error(message)


_jobs_lock = threading.Lock()
_current_job: Optional[ImportJob] = None  # the running or most recent import


def get_current_job() -> Optional[ImportJob]:
    return _current_job


def start_import(game_id: str) -> ImportJob:
    """Start importing the missing saves of the game in the background, or raise ImportRefused."""
    global _current_job
    save_path = config.CONFIG.save_file_path
    if save_path is None or not save_path.is_dir():
        raise ImportRefused(
            _("The save files folder %(save_path)s does not exist. Check the settings.", save_path=save_path)
        )
    _refuse_if_running()
    plan = get_import_plan(game_id, save_path)
    if not plan.to_import:
        raise ImportRefused(_("All saves of %(game_id)s are already imported.", game_id=game_id))
    with _jobs_lock:
        _refuse_if_running()
        _current_job = ImportJob(plan, save_path)
        _current_job._thread.start()
    return _current_job


def start_reprocess(game_id: Optional[str] = None) -> ImportJob:
    """
    Start importing the kept copies of failed saves again in the background (of one game, by default
    all), or raise ImportRefused. Gamestates of the same date are replaced, copies that import
    cleanly are deleted and the records of the others are updated.
    """
    global _current_job
    _refuse_if_running()
    copies = save_copies.list_kept_copies(game_id)
    if not copies:
        raise ImportRefused(_("There are no kept copies of failed saves to reprocess."))
    infos = []
    for c in copies:
        try:
            infos.append(read_save_info(c.path))
        except OSError:
            logger.warning(f"Kept copy {c.path} disappeared, skipping it")
    if not infos:
        raise ImportRefused(_("There are no kept copies of failed saves to reprocess."))
    game_ids = sorted({i.path.parent.name for i in infos})
    plan = ImportPlan(
        game_id=game_id or (game_ids[0] if len(game_ids) == 1 else _ALL_GAMES_ID),
        empire_name="",
        saves=infos,
        num_imported=0,
        num_filtered_out=0,
        to_import=infos,
        newest_imported_date=None,
    )
    with _jobs_lock:
        _refuse_if_running()
        _current_job = ImportJob(plan, save_copies.failed_saves_path(), reprocess_game_ids=game_ids)
        _current_job._thread.start()
    return _current_job


def _refuse_if_running() -> None:
    job = _current_job
    if job is not None and job.is_active:
        raise ImportRefused(
            _(
                "An import of %(game_id)s is already running. Wait for it to finish or cancel it.",
                game_id=job.game_label,
            )
        )


def cancel_import() -> Optional[ImportJob]:
    job = _current_job
    if job is not None and job.is_active:
        job.cancel()
    return job


def defer_to_running_import(game_id: str) -> bool:
    """
    Called by the save watcher for each new save. True if a running import of the same game will
    import the save instead, so the game's saves are imported in order by a single importer.
    """
    with _jobs_lock:
        job = _current_job
        if job is None or job.game_id != game_id or not job._accepts_deferred_saves:
            return False
        job._has_deferred_saves = True
        job.num_deferred_by_watcher += 1
    logger.info(f"Leaving a new save of game {game_id} to the running import")
    return True
