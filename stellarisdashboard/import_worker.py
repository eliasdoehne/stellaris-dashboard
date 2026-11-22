"""
Parse saves and import them into the database in one long-lived worker process.

Parsing a save and importing it into the database take a few seconds of CPU time, most of it
inside long C calls or pure Python loops that hold the GIL. Done inside the dashboard's process,
they stall the web server's threads (the UI), so both happen in a separate process. The parsed
gamestate (tens of megabytes) never crosses the process boundary: the worker reads the save
file, imports it and sends back a small ImportResult.

The process is started by the first ``ImportWorker.submit`` and handles one job at a time, in
submission order. The UI process only holds the job queue and one reader thread that blocks on
the worker's messages: job results, and the worker's log records, which are handled by the
logging of the UI process (stdout, log.txt) so the worker never writes the log file itself.
See docs/import-worker.md for what does not cross the process boundary (settings, locks,
logging, start methods) and how each is handled.

This module is imported by the worker process, so it must not import the dashboard app (dash,
plotly, flask), ``cli`` or ``bulk_import``, not even indirectly. test/import_worker_test.py checks that.
"""

import atexit
import collections
import concurrent.futures
import dataclasses
import logging
import multiprocessing
import multiprocessing.connection
import os
import pathlib
import signal
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional

from stellarisdashboard import config, country_color_cache, datamodel
from stellarisdashboard.parsing import save_copies, save_parser, timeline

logger = logging.getLogger(__name__)

# how often an idle worker checks whether the dashboard process is still there
_PARENT_CHECK_SECONDS = 1.0
# a job is tried on at most this many workers if they die before they start it
_MAX_DISPATCH_ATTEMPTS = 2


@dataclasses.dataclass
class ImportRequest:
    job_id: int
    save_file: pathlib.Path
    game_id: str
    replace_existing: bool
    settings: Dict[str, Any]  # see settings_snapshot
    log_tag: str  # names the client; attached to every log record of the job as import_tag


@dataclasses.dataclass
class ImportResult:
    job_id: int
    game_id: str
    save_file: pathlib.Path
    game_date: Optional[str] = None
    parsed: bool = False
    imported: bool = False  # a new gamestate was committed
    failures: List[Dict[str, Any]] = dataclasses.field(default_factory=list)  # save_copies.failure
    error: Optional[str] = None  # an unexpected exception, or the worker died
    timings: Dict[str, float] = dataclasses.field(default_factory=dict)  # seconds: "parse", "import"


class ImportWorkerError(RuntimeError):
    """A job could not be completed because the worker was shut down."""


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


class _ForwardingHandler(logging.Handler):
    """Sends the log records of the worker process to the UI process."""

    def __init__(self, connection, send_lock):
        super().__init__()
        self._connection = connection
        self._send_lock = send_lock
        self.tag = ""
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = {
                key: value
                for key, value in record.__dict__.items()
                if isinstance(value, (str, int, float, bool, type(None)))
            }
            # the formatted message and traceback instead of arguments and exception objects,
            # which need not be picklable
            data["msg"] = record.getMessage()
            data["args"] = None
            data["exc_info"] = None
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                data["exc_text"] = record.exc_text or self._formatter.formatException(record.exc_info)
                data["exc_type_name"] = type(exc).__name__
                data["exc_message"] = str(exc)
            data["import_tag"] = self.tag
            with self._send_lock:
                self._connection.send(("log", data))
        except Exception:
            self.handleError(record)


def _worker_setup_logging(connection, send_lock) -> _ForwardingHandler:
    root = logging.getLogger()
    for handler in list(root.handlers):
        # the stdout and log.txt handlers config.initialize() installed: the UI process logs for us
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()
    handler = _ForwardingHandler(connection, send_lock)
    root.addHandler(handler)
    return handler


def _apply_settings(settings: Dict[str, Any]) -> None:
    previous_output_path = config.CONFIG.base_output_path
    for key, value in settings.items():
        setattr(config.CONFIG, key, value)
    if config.CONFIG.base_output_path != previous_output_path:
        datamodel.dispose_engines()  # the engines point into the previous output folder
    level = config.LOG_LEVELS.get(config.CONFIG.log_level, logging.INFO)
    logging.getLogger().setLevel(level)


def _run_job(request: ImportRequest) -> ImportResult:
    result = ImportResult(job_id=request.job_id, game_id=request.game_id, save_file=request.save_file)
    save_file, game_id = request.save_file, request.game_id
    gamestate = None
    tle = None
    try:
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
                import_gamestate(
                    game_id, gamestate, save_file, replace_existing=request.replace_existing, tle=tle
                )
            finally:
                result.timings["import"] = time.perf_counter() - t_start
            result.imported = tle.number_of_parsed_saves > 0
    except Exception as e:
        logger.exception(f"Importing save file {save_file} failed:")
        result.error = save_copies.format_exception(e)
    finally:
        if tle is not None:
            result.failures.extend(tle.failures)
        del gamestate, tle  # before the next job, so only one gamestate is in memory
    return result


def worker_main(request_connection, message_connection, initial_settings: Dict[str, Any]) -> None:
    """Entry point of the worker process: handle jobs until told to stop or the dashboard is gone."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is handled by the dashboard, which stops us
    sys.stdout = open(os.devnull, "w")
    send_lock = threading.Lock()
    log_handler = _worker_setup_logging(message_connection, send_lock)
    parent = multiprocessing.parent_process()
    try:
        _apply_settings(initial_settings)
        try:
            # load the game's text now rather than during the first job
            from stellarisdashboard import game_info

            game_info.get_global_renderer()
        except Exception:
            logger.exception("Failed to load the game text, it will be loaded on first use")
        while True:
            if not request_connection.poll(_PARENT_CHECK_SECONDS):
                if parent is not None and not parent.is_alive():
                    return
                continue
            try:
                request = request_connection.recv()
            except (EOFError, OSError):
                return
            if request is None:
                return
            try:
                _apply_settings(request.settings)
                log_handler.tag = request.log_tag
                with send_lock:
                    message_connection.send(("started", request.job_id))
                result = _run_job(request)
            except Exception as e:  # e.g. invalid settings; the worker must survive
                logger.exception("Import job failed:")
                result = ImportResult(
                    job_id=request.job_id,
                    game_id=request.game_id,
                    save_file=request.save_file,
                    error=save_copies.format_exception(e),
                )
            try:
                with send_lock:
                    message_connection.send(("result", result))
            except OSError:
                return  # the dashboard is gone
            log_handler.tag = ""
            del request, result
    finally:
        logging.getLogger().removeHandler(log_handler)
        try:
            message_connection.close()
        except OSError:
            pass


# ----------------------------------------------------------------------------- UI process


@dataclasses.dataclass
class _Job:
    request: ImportRequest
    future: concurrent.futures.Future
    started: bool = False  # the worker said it started the job
    attempts: int = 0


class _WorkerProcess:
    """One started worker process with its pipes and the thread that reads its messages."""

    def __init__(self, context, initial_settings, on_message, on_exit):
        request_reader, self.request_writer = context.Pipe(duplex=False)
        self.message_reader, message_writer = context.Pipe(duplex=False)
        self.process = context.Process(
            target=worker_main,
            args=(request_reader, message_writer, initial_settings),
            name="import-worker",
            daemon=True,  # never outlives the dashboard
        )
        self.process.start()
        # close our copies of the worker's ends, or we would never see the pipe close when it dies
        request_reader.close()
        message_writer.close()
        self._on_message = on_message
        self._on_exit = on_exit
        self._reader = threading.Thread(target=self._read_messages, name="import-worker-reader", daemon=True)
        self._reader.start()

    def _read_messages(self) -> None:
        connection, sentinel = self.message_reader, self.process.sentinel
        while True:
            try:
                ready = multiprocessing.connection.wait([connection, sentinel])
                if connection in ready:
                    try:
                        message = connection.recv()
                    except (EOFError, OSError):
                        break
                    try:
                        self._on_message(self, message)
                    except Exception:
                        logger.exception("Could not handle a message of the import worker")
                    continue  # read everything the worker sent before it ended
                break
            except (OSError, ValueError):
                break
        self.process.join(10)
        self._on_exit(self, self.process.exitcode)

    def send(self, request: Optional[ImportRequest]) -> None:
        self.request_writer.send(request)

    def close_pipes(self) -> None:
        for connection in (self.request_writer, self.message_reader):
            try:
                connection.close()
            except OSError:
                pass


class ImportWorker:
    """
    Client of the import worker process, for the UI process. Thread-safe.

    Jobs run one at a time, in the order they were submitted. A job is sent to the worker only
    once the one before it is done, so after a crash the jobs behind it are still here and are
    given to a new worker.
    """

    def __init__(self, start_method: Optional[str] = None):
        """:param start_method: "spawn", "forkserver" or "fork"; by default the platform's."""
        self._context = multiprocessing.get_context(start_method)
        self._lock = threading.RLock()
        self._worker: Optional[_WorkerProcess] = None
        self._queue: Deque[_Job] = collections.deque()  # not sent yet
        self._current: Optional[_Job] = None  # sent to the worker, no result yet
        self._next_job_id = 0
        self._closed = False

    def submit(
        self,
        save_file: pathlib.Path,
        game_id: str,
        replace_existing: bool = False,
        log_tag: str = "monitor",
    ) -> "concurrent.futures.Future[ImportResult]":
        """
        Queue a save for import. The future resolves to an ImportResult; it is also resolved
        (with an error result) if the worker dies during the job. It fails with ImportWorkerError
        if the worker is shut down first. The worker uses the settings as they are now.
        Callbacks added to the future run in the worker's reader thread.
        """
        with self._lock:
            if self._closed:
                raise ImportWorkerError("The import worker has been shut down")
            request = ImportRequest(
                job_id=self._next_job_id,
                save_file=pathlib.Path(save_file),
                game_id=game_id,
                replace_existing=replace_existing,
                settings=settings_snapshot(),
                log_tag=log_tag,
            )
            self._next_job_id += 1
            job = _Job(request, concurrent.futures.Future())
            self._queue.append(job)
            self._dispatch()
            return job.future

    def start(self) -> None:
        """Start the worker process now (it also loads the game text), instead of with the first job."""
        with self._lock:
            if not self._closed:
                self._ensure_worker()

    @property
    def num_waiting(self) -> int:
        """Jobs that are queued or running."""
        with self._lock:
            return len(self._queue) + (self._current is not None)

    def _ensure_worker(self) -> _WorkerProcess:
        # called with the lock held
        if self._worker is None:
            self._worker = _WorkerProcess(
                self._context, settings_snapshot(), self._on_message, self._on_worker_exit
            )
            logger.info(f"Started the import worker process (pid {self._worker.process.pid})")
        return self._worker

    def _dispatch(self) -> None:
        """Send the next queued job to the worker if it is idle. Starts the worker if needed."""
        with self._lock:
            while self._current is None and self._queue and not self._closed:
                job = self._queue.popleft()
                try:
                    worker = self._ensure_worker()
                except Exception as e:
                    self._fail(job, f"Could not start the import worker: {e!r}")
                    continue
                job.attempts += 1
                self._current = job
                try:
                    worker.send(job.request)
                except Exception as e:
                    if worker.process.is_alive():
                        self._current = None
                        self._fail(job, f"Could not send {job.request.save_file} to the import worker: {e!r}")
                    # else: the worker died and its exit handler takes the job from here

    def _fail(self, job: _Job, error: str, keep_copy: bool = False) -> None:
        request = job.request
        # tagged like the records of the worker, so the client of the job sees it
        logger.error(f"Not importing {request.save_file}: {error}", extra={"import_tag": request.log_tag})
        result = ImportResult(request.job_id, request.game_id, request.save_file, error=error)
        if keep_copy:
            result.failures.append(save_copies.failure(save_copies.STAGE_IMPORT, error))
            save_copies.keep_failed_save(request.save_file, request.game_id, result.failures)
        if not job.future.done():
            job.future.set_result(result)

    def _on_message(self, worker: _WorkerProcess, message) -> None:
        kind, payload = message
        if kind == "log":
            record = logging.makeLogRecord(payload)
            logging.getLogger(record.name).handle(record)
        elif kind == "started":
            with self._lock:
                if self._current is not None and self._current.request.job_id == payload:
                    self._current.started = True
        elif kind == "result":
            with self._lock:
                job = self._current
                if job is None or job.request.job_id != payload.job_id:
                    logger.warning(f"Ignoring the result of unexpected import job {payload.job_id}")
                    return
                self._current = None
                self._dispatch()
            job.future.set_result(payload)

    def _on_worker_exit(self, worker: _WorkerProcess, exitcode: Optional[int]) -> None:
        worker.close_pipes()
        with self._lock:
            if worker is not self._worker:
                return
            self._worker = None
            job, self._current = self._current, None
            closed = self._closed
        if closed:
            return
        if job is not None:
            if job.started or job.attempts >= _MAX_DISPATCH_ATTEMPTS:
                self._fail(
                    job,
                    f"the import worker exited with code {exitcode} while importing {job.request.save_file}",
                    keep_copy=True,
                )
            else:
                with self._lock:
                    self._queue.appendleft(job)  # never started: another worker takes it
        elif self._queue:
            logger.warning(f"The import worker exited with code {exitcode}, starting a new one")
        self._dispatch()

    def shutdown(self, timeout: float = 10.0) -> None:
        """
        Stop the worker: it finishes the job it is on, for up to `timeout` seconds. Jobs that did
        not finish fail with ImportWorkerError. No more jobs are accepted.
        """
        with self._lock:
            self._closed = True
            worker, self._worker = self._worker, None
            jobs = list(self._queue) + ([self._current] if self._current is not None else [])
            self._queue.clear()
            self._current = None
        if worker is not None:
            try:
                worker.send(None)
            except (OSError, ValueError):
                pass
            process = worker.process
            process.join(timeout)
            if process.is_alive():
                logger.warning("The import worker did not stop in time, terminating it")
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
            worker._reader.join(5)
            worker.close_pipes()
        for job in jobs:
            if not job.future.done():
                job.future.set_exception(
                    ImportWorkerError(f"The import worker was shut down before importing {job.request.save_file}")
                )


class ImportWorkerPool:
    """
    Up to `size` import worker processes (ImportWorker instances, started as needed) with the same
    interface as one ImportWorker: ``submit``, ``start``, ``num_waiting``, ``shutdown``. Thread-safe.

    Jobs of one game always go to the same worker while it has unfinished jobs of that game, so a
    game's saves are imported in order by one process and two processes never write the same
    game's database at the same time. A job of a game without unfinished jobs goes to an idle
    worker, else to a new worker if fewer than `size` exist, else to the least loaded one. With
    size 1 this is a single ImportWorker. Workers are not stopped while idle.
    """

    def __init__(self, size: int = 1, start_method: Optional[str] = None, worker_factory=None):
        self._size = max(1, int(size))
        self._factory = worker_factory or (lambda: ImportWorker(start_method))
        self._lock = threading.RLock()
        self._workers: List[ImportWorker] = []
        self._load: List[int] = []  # unfinished jobs per worker
        self._games: Dict[str, List[int]] = {}  # game id -> [worker index, unfinished jobs]
        self._closed = False

    @property
    def size(self) -> int:
        return self._size

    @property
    def num_workers(self) -> int:
        """Workers created so far (their processes start with their first job)."""
        with self._lock:
            return len(self._workers)

    @property
    def num_waiting(self) -> int:
        """Jobs that are queued or running."""
        with self._lock:
            return sum(self._load)

    def _choose(self, game_id: str) -> int:
        # called with the lock held
        entry = self._games.get(game_id)
        if entry is not None:
            return entry[0]
        for index, load in enumerate(self._load):
            if load == 0:
                return index
        if len(self._workers) < self._size:
            self._workers.append(self._factory())
            self._load.append(0)
            return len(self._workers) - 1
        return min(range(len(self._load)), key=self._load.__getitem__)

    def submit(
        self,
        save_file: pathlib.Path,
        game_id: str,
        replace_existing: bool = False,
        log_tag: str = "monitor",
    ) -> "concurrent.futures.Future[ImportResult]":
        """As ImportWorker.submit, on the worker that has the game's unfinished jobs, or else the best one."""
        with self._lock:
            if self._closed:
                raise ImportWorkerError("The import worker has been shut down")
            index = self._choose(game_id)
            entry = self._games.setdefault(game_id, [index, 0])
            entry[1] += 1
            self._load[index] += 1
            worker = self._workers[index]
        # Outside our lock: a worker resolves futures (and so calls _finished) while holding its own lock.
        try:
            future = worker.submit(save_file, game_id, replace_existing, log_tag)
        except BaseException:
            self._finished(game_id, index)
            raise
        future.add_done_callback(lambda _future: self._finished(game_id, index))
        return future

    def _finished(self, game_id: str, index: int) -> None:
        with self._lock:
            self._load[index] -= 1
            entry = self._games.get(game_id)
            if entry is not None:
                entry[1] -= 1
                if entry[1] <= 0:
                    del self._games[game_id]

    def start(self) -> None:
        """Start the first worker now (it also loads the game text), instead of with the first job."""
        with self._lock:
            if self._closed:
                return
            if not self._workers:
                self._workers.append(self._factory())
                self._load.append(0)
            worker = self._workers[0]
        worker.start()

    def shutdown(self, timeout: float = 10.0) -> None:
        """Stop all workers at once, as ImportWorker.shutdown does for each. No more jobs are accepted."""
        with self._lock:
            self._closed = True
            workers = list(self._workers)
        threads = [threading.Thread(target=w.shutdown, args=(timeout,)) for w in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()


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
