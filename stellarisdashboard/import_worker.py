"""
Parse saves and import them into the database in worker processes.

Parsing and importing a save take seconds of CPU time that mostly holds the GIL. Done in the
dashboard's process, they would stall the web server (a thread of the same process), so they run
in a process pool. The parsed gamestate never leaves the worker: it reads the save file, imports
it and sends back a small ImportResult. Log records of the workers are handled by the logging of
the submitting process (stdout, log.txt). See docs/import-worker.md.

This module is imported by the worker processes, so it must not import the dashboard app (dash,
plotly, flask), ``cli`` or ``bulk_import``, not even indirectly. test/import_worker_test.py checks that.
"""

import atexit
import collections
import concurrent.futures
import dataclasses
import logging
import logging.handlers
import multiprocessing
import os
import pathlib
import queue
import signal
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from stellarisdashboard import config, country_color_cache, datamodel
from stellarisdashboard.parsing import save_copies, save_parser, timeline

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ImportResult:
    game_id: str
    save_file: pathlib.Path
    game_date: Optional[str] = None
    parsed: bool = False
    imported: bool = False  # a new gamestate was committed
    failures: List[Dict[str, Any]] = dataclasses.field(default_factory=list)  # save_copies.failure
    error: Optional[str] = None  # an unexpected exception, or the worker died
    timings: Dict[str, float] = dataclasses.field(default_factory=dict)  # seconds: "parse", "import"

    def error_messages(self) -> List[str]:
        """One line per failure, e.g. for the error list of the import page."""
        messages = [
            f"{f.get('processor') or f['stage']} failed: {_last_line(f['error'])}" for f in self.failures
        ]
        if self.error and not self.failures:
            messages.append(_last_line(self.error))
        return messages


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


class ImportWorkerError(RuntimeError):
    """A job could not be completed because the workers were shut down."""


def settings_snapshot() -> Dict[str, Any]:
    """The settings the import depends on: every setting, plus the debug mode."""
    snapshot = {key: getattr(config.CONFIG, key) for key in config.SETTINGS}
    snapshot["debug_mode"] = config.CONFIG.debug_mode
    return snapshot


def import_gamestate(
    game_name: str,
    gamestate_dict,
    save_file: pathlib.Path,
    replace_existing: bool = False,
    tle: Optional[timeline.TimelineExtractor] = None,
) -> timeline.TimelineExtractor:
    """
    Import a parsed save into the database. A copy of the save is kept if the import
    or any data processor failed, and a kept copy is removed once it imports cleanly.

    :param tle: The extractor to use, if the caller wants its failures even if this raises.
    """
    tle = tle or timeline.TimelineExtractor()
    try:
        tle.process_gamestate(game_name, gamestate_dict, replace_existing=replace_existing)
    except Exception as e:
        # errors raised before the import started, e.g. a gamestate without a date
        if not tle.failures:
            tle.failures.append(
                save_copies.failure(save_copies.STAGE_IMPORT, save_copies.format_exception(e))
            )
        raise
    finally:
        if tle.failures:
            game_date = gamestate_dict.get("date") if isinstance(gamestate_dict, dict) else None
            save_copies.keep_failed_save(save_file, game_name, tle.failures, game_date)
        elif tle.number_of_parsed_saves and save_copies.is_kept_copy(save_file):
            save_copies.remove_kept_copy(save_file)
    return tle


def after_import(result: ImportResult) -> None:
    """
    What the UI process does after each result, whoever submitted the job. Light work only.
    The plot data is not updated here: the next request updates it incrementally.
    """
    country_color_cache.clear_cached_country_colors()
    datamodel.clear_available_games_cache()


# ----------------------------------------------------------------------------- worker process


def _init_worker(log_queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is handled by the dashboard, which stops us
    sys.stdout = open(os.devnull, "w")
    root = logging.getLogger()
    for handler in list(root.handlers):
        # the stdout and log.txt handlers of config.initialize(): the submitting process logs for us
        root.removeHandler(handler)
        handler.close()
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    # The pool's workers wait for jobs forever, also when the dashboard was killed: exit with it.
    threading.Thread(target=_exit_with_parent, daemon=True).start()
    try:
        from stellarisdashboard import game_info

        game_info.get_global_renderer()  # load the game's text now rather than during the first job
    except Exception:
        logger.exception("Failed to load the game text, it will be loaded on first use")


def _exit_with_parent() -> None:
    multiprocessing.parent_process().join()
    os._exit(0)


def _apply_settings(settings: Dict[str, Any]) -> None:
    previous_output_path = config.CONFIG.base_output_path
    for key, value in settings.items():
        setattr(config.CONFIG, key, value)
    if config.CONFIG.base_output_path != previous_output_path:
        datamodel.dispose_engines()  # the engines point into the previous output folder
    logging.getLogger().setLevel(config.LOG_LEVELS.get(config.CONFIG.log_level, logging.INFO))


def _run_job(save_file: pathlib.Path, game_id: str, replace_existing: bool, settings) -> ImportResult:
    result = ImportResult(game_id=game_id, save_file=save_file)
    gamestate = None
    tle = None
    try:
        _apply_settings(settings)
        t_start = time.perf_counter()
        try:
            gamestate = save_parser.parse_save(save_file)
            result.parsed = True
            result.game_date = gamestate.get("date")
        except Exception as e:
            logger.exception(f"Error while reading save file {save_file}:")
            result.failures.append(
                save_copies.failure(save_copies.STAGE_PARSE, save_copies.format_exception(e))
            )
            save_copies.keep_failed_save(save_file, game_id, result.failures)
        result.timings["parse"] = time.perf_counter() - t_start
        save_copies.back_up_save(save_file, game_id)
        if gamestate is not None:
            t_start = time.perf_counter()
            tle = timeline.TimelineExtractor()
            try:
                import_gamestate(game_id, gamestate, save_file, replace_existing=replace_existing, tle=tle)
            finally:
                result.timings["import"] = time.perf_counter() - t_start
            result.imported = tle.number_of_parsed_saves > 0
    except Exception as e:
        logger.exception(f"Importing save file {save_file} failed:")
        result.error = save_copies.format_exception(e)
    finally:
        if tle is not None:
            result.failures.extend(tle.failures)
    return result


# ----------------------------------------------------------------------------- UI process


def _handle_worker_logs(log_queue) -> None:
    try:
        while (record := log_queue.get()) is not None:
            logging.getLogger(record.name).handle(record)
    except (EOFError, OSError):
        pass  # the queue was closed at exit


class ImportWorkerPool:
    """
    Imports saves in up to `size` worker processes. Thread-safe.

    The saves of one game are imported one at a time, in submission order, so two processes never
    write the same game's database at once (in WAL mode, a write after a read fails at once if
    another process committed in between). Different games are imported in parallel.
    """

    def __init__(self, size: int = 1, start_method: Optional[str] = None):
        """:param start_method: "spawn", "forkserver" or "fork"; by default the platform's."""
        self._size = max(1, int(size))
        self._context = multiprocessing.get_context(start_method)
        self._lock = threading.RLock()
        self._executor: Optional[concurrent.futures.ProcessPoolExecutor] = None
        self._log_queue = None
        # finished jobs: handled in a thread of our own, not in the executor's thread, which holds
        # the executor's lock while it fails the jobs of a dead worker
        self._done: "queue.SimpleQueue[Optional[tuple]]" = queue.SimpleQueue()
        # per game: the jobs not finished yet, the first one is with the executor
        self._games: Dict[str, Deque[Tuple[tuple, concurrent.futures.Future]]] = {}
        self._running: Dict[concurrent.futures.Future, Tuple[tuple, concurrent.futures.Future]] = {}
        self._closed = False

    @property
    def num_waiting(self) -> int:
        """Jobs that are queued or running."""
        with self._lock:
            return sum(len(jobs) for jobs in self._games.values())

    def start(self) -> None:
        """Start a worker now (it loads the game text), instead of with the first job."""
        with self._lock:
            if not self._closed:
                self._get_executor().submit(int)

    def submit(
        self, save_file: pathlib.Path, game_id: str, replace_existing: bool = False
    ) -> "concurrent.futures.Future[ImportResult]":
        """
        Queue a save for import with the settings as they are now. The future resolves to an
        ImportResult, also (with an error) if the worker dies. It can be cancelled until the job
        starts, and fails with ImportWorkerError if the pool is shut down first. Callbacks added
        to it run in a thread of the pool.
        """
        future = concurrent.futures.Future()
        with self._lock:
            if self._closed:
                raise ImportWorkerError("The import workers have been shut down")
            jobs = self._games.setdefault(game_id, collections.deque())
            jobs.append(((pathlib.Path(save_file), game_id, replace_existing), future))
            if len(jobs) == 1:
                self._start_next(game_id)
        return future

    def _get_executor(self) -> concurrent.futures.ProcessPoolExecutor:
        # called with the lock held
        if self._executor is None:
            if self._log_queue is None:
                self._log_queue = self._context.Queue()
                threading.Thread(
                    target=_handle_worker_logs, args=(self._log_queue,), name="import-worker-logs", daemon=True
                ).start()
                threading.Thread(target=self._handle_finished_jobs, name="import-worker-results", daemon=True).start()
            self._executor = concurrent.futures.ProcessPoolExecutor(
                self._size, mp_context=self._context, initializer=_init_worker, initargs=(self._log_queue,)
            )
        return self._executor

    def _start_next(self, game_id: str) -> None:
        # called with the lock held: send the game's next job that was not cancelled to the executor
        jobs = self._games[game_id]
        while jobs:
            (save_file, _, _), future = job = jobs[0]
            if future.set_running_or_notify_cancel():
                try:
                    running = self._submit(job[0])
                except Exception as e:
                    future.set_result(self._failed(game_id, save_file, f"could not start the import worker: {e!r}"))
                else:
                    self._running[running] = job
                    running.add_done_callback(lambda running, game_id=game_id: self._done.put((game_id, running)))
                    return
            jobs.popleft()
        del self._games[game_id]

    def _submit(self, args: tuple) -> concurrent.futures.Future:
        try:
            return self._get_executor().submit(_run_job, *args, settings_snapshot())
        except concurrent.futures.BrokenExecutor:
            self._executor = None  # a worker died since the last job: start new ones
            return self._get_executor().submit(_run_job, *args, settings_snapshot())

    def _handle_finished_jobs(self) -> None:
        while (item := self._done.get()) is not None:
            self._finished(*item)

    def _finished(self, game_id: str, running: concurrent.futures.Future) -> None:
        with self._lock:
            job = self._running.pop(running, None)
            if job is None or running.cancelled():
                return  # stopped by shutdown(), which fails the job
            (save_file, _, _), future = job
            try:
                result = running.result()
            except Exception as e:
                # A worker died (a crash of the parser, the out-of-memory killer). The executor then
                # fails all the jobs it had, so each of them keeps a copy of its save.
                result = self._failed(game_id, save_file, f"the import worker failed: {e!r}")
            if not self._closed:
                self._games[game_id].popleft()
                self._start_next(game_id)
        future.set_result(result)

    @staticmethod
    def _failed(game_id: str, save_file: pathlib.Path, error: str) -> ImportResult:
        logger.error(f"Not importing {save_file}: {error}")
        result = ImportResult(game_id, save_file, error=error)
        result.failures.append(save_copies.failure(save_copies.STAGE_IMPORT, error))
        save_copies.keep_failed_save(save_file, game_id, result.failures)
        return result

    def shutdown(self, timeout: float = 10.0) -> None:
        """
        Stop the workers: running jobs may finish for up to `timeout` seconds, then the workers are
        terminated. Jobs that did not finish fail with ImportWorkerError. No more jobs are accepted.
        """
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
            jobs = [job for game_jobs in self._games.values() for job in game_jobs]
            self._games.clear()
            running = list(self._running)
        for job in running:
            job.cancel()  # succeeds for the jobs that did not start yet
        if executor is not None:
            _, not_done = concurrent.futures.wait(running, timeout)
            with self._lock:
                self._running.clear()  # the jobs left fail below
            if not_done:
                logger.warning("The import workers did not stop in time, terminating them")
                executor.terminate_workers()
            else:
                executor.shutdown()
        for _, future in jobs:
            if not future.done():
                future.set_exception(ImportWorkerError("The import workers were shut down before the import"))
        if self._log_queue is not None:
            self._log_queue.put(None)  # ends the two threads of the pool
            self._done.put(None)


_worker: Optional[ImportWorkerPool] = None
_worker_lock = threading.Lock()


def get_import_worker() -> ImportWorkerPool:
    """
    The import workers of this process, created on first use: a pool of `threads` workers
    (the setting is read now), shared by the save monitor and the import page.
    """
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = ImportWorkerPool(config.CONFIG.threads)
        return _worker


def shutdown_import_worker(timeout: float = 10.0) -> None:
    """Stop the process's import workers, if there are any. The next get_import_worker() makes new ones."""
    global _worker
    with _worker_lock:
        worker, _worker = _worker, None
    if worker is not None:
        worker.shutdown(timeout)


atexit.register(shutdown_import_worker, 3.0)
