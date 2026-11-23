# The import worker process

Parsing a save and importing it into the database take seconds of CPU time that mostly holds the
GIL. Done in the dashboard's process, that stalls the web server (it runs as a thread of the same
process), so the work happens in one long-lived worker process, `stellarisdashboard/import_worker.py`.

```
dashboard process                               worker process
  web server threads                              import_worker.worker_main
  save monitor (cli.f_monitor_saves)  --job-->      parse the save (Rust parser)
  import page (bulk_import)                         back up the save, import it into the database
  reader thread (blocks on the pipe)  <-result--    (log records)
  logging (stdout, log.txt)           <--logs---
```

The live save monitor, the import page's bulk import and reprocessing, and the CLI batch commands
(`parse-saves`, `reprocess-failed-saves`) all use these workers: they submit paths and get small
results back. Nothing parses or imports in the process that submits.

- `ImportWorker.submit(save_file, game_id, ...)` returns a `concurrent.futures.Future` for an
  `ImportResult` (parsed, imported, failures, error, timings). A worker runs one job at a time, in the order
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
- `ImportWorkerPool` (see below) is what the dashboard and the CLI use; one `ImportWorker` is one
  process with its own job queue.
- After each result the UI process calls `import_worker.after_import(result)`: it clears the
  country color cache and the available games cache. It does not update the plot data; the next
  request does that incrementally.

## Several workers: the pool and the `threads` setting

`ImportWorkerPool(size)` holds up to `size` `ImportWorker`s (each a process with its own queue,
crash handling and log forwarding) and has the same `submit`/`start`/`shutdown` interface.
`get_import_worker()` returns the dashboard's one shared pool, of `threads` workers (the setting
is read when the pool is created and needs a restart). The save monitor and the import page both
submit to it. With the default `threads: 1` that is one worker, so a bulk import and live saves
take turns in one queue.

**Routing with game affinity.** While a worker has unfinished jobs of a game, every new job of
that game goes to the same worker. Otherwise the job goes to an idle worker, else to a new worker
if fewer than `size` exist, else to the least loaded one. So the saves of one game are always
imported in order by one process at a time, and two processes never write the same game's
database at once. That is a correctness rule, not only tidiness: in WAL mode a transaction that
has read and then tries to write fails at once with `SQLITE_BUSY` if another process committed
in between; the busy timeout does not help there. Games without unfinished jobs are not pinned: the
next job of the game may go to another worker. Idle workers are not stopped; they keep the
memory of their last import until the dashboard ends.

**Why `threads` means worker processes.** The setting used to be the number of processes that
parsed saves in parallel. Parsing more saves of one game at once did not make an import faster:
the database stage is serial and in order for a game, and the parsed gamestate (tens of
megabytes) had to be pickled to the process that imported it, which cost more than it saved. In
the worker the parse and the import of a save are one job, nothing is pickled, and what can run in
parallel is the import of different games (separate database files). So `threads` is now the
number of worker processes, and more than 1 only helps when saves of several games are imported
at once (a batch of several games, or a bulk import next to a game that is being played). Each
worker is a process of its own with the memory of an import.

**Batches.** `cli.f_parse_saves` selects the saves (`BatchSavePathMonitor` finds them and applies
`skip_saves`), submits them to a pool and calls `on_save_processed(result)` per save, in order per
game. At most 2 jobs per game are with the workers at a time: the worker never idles between two
saves of the batch, a cancel (`stop_event`: no more submissions, jobs in flight finish) takes
effect within about two saves, and a long batch cannot fill the shared queue ahead of live saves of
other games. The import page passes the shared pool; the CLI creates a pool of `--threads`
workers and shuts it down at the end, so a batch of several games imports up to that many games in
parallel. Reprocessing kept copies of failed saves works the same; a live save of a game being
reprocessed goes to the same worker and is imported between the reprocessed saves.

**The import page's error list.** Each request of a bulk job carries the log tag
`<job tag>:<save file>`; the forwarded records of the worker carry it as `import_tag`, and
`bulk_import._ErrorCollector` lists the error records whose tag starts with the job's tag (plus the
records of the job's own thread). Records of the live monitor have the tag `monitor` and are not
listed. If a worker dies during a job, the pool's error record carries the job's tag too.

## What does not cross the process boundary

**Locks.** `datamodel._DB_LOCKS` (one write lock per game) and `timeline._process_gamestate_lock`
are in-process locks. In the dashboard all writes happen in the workers, one job at a time per
worker and one worker per game at a time (see the pool), so they are never contended there. They
still protect in-process callers: the CLI commands and the tests. Between processes SQLite's own
locking applies. The databases use WAL, so the dashboard reads while the worker writes. A second
writer of the same game, for example a CLI import started while the dashboard runs, waits for the
write lock: every connection has a busy timeout of 30 s (`_SQLITE_BUSY_TIMEOUT_MS`, set as the
`sqlite3` connect timeout and as `PRAGMA busy_timeout`). Waiting only works if the writer asks for
the lock before it reads. pysqlite starts transactions lazily, so a write session would begin as a
read transaction (a `SAVEPOINT` or a `SELECT` first), with a WAL snapshot of the database. If
another process commits before this session's first write, the upgrade to a write transaction
fails at once with "database is locked": the busy timeout does not apply, because waiting cannot
make the stale snapshot current. So every session from `get_db_session(write=True)` runs
`BEGIN IMMEDIATE` when each of its transactions begins (an `after_begin` listener; savepoints are
not affected), and the second writer waits at that point. Read sessions stay deferred and never
take the write lock.

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
that import, which imports it after its batch. (Even without that, the pool would send the save to
the worker that has the game's jobs; the deferral keeps the import page's counts and its
"imported after the others" note right.) A reprocessing job does not take deferred saves.

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
