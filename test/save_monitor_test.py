import concurrent.futures
import os
import pathlib
import threading
import time

import pytest

from stellarisdashboard import bulk_import, cli, config, import_worker
from stellarisdashboard.parsing import save_parser


class FakeWorker:
    """Stands in for the import worker: records the jobs, and the test resolves their futures."""

    def __init__(self, auto_complete=True):
        self.auto_complete = auto_complete
        self.jobs = []  # (save_file, game_id, future)
        self.started = False

    def start(self):
        self.started = True

    @property
    def num_waiting(self):
        return sum(not future.done() for _, _, future in self.jobs)

    def submit(self, save_file, game_id, replace_existing=False):
        future = concurrent.futures.Future()
        self.jobs.append((save_file, game_id, future))
        if self.auto_complete:
            self.complete(len(self.jobs) - 1)
        return future

    def complete(self, index):
        save_file, game_id, future = self.jobs[index]
        future.set_result(
            import_worker.ImportResult(
                game_id=game_id,
                save_file=save_file,
                game_date="2200.01.01",
                parsed=True,
                imported=True,
                timings={"parse": 1.0, "import": 2.0},
            )
        )


@pytest.fixture
def monitor_env(tmp_path, monkeypatch):
    """Runs f_monitor_saves in a thread against a fake worker."""
    saves = tmp_path / "saves"
    saves.mkdir()
    fake = FakeWorker()
    handled = []  # save names, in the order after_import saw the results
    monkeypatch.setattr(import_worker, "get_import_worker", lambda: fake)
    monkeypatch.setattr(import_worker, "shutdown_import_worker", lambda timeout=0: handled.append("shutdown"))
    monkeypatch.setattr(import_worker, "after_import", lambda result: handled.append(result.save_file.name))
    monkeypatch.setattr(bulk_import, "defer_to_running_import", lambda game_id: False)
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monkeypatch.setattr(cli, "POLLING_INTERVAL", 0.02)
    stop = threading.Event()
    thread = threading.Thread(target=cli.f_monitor_saves, kwargs=dict(save_path=saves, stop_event=stop))

    class Env:
        pass

    env = Env()
    env.saves, env.fake, env.handled, env.stop, env.thread = saves, fake, handled, stop, thread

    def add(game, name):
        path = saves / game / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"save")
        return path

    env.add = add

    def wait_for(condition, timeout=10):
        deadline = time.time() + timeout
        while not condition():
            assert time.time() < deadline, "timed out"
            time.sleep(0.01)

    env.wait_for = wait_for
    yield env
    stop.set()
    if thread.ident is not None:
        thread.join(10)
        assert not thread.is_alive()


def start_monitor(env):
    """Start the monitor and wait until it has noted the saves that exist at startup."""
    env.thread.start()
    env.wait_for(lambda: env.fake.started)  # starts the worker after that


def test_new_saves_are_all_submitted_in_order(monitor_env):
    env = monitor_env
    env.fake.auto_complete = False
    start_monitor(env)
    for i in range(5):
        env.add("game1", f"2200.01.0{i + 1}.sav")
    env.wait_for(lambda: len(env.fake.jobs) == 5)
    assert [job[0].name for job in env.fake.jobs] == [f"2200.01.0{i + 1}.sav" for i in range(5)]
    assert [job[1] for job in env.fake.jobs] == ["game1"] * 5
    for i in range(5):
        env.fake.complete(i)
    assert env.handled == [f"2200.01.0{i + 1}.sav" for i in range(5)]


def test_existing_saves_are_ignored(monitor_env):
    env = monitor_env
    env.add("game1", "old.sav")
    start_monitor(env)
    env.add("game1", "new.sav")
    env.wait_for(lambda: env.handled == ["new.sav"])
    assert [job[0].name for job in env.fake.jobs] == ["new.sav"]


def test_a_game_with_a_running_import_is_left_to_it(monitor_env, monkeypatch):
    env = monitor_env
    deferred = []

    def defer(game_id):
        if game_id == "game2":
            deferred.append(game_id)
            return True
        return False

    monkeypatch.setattr(bulk_import, "defer_to_running_import", defer)
    start_monitor(env)
    env.add("game1", "a.sav")
    env.add("game2", "b.sav")
    env.add("game1", "c.sav")
    env.wait_for(lambda: len(env.handled) == 2)
    assert [job[0].name for job in env.fake.jobs] == ["a.sav", "c.sav"]
    assert deferred == ["game2"]
    time.sleep(0.2)
    assert len(env.fake.jobs) == 2  # and not looked at again


def test_the_monitor_stops_the_worker(monitor_env):
    env = monitor_env
    start_monitor(env)
    env.stop.set()
    env.thread.join(10)
    assert env.handled[-1] == "shutdown"


def test_a_failing_job_does_not_stop_the_monitor(monitor_env):
    env = monitor_env
    env.fake.auto_complete = False
    start_monitor(env)
    env.add("game1", "a.sav")
    env.add("game1", "b.sav")
    env.wait_for(lambda: len(env.fake.jobs) == 2)
    env.fake.jobs[0][2].set_exception(import_worker.ImportWorkerError("shut down"))
    env.fake.complete(1)
    env.wait_for(lambda: env.handled == ["b.sav"])


def test_the_monitor_reports_every_new_save_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    assert monitor.get_new_savefiles() == []
    made = []
    for game, name in [("g1", "b.sav"), ("g1", "a.sav"), ("g2", "c.sav"), ("g1", "ironman.sav")]:
        path = tmp_path / game / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
        made.append(path)
    new = monitor.get_new_savefiles()
    assert new == sorted(made)  # all of them, however many: nothing is skipped
    assert monitor.get_new_savefiles() == []
    # ironman.sav is overwritten by the game and so is never marked as processed
    os.utime(tmp_path / "g1" / "ironman.sav", (time.time() + 5, time.time() + 5))
    assert monitor.get_new_savefiles() == [tmp_path / "g1" / "ironman.sav"]


def test_the_monitor_applies_skip_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 1)
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    for name in ("a.sav", "b.sav", "c.sav", "d.sav"):
        path = tmp_path / "g" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
    assert [p.name for p in monitor.get_new_savefiles()] == ["b.sav", "d.sav"]


# --- detection does not depend on wall-clock timestamps ---


def _write(path, data=b"x"):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_bytes(data)
    return path


def test_a_new_save_with_an_old_timestamp_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    assert monitor.get_new_savefiles() == []
    # appears right after a scan, but is stamped before it (coarse or preserved timestamps)
    path = _write(tmp_path / "g" / "a.sav")
    os.utime(path, (1_000, 1_000))
    assert monitor.get_new_savefiles() == [path]
    assert monitor.get_new_savefiles() == []


def test_a_save_renamed_into_place_during_a_scan_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    game = tmp_path / "g"
    _write(game / "first.sav")
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    part = _write(game / "second.part")
    real_glob = pathlib.Path.glob

    def glob_then_rename(self, pattern, *args, **kwargs):
        found = list(real_glob(self, pattern, *args, **kwargs))  # the directory is listed ...
        part.rename(game / "second.sav")  # ... and the save appears afterwards
        return iter(found)

    monkeypatch.setattr(pathlib.Path, "glob", glob_then_rename)
    assert monitor.get_new_savefiles() == []
    monkeypatch.undo()
    assert monitor.get_new_savefiles() == [game / "second.sav"]


def test_a_skipped_save_is_never_reported_later_and_counted_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 1)
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    a = _write(tmp_path / "g" / "a.sav")
    assert monitor.get_new_savefiles() == []  # first of every two: skipped
    assert monitor.get_new_savefiles() == []  # and not reconsidered
    assert monitor.num_encountered_saves == 1
    b = _write(tmp_path / "g" / "b.sav")
    assert monitor.get_new_savefiles() == [b]
    assert monitor.num_encountered_saves == 2
    assert monitor.get_new_savefiles() == []
    assert a in monitor.processed_saves


def test_a_skipped_save_is_counted_once_by_the_batch_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 1)
    for name in ("a.sav", "b.sav", "c.sav"):
        _write(tmp_path / "g" / name)
    monitor = save_parser.BatchSavePathMonitor(tmp_path)
    assert [p.name for p in monitor.get_new_savefiles()] == ["b.sav"]
    assert monitor.num_encountered_saves == 3
    assert monitor.get_new_savefiles() == []
    assert monitor.num_encountered_saves == 3


def test_an_ironman_save_is_reported_when_rewritten_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    iron = _write(tmp_path / "g" / "ironman.sav", b"one")
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    assert monitor.get_new_savefiles() == []  # not at startup
    assert monitor.get_new_savefiles() == []  # not while unchanged
    iron.write_bytes(b"longer content")  # new size
    assert monitor.get_new_savefiles() == [iron]
    assert monitor.get_new_savefiles() == []
    stat = iron.stat()
    os.utime(iron, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # same size, new mtime
    assert monitor.get_new_savefiles() == [iron]
    assert monitor.get_new_savefiles() == []


def test_an_ironman_save_that_disappears_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    iron = _write(tmp_path / "g" / "ironman.sav", b"one")
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    iron.write_bytes(b"two!")
    real_stat = pathlib.Path.stat

    def failing_stat(self, *args, **kwargs):
        if self.name == "ironman.sav":
            raise FileNotFoundError(str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", failing_stat)
    assert monitor.get_new_savefiles() == []
    monkeypatch.undo()
    assert monitor.get_new_savefiles() == [iron]


def test_only_unprocessed_non_ironman_saves_are_stat_ed(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    for i in range(5):
        _write(tmp_path / "g" / f"{i}.sav")
    monitor = save_parser.SavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    stat_calls = []
    real_stat = pathlib.Path.stat

    def counting_stat(self, *args, **kwargs):
        stat_calls.append(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", counting_stat)
    assert monitor.get_new_savefiles() == []
    assert [p for p in stat_calls if p.suffix == ".sav"] == []


def test_the_game_name_prefix_is_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monitor = save_parser.SavePathMonitor(tmp_path, game_name_prefix="unit")
    monitor.mark_all_existing_saves_processed()
    _write(tmp_path / "other" / "a.sav")
    wanted = _write(tmp_path / "united_1" / "a.sav")
    assert monitor.get_new_savefiles() == [wanted]
