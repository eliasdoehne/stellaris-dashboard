import threading
import time

from stellarisdashboard import datamodel, game_info
from stellarisdashboard.dashboard_app import warm_up


def _run_concurrently(fn, n=8):
    barrier = threading.Barrier(n)
    results = []

    def target():
        barrier.wait()
        results.append(fn())

    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_global_renderer_loaded_once_under_concurrency(monkeypatch):
    loads = []

    class SlowRenderer:
        def __init__(self, localization_files):
            self.loaded = False

        def load_name_mapping(self):
            loads.append(1)
            time.sleep(0.1)
            self.loaded = True

    monkeypatch.setattr(game_info, "NameRenderer", SlowRenderer)
    monkeypatch.setattr(game_info, "global_renderer", None)

    renderers = _run_concurrently(game_info.get_global_renderer)

    assert len(loads) == 1
    assert all(r is renderers[0] and r.loaded for r in renderers)


def test_engine_setup_runs_once_under_concurrency(monkeypatch):
    setups = []

    def slow_setup(game_id):
        setups.append(game_id)
        time.sleep(0.1)
        datamodel._SESSIONMAKERS[game_id] = object()

    monkeypatch.setattr(datamodel, "_setup_engine", slow_setup)
    monkeypatch.setattr(datamodel, "_SESSIONMAKERS", {})

    results = _run_concurrently(lambda: datamodel._get_or_create_sessionmaker("g"))

    assert setups == ["g"]
    assert len(set(map(id, results))) == 1


def test_warm_up_swallows_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(game_info, "get_global_renderer", fail)
    monkeypatch.setattr(datamodel, "warm_up_databases", fail)

    warm_up()  # must log and return, not raise
