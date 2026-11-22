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
        self.jobs = []  # (save_file, game_id, log_tag, future)
        self.started = False
        self.lock = threading.Lock()

    def start(self):
        self.started = True

    def submit(self, save_file, game_id, replace_existing=False, log_tag="monitor"):
        future = concurrent.futures.Future()
        with self.lock:
            self.jobs.append((save_file, game_id, log_tag, future))
        if self.auto_complete:
            self.complete(len(self.jobs) - 1)
        return future

    def complete(self, index):
        save_file, game_id, _, future = self.jobs[index]
        future.set_result(
            import_worker.ImportResult(
                job_id=index,
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
    env.counter = 0

    def add(game, name):
        # a distinct, increasing modification time, as written by the game
        path = saves / game / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"save")
        env.counter += 1
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
    """Start the monitor. It ignores the saves that exist at startup; wait until it has looked."""
    env.thread.start()
    env.wait_for(lambda: env.fake.started)
    time.sleep(0.1)


def test_new_saves_are_all_submitted_in_order(monitor_env):
    env = monitor_env
    start_monitor(env)
    for i in range(5):
        env.add("game1", f"2200.01.0{i + 1}.sav")
    env.wait_for(lambda: len(env.handled) == 5)
    assert [job[0].name for job in env.fake.jobs] == [f"2200.01.0{i + 1}.sav" for i in range(5)]
    assert [job[1] for job in env.fake.jobs] == ["game1"] * 5
    assert {job[2] for job in env.fake.jobs} == {"monitor"}
    assert env.handled == [f"2200.01.0{i + 1}.sav" for i in range(5)]


def test_existing_saves_are_ignored(monitor_env):
    env = monitor_env
    env.add("game1", "old.sav")
    start_monitor(env)
    env.add("game1", "new.sav")
    env.wait_for(lambda: env.handled == ["new.sav"])
    assert [job[0].name for job in env.fake.jobs] == ["new.sav"]


def test_results_are_handled_in_order_even_if_completed_out_of_order(monitor_env):
    env = monitor_env
    env.fake.auto_complete = False
    start_monitor(env)
    for name in ("a.sav", "b.sav", "c.sav"):
        env.add("game1", name)
    env.wait_for(lambda: len(env.fake.jobs) == 3)
    env.fake.complete(2)
    env.fake.complete(1)
    time.sleep(0.2)
    assert env.handled == []  # the oldest save is still being imported
    env.fake.complete(0)
    env.wait_for(lambda: len(env.handled) == 3)
    assert env.handled == ["a.sav", "b.sav", "c.sav"]


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


def test_the_plot_data_is_not_updated(monitor_env, monkeypatch):
    from stellarisdashboard.dashboard_app import visualization_data

    calls = []
    monkeypatch.setattr(
        visualization_data, "get_current_execution_plot_data", lambda *a, **k: calls.append(a)
    )
    env = monitor_env
    start_monitor(env)
    env.add("game1", "a.sav")
    env.wait_for(lambda: env.handled == ["a.sav"])
    assert calls == []


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
    env.fake.jobs[0][3].set_exception(import_worker.ImportWorkerError("shut down"))
    env.fake.complete(1)
    env.wait_for(lambda: env.handled == ["b.sav"])


def test_the_continuous_monitor_reports_every_new_save_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monitor = save_parser.ContinuousSavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    assert monitor.get_new_saves() == []
    time.sleep(0.01)
    made = []
    for game, name in [("g1", "b.sav"), ("g1", "a.sav"), ("g2", "c.sav"), ("g1", "ironman.sav")]:
        path = tmp_path / game / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
        made.append(path)
    new = monitor.get_new_saves()
    assert new == sorted(made)  # all of them, however many: nothing is skipped
    assert monitor.get_new_saves() == []
    # ironman.sav is overwritten by the game and so is never marked as processed
    os.utime(tmp_path / "g1" / "ironman.sav", (time.time() + 5, time.time() + 5))
    assert monitor.get_new_saves() == [tmp_path / "g1" / "ironman.sav"]


def test_the_continuous_monitor_applies_skip_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "skip_saves", 1)
    monitor = save_parser.ContinuousSavePathMonitor(tmp_path)
    monitor.mark_all_existing_saves_processed()
    time.sleep(0.01)
    for name in ("a.sav", "b.sav", "c.sav", "d.sav"):
        path = tmp_path / "g" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
    assert [p.name for p in monitor.get_new_saves()] == ["b.sav", "d.sav"]
