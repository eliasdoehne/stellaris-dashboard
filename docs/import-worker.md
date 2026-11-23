# The import worker processes

Parsing a save and importing it into the database take seconds of CPU time that mostly holds the
GIL. Done in the dashboard's process, that stalls the web server (it runs as a thread of the same
process), so the work happens in worker processes: `stellarisdashboard/import_worker.py`.

```
dashboard process                               worker processes (ProcessPoolExecutor)
  save monitor (cli.f_monitor_saves)  --path-->   parse the save (Rust parser)
  import page (bulk_import)                       back up the save, import it into the database
  ImportWorkerPool                    <-result-   ImportResult (dates, failures, timings)
  logging (stdout, log.txt)           <--logs--   QueueHandler
```

The live save monitor, the import page (bulk import and reprocessing) and the CLI batch commands
(`parse-saves`, `reprocess-failed-saves`) all submit save paths to an `ImportWorkerPool` and get
an `ImportResult` back. The parsed gamestate never leaves the worker.

## The pool

- `ImportWorkerPool(size).submit(save_file, game_id, replace_existing)` returns a
  `concurrent.futures.Future` of an `ImportResult`. A future can be cancelled until its job starts.
- **One game at a time per game.** The pool keeps a queue per game and gives only the head of each
  queue to the executor. So a game's saves are imported in order, one at a time, and two processes
  never write the same game's database at once. That is a correctness rule: in WAL mode a
  transaction that read and then writes fails at once if another process committed in between.
  Different games are imported in parallel by up to `size` processes.
- `get_import_worker()` is the dashboard's shared pool of `threads` processes (read when the pool is
  created), used by the save monitor and the import page. The CLI commands make a pool of
  `--threads` processes for the batch. With the default `threads: 1`, live saves and a bulk import
  of another game take turns, one save each.
- If a worker dies (a crash of the parser, the out-of-memory killer), the executor fails every job
  it had then; each of those keeps a copy of its save (`failed_saves`, stage `import`), which the
  import page can reprocess. The game's later saves go to a new executor.
- Workers exit when the dashboard process is gone, also if it was killed. `shutdown()` lets
  running jobs finish for up to a timeout, then terminates the workers; jobs that did not finish
  fail with `ImportWorkerError`.
- After each result the submitting process calls `import_worker.after_import(result)`, which clears
  the country color and available games caches. The plot data is updated by the next request.

## What does not cross the process boundary

- **Settings.** Each job carries a snapshot of `config.SETTINGS` plus `debug_mode`, so a change on
  the settings page reaches the workers with the next job.
- **Logging.** Workers drop the handlers of their own `config.initialize()` and send their records
  through a `multiprocessing` queue to the submitting process, which handles them with its own
  handlers. Workers never write `log.txt`.
- **The import page's error list** is built from the `ImportResult`s of its saves
  (`ImportResult.error_messages()`), not from log records.
- **Locks.** `datamodel._DB_LOCKS` and `timeline._process_gamestate_lock` are per process. Between
  processes SQLite's locking applies: write sessions begin with `BEGIN IMMEDIATE`, so a second
  writer (e.g. a CLI import while the dashboard imports the same game) waits for the busy timeout
  instead of failing. Readers stay deferred. Opening a new database from two processes at once is
  safe: the migration and the switch to WAL take the write lock and check again
  (`datamodel._setup_engine`, `_set_sqlite_pragmas`).
- **Coordination with the import page.** `bulk_import.defer_to_running_import` runs in the
  dashboard process; the monitor calls it before submitting, so a save of a game being imported is
  left to that import.

## Start methods and PyInstaller

The pool uses the platform's default start method (forkserver on Linux, spawn on Windows and macOS);
the tests also run it with spawn. `__main__.py` calls `multiprocessing.freeze_support()` before its
heavy imports, so in a frozen build a worker starts without loading the web server. The worker
module must not import dash, plotly, `cli` or `bulk_import`; a test checks that.
