# The import worker process

Parsing a save and importing it into the database take seconds of CPU time that mostly holds the
GIL. Done in the dashboard's process, that stalls the web server (it runs as a thread of the same
process), so the work happens in one long-lived worker process, `stellarisdashboard/import_worker.py`.

```
dashboard process                               worker process
  web server threads                              import_worker.worker_main
  save monitor (cli.f_monitor_saves)  --job-->      parse the save (Rust parser)
  reader thread (blocks on the pipe)  <-result--    back up the save, import it into the database
  logging (stdout, log.txt)           <--logs---    (log records)
```

- `ImportWorker.submit(save_file, game_id, ...)` returns a `concurrent.futures.Future` for an
  `ImportResult` (parsed, imported, failures, error, timings). Jobs run one at a time in the order
  they were submitted. Only a path and a few settings go to the worker; only the small result comes
  back. The parsed gamestate never leaves the worker, and is dropped before the next job.
- The process starts with the first job (the save monitor starts it at startup, so the game's
  text is loaded in the worker before the first save). It is a daemon process, stopped by
  `shutdown()`, by an `atexit` handler and by itself when the dashboard is gone (it checks its
  parent while idle), so a killed dashboard leaves no worker behind.
- If the worker dies during a job (a crash of the Rust parser, the out-of-memory killer), that job
  fails with an error result and a kept copy of the save (`failed_saves`, stage `import`, "the
  import worker exited with code N"). The jobs queued behind it are not lost: the client sends
  only one job at a time, so it still has them, starts a new worker and sends them in order.
- After each result the UI process calls `import_worker.after_import(result)`: it clears the
  country color cache and the available games cache. It does not update the plot data; the next
  request does that incrementally.

## What does not cross the process boundary

**Locks.** `datamodel._DB_LOCKS` (one write lock per game) and `timeline._process_gamestate_lock`
are in-process locks. In the dashboard all writes happen in the worker, one job at a time, so
they are never contended there. They still protect in-process callers: the CLI commands and the
tests. Between processes SQLite's own locking applies. The databases use WAL, so the dashboard
reads while the worker writes. A second writer of the same game, for example a CLI import started
while the dashboard runs, waits for the write lock: every connection has a busy timeout of 30 s
(`_SQLITE_BUSY_TIMEOUT_MS`, set as the `sqlite3` connect timeout and as `PRAGMA busy_timeout`).

**Opening a database for the first time.** `datamodel._setup_engine` migrates the database to the
current schema (it creates all tables of a new one). The dashboard's warm-up and the worker can do
that at the same time, for example for a game that is new: both saw "no tables" and both ran
`CREATE TABLE`, so one failed with "table already exists". The migration now first looks without a
lock. Only if something is to be changed does it take SQLite's write lock (`BEGIN IMMEDIATE`) and
look again, so the second process finds nothing left to do. Opening an up-to-date database takes no
lock and never waits for a running import. `Config.db_path` creates the `db` folder with
`exist_ok` for the same reason.

**The save monitor's coordination with the import page.** `bulk_import.defer_to_running_import`
looks at the import job of the import page, which lives in the dashboard process, so the monitor
keeps calling it there, before submitting a save. A save of a game with a running import is left to
that import.

**Settings.** `config.CONFIG` is per process. Each job carries a snapshot of every setting of
`config.SETTINGS` plus `debug_mode`, taken when the job is submitted, and the worker applies it
before the job. A change on the settings page therefore reaches the worker with the next job. The
worker is also started with a snapshot. If `base_output_path` changed, the worker drops its cached
database engines (`datamodel.dispose_engines`). Settings that are only read once per process, for
example the game's text (`stellaris_language`, the game paths), need a restart of the dashboard,
as before.

**Logging.** The worker removes the log handlers its own `config.initialize()` installed (stdout,
`log.txt`) and sends every log record to the dashboard process, which handles it like its own
(`logging.getLogger(record.name).handle(record)`), so the dashboard's log has the worker's lines
and the worker never writes `log.txt` itself. A record carries the formatted message (without the
traceback), the traceback as `exc_text`, the exception's type name and message as `exc_type_name`
and `exc_message`, `import_tag` (the `log_tag` of the job, "monitor" for the save monitor) and the
worker's `processName`, `import-worker`. The worker's level is the `log_level` setting.

**Start method.** The worker uses the platform's default `multiprocessing` start method:
forkserver on Linux (Python 3.14), spawn on Windows and macOS. Everything that is sent is picklable
(small dataclasses, `Path`s, plain setting values), the entry point is a module-level function and
the connections are handed to the process at start. The tests run the worker with the default
method and with an explicit spawn context.

## PyInstaller

`pyinstaller-package.spec` needs no change. With spawn the child process starts the same
executable with `--multiprocessing-fork`; `multiprocessing.freeze_support()` recognises that and
runs the worker instead of the dashboard. The package's `__main__.py` now calls it before its heavy
imports (it is a no-op otherwise), so a worker in a frozen build starts without importing scipy
and the web server first. `stellarisdashboard.import_worker` is found by PyInstaller's import
analysis because `cli` imports it. The worker imports neither dash nor plotly.
