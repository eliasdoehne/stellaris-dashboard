import concurrent.futures
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
import zipfile

import pytest

import stellarisdashboard
from stellarisdashboard import config, country_color_cache, datamodel, import_worker
from stellarisdashboard.parsing import save_copies

START_METHODS = [None, "spawn"]  # None: the platform's default
needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
needs_posix_signals = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")

# a save that the Rust parser accepts and that imports with a failed data processor
TINY_GAMESTATE = """\
date="{date}"
galaxy={ template="x" }
country={ 0={ name={ key="A" } } }
player={ { name="p" country=0 } }
"""


def make_save(folder: pathlib.Path, name: str, game_id="game1", gamestate=TINY_GAMESTATE, date="2200.01.05"):
    gamestate = gamestate.replace("{date}", date)
    path = folder / game_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta", f'version="Test"\ndate="{date}"\nname="x"\n')
        z.writestr("gamestate", gamestate)
    return path


@pytest.fixture
def output(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path / "out")
    monkeypatch.setattr(config.CONFIG, "keep_failed_saves", True)
    monkeypatch.setattr(config.CONFIG, "backup_saves", False)
    monkeypatch.setattr(config.CONFIG, "failed_saves_max_size_mb", 1000)
    monkeypatch.setattr(config.CONFIG, "debug_mode", False)
    return tmp_path / "out"


@pytest.fixture(scope="module", params=START_METHODS, ids=lambda m: m or "default")
def worker(request):
    w = import_worker.ImportWorker(request.param)
    yield w
    w.shutdown()
    assert not w._worker


def test_module_does_not_import_the_dashboard_app():
    code = (
        "import sys, stellarisdashboard.import_worker\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('dash', 'plotly', 'flask', 'waitress')"
        " or m.startswith('stellarisdashboard.dashboard_app') or m in ('stellarisdashboard.cli', 'stellarisdashboard.bulk_import'))\n"
        "assert not bad, bad\n"
    )
    root = str(pathlib.Path(stellarisdashboard.__file__).parent.parent)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH")])))
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]


def test_settings_snapshot_has_every_setting_and_the_debug_mode():
    snapshot = import_worker.settings_snapshot()
    assert set(snapshot) == set(config.SETTINGS) | {"debug_mode"}


def test_parse_failure_is_reported_and_kept(worker, output, tmp_path):
    bad = tmp_path / "saves" / "game1" / "bad.sav"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a save")
    result = worker.submit(bad, "game1").result(60)
    assert isinstance(result, import_worker.ImportResult)
    assert (result.save_file, result.game_id) == (bad, "game1")
    assert not result.parsed and not result.imported and result.error is None
    assert [f["stage"] for f in result.failures] == [save_copies.STAGE_PARSE]
    assert "ValueError" in result.failures[0]["error"]
    kept = output / "failed_saves" / "game1" / "bad.sav"
    assert kept.read_bytes() == b"not a save"
    assert json.loads(kept.with_suffix(".json").read_text())["failures"][0]["stage"] == "parse"


def test_import_of_a_tiny_save(worker, output, tmp_path):
    save = make_save(tmp_path / "saves", "tiny.sav")
    result = worker.submit(save, "game1").result(60)
    assert result.error is None
    assert result.parsed and result.imported
    assert result.game_date == "2200.01.05"
    # defined outcome: the tiny gamestate lacks the data of one processor, which is isolated
    assert [(f["stage"], f.get("processor")) for f in result.failures] == [("processor", "systems")]
    assert set(result.timings) == {"parse", "import"}
    assert (output / "db" / "game1.db").exists()
    assert (output / "failed_saves" / "game1" / "tiny.sav").exists()
    # the same date again is skipped without a new gamestate
    again = worker.submit(save, "game1").result(60)
    assert again.parsed and not again.imported


def test_settings_of_each_job_reach_the_worker(worker, output, tmp_path, monkeypatch):
    save = make_save(tmp_path / "saves", "tiny.sav")
    worker.submit(save, "game1").result(60)
    assert not (output / "save_backups").exists()
    monkeypatch.setattr(config.CONFIG, "backup_saves", True)
    worker.submit(save, "game1").result(60)
    assert (output / "save_backups" / "game1" / "tiny.sav").exists()
    monkeypatch.setattr(config.CONFIG, "keep_failed_saves", False)
    other = make_save(tmp_path / "saves", "tiny2.sav", date="2200.01.06")
    worker.submit(other, "game1").result(60)
    assert not (output / "failed_saves" / "game1" / "tiny2.sav").exists()


def test_log_records_of_the_worker_reach_the_parent(worker, output, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    bad = tmp_path / "saves" / "game1" / "bad.sav"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a save")
    worker.submit(bad, "game1", log_tag="test-client").result(60)
    records = [r for r in caplog.records if "Error while reading save file" in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.name == "stellarisdashboard.import_worker"
    assert record.import_tag == "test-client"
    assert record.processName == "import-worker"
    assert record.getMessage() == f"Error while reading save file {bad}:"  # without the traceback
    assert "Traceback" in record.exc_text and "ValueError" in record.exc_text
    assert record.exc_type_name == "ValueError"
    assert "Failed to read" in record.exc_message
    assert any(r.import_tag == "test-client" for r in caplog.records if hasattr(r, "import_tag"))
    # formatting a forwarded record adds the traceback, as for a local one
    assert "ValueError" in logging.Formatter("%(message)s").format(record)


def test_the_worker_never_writes_the_log_file(output, tmp_path, monkeypatch):
    # with log_to_file the worker's own config.initialize() would open log.txt
    monkeypatch.setattr(config.CONFIG, "log_to_file", True)
    w = import_worker.ImportWorker("spawn")
    try:
        bad = tmp_path / "saves" / "game1" / "bad.sav"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not a save")
        w.submit(bad, "game1").result(60)
    finally:
        w.shutdown()
    assert not (output / "log.txt").exists()


def test_jobs_run_in_submission_order(worker, output, tmp_path):
    saves = [make_save(tmp_path / "saves", f"s{i}.sav", date=f"2200.01.{10 + i:02d}") for i in range(4)]
    futures = [worker.submit(s, "game1") for s in saves]
    done_order = []
    for f in futures:
        f.add_done_callback(lambda f: done_order.append(f.result().save_file.name))
    results = [f.result(60) for f in futures]
    assert [r.save_file for r in results] == saves
    assert [r.job_id for r in results] == sorted(r.job_id for r in results)
    assert all(r.imported for r in results)
    deadline = time.time() + 5
    while len(done_order) < 4 and time.time() < deadline:
        time.sleep(0.01)
    assert done_order == [s.name for s in saves]


@needs_fifo
def test_a_dying_worker_fails_its_job_and_the_queued_jobs_still_run(output, tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "keep_failed_saves", False)  # copying the pipe would block
    w = import_worker.ImportWorker()
    try:
        saves_dir = tmp_path / "saves"
        hanging = saves_dir / "game1" / "hang.sav"
        hanging.parent.mkdir(parents=True)
        os.mkfifo(hanging)  # opening it blocks: the worker is stuck in the job until it is killed
        good = [make_save(saves_dir, f"s{i}.sav", date=f"2200.01.{10 + i:02d}") for i in range(2)]
        futures = [w.submit(hanging, "game1")] + [w.submit(s, "game1") for s in good]
        deadline = time.time() + 60
        while not (w._current and w._current.started):
            assert time.time() < deadline, "the job did not start"
            time.sleep(0.01)
        first_process = w._worker.process
        first_process.kill()
        first = futures[0].result(60)
        assert first.error and "exited with code" in first.error
        assert not first.parsed and not first.imported
        later = [f.result(60) for f in futures[1:]]
        assert [r.save_file for r in later] == good
        assert all(r.imported and r.error is None for r in later)
        assert w._worker.process is not first_process and w._worker.process.is_alive()
    finally:
        w.shutdown(timeout=2)


def test_a_job_lost_with_its_worker_keeps_a_copy_of_the_save(output, tmp_path):
    class DeadWorker:
        def close_pipes(self):
            pass

    w = import_worker.ImportWorker()
    save = make_save(tmp_path / "saves", "crash.sav")
    request = import_worker.ImportRequest(0, save, "game1", False, {}, "monitor")
    job = import_worker._Job(request, concurrent.futures.Future(), started=True, attempts=1)
    dead = DeadWorker()
    w._worker, w._current = dead, job
    w._on_worker_exit(dead, -9)
    result = job.future.result(1)
    assert "the import worker exited with code -9" in result.error
    record = json.loads((output / "failed_saves" / "game1" / "crash.json").read_text())
    assert record["failures"][0]["stage"] == save_copies.STAGE_IMPORT
    assert "exited with code -9" in record["failures"][0]["error"]
    assert w._worker is None  # a new one is started with the next job


@needs_fifo
def test_shutdown_terminates_a_stuck_worker_and_fails_waiting_jobs(output, tmp_path):
    w = import_worker.ImportWorker()
    hanging = tmp_path / "saves" / "game1" / "hang.sav"
    hanging.parent.mkdir(parents=True)
    os.mkfifo(hanging)
    first = w.submit(hanging, "game1")
    second = w.submit(make_save(tmp_path / "saves", "s.sav"), "game1")
    deadline = time.time() + 60
    while not (w._current and w._current.started):
        assert time.time() < deadline
        time.sleep(0.01)
    process = w._worker.process
    w.shutdown(timeout=0.5)
    assert not process.is_alive()
    for future in (first, second):
        with pytest.raises(import_worker.ImportWorkerError):
            future.result(5)
    with pytest.raises(import_worker.ImportWorkerError):
        w.submit(hanging, "game1")


def test_shutdown_of_an_idle_worker_is_quick_and_repeatable(output):
    w = import_worker.ImportWorker()
    w.start()
    process = w._worker.process
    t = time.time()
    w.shutdown()
    w.shutdown()
    assert time.time() - t < 5
    assert not process.is_alive()


@needs_posix_signals
def test_the_worker_exits_when_the_dashboard_is_killed(tmp_path):
    code = (
        "import sys, time\n"
        "from stellarisdashboard import import_worker\n"
        "if __name__ == '__main__':\n"
        "    w = import_worker.ImportWorker()\n"
        "    w.start()\n"
        "    print('WORKER-PID', w._worker.process.pid, flush=True)\n"
        "    time.sleep(600)\n"
    )
    root = str(pathlib.Path(stellarisdashboard.__file__).parent.parent)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH")])))
    parent = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
    try:
        line = ""
        while not line.startswith("WORKER-PID"):  # after the log lines
            line = parent.stdout.readline()
            assert line, "the dashboard process ended"
        pid = int(line.split()[1])
        os.kill(pid, 0)  # alive
        parent.kill()
        parent.wait()
        deadline = time.time() + 15
        while True:
            try:
                os.kill(pid, 0)
                # a zombie of a process whose parent is gone is reaped by init; wait for that
            except ProcessLookupError:
                break
            assert time.time() < deadline, "the worker is still running"
            time.sleep(0.2)
    finally:
        parent.kill()
        parent.wait()


def test_after_import_clears_the_caches(monkeypatch):
    country_color_cache.GAME_COUNTRY_COLORS["g"] = object()
    monkeypatch.setattr(datamodel, "_available_games_cache", (time.time(), {"g": {}}))
    result = import_worker.ImportResult(job_id=1, game_id="g", save_file=pathlib.Path("x.sav"))
    import_worker.after_import(result)
    assert "g" not in country_color_cache.GAME_COUNTRY_COLORS
    assert datamodel._available_games_cache[1] == {}
    assert time.time() - datamodel._available_games_cache[0] > datamodel._AVAILABLE_GAMES_CACHE_TTL_SECONDS


def test_import_gamestate_alias_in_cli():
    from stellarisdashboard import cli

    assert cli.f_import_gamestate is import_worker.import_gamestate


def test_opening_a_new_database_in_two_processes_at_once(tmp_path):
    """The dashboard's warm-up and the import worker can both migrate a database that is new to them."""
    code = (
        "import sys, time, pathlib\n"
        "from stellarisdashboard import config, datamodel\n"
        "config.CONFIG.base_output_path = pathlib.Path(sys.argv[1])\n"
        "start = pathlib.Path(sys.argv[2])\n"
        "while not start.exists():\n"
        "    time.sleep(0.001)\n"
        "datamodel._get_or_create_sessionmaker('g')\n"
        "with datamodel.get_db_session('g') as s:\n"
        "    s.query(datamodel.Game).count()\n"
    )
    root = str(pathlib.Path(stellarisdashboard.__file__).parent.parent)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH")])))
    start = tmp_path / "start"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path / "out"), str(start)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    time.sleep(3)  # all three have imported the modules and wait for the signal
    start.write_text("")
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err[-3000:]
