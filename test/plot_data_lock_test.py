import threading
import time

import pytest

from stellarisdashboard import datamodel
from stellarisdashboard.dashboard_app import visualization_data as vd


class FakeContainer:
    def __init__(self, perspective, **kwargs):
        self.perspective = perspective
        self.loaded = []

    def extract_data_from_gamestate(self, gs):
        self.loaded.append(gs.date)


class FakeGamestate:
    def __init__(self, date):
        self.date = date


class FakeDb:
    """Stands in for the database: gamestates are loaded by date, slowly if asked."""

    def __init__(self, dates, delay=0.0):
        self.dates = list(dates)
        self.delay = delay
        self.served = []
        self.inits = []

    def count(self, game_name, since):
        return len([d for d in self.dates if d > since])

    def get(self, game_name, since):
        for d in [d for d in self.dates if d > since]:
            time.sleep(self.delay)
            self.served.append(d)
            yield FakeGamestate(d)


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, *a):
        return self

    def filter_by(self, **kw):
        return self

    def first(self):
        return object()


@pytest.fixture
def fake_env(monkeypatch):
    db = FakeDb([10, 20, 30])
    spec = vd.PlotSpecification(
        plot_id="fake",
        title="fake",
        style=vd.PlotStyle.line,
        data_container_factory=FakeContainer,
    )

    monkeypatch.setattr(datamodel, "count_gamestates_since", db.count)
    monkeypatch.setattr(datamodel, "get_gamestates_since", db.get)
    monkeypatch.setattr(
        vd, "get_plot_specifications_for_tab_layout", lambda: {"t": [spec]}
    )
    monkeypatch.setattr(vd, "get_market_graphs", lambda resources: [])
    monkeypatch.setattr(datamodel, "get_db_session", lambda name: FakeSession())
    original_init = vd.PlotDataManager.initialize

    def counting_initialize(self):
        db.inits.append(self.country_perspective)
        time.sleep(db.delay)
        original_init(self)

    monkeypatch.setattr(vd.PlotDataManager, "initialize", counting_initialize)
    monkeypatch.setattr(vd, "_CURRENT_EXECUTION_PLOT_DATA", vd.LRUDict(3))
    return db


def test_perspective_is_kept_without_argument(fake_env):
    m = vd.get_current_execution_plot_data("g", 5)
    assert m.country_perspective == 5
    inits = len(fake_env.inits)
    m2 = vd.get_current_execution_plot_data("g")
    assert m2 is m
    assert m.country_perspective == 5
    assert len(fake_env.inits) == inits


def test_explicit_none_switches_to_observer(fake_env):
    m = vd.get_current_execution_plot_data("g", 5)
    vd.get_current_execution_plot_data("g", None)
    assert m.country_perspective is None
    assert m.data_containers_by_plot_id["fake"].perspective is None
    assert m.last_date == 30


def test_new_gamestate_loaded_incrementally(fake_env):
    m = vd.get_current_execution_plot_data("g", 5)
    assert m.data_containers_by_plot_id["fake"].loaded == [10, 20, 30]
    inits = len(fake_env.inits)
    fake_env.dates.append(40)
    assert vd.get_current_execution_plot_data("g") is m
    assert len(fake_env.inits) == inits
    assert m.data_containers_by_plot_id["fake"].loaded == [10, 20, 30, 40]


def test_concurrent_callers_build_one_manager_and_load_once(fake_env):
    fake_env.delay = 0.05
    results = []

    def worker():
        with vd.plot_data("g", 7) as m:
            results.append(m)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4
    assert all(m is results[0] for m in results)
    assert len(fake_env.inits) == 1
    assert fake_env.served == [10, 20, 30]
    assert results[0].data_containers_by_plot_id["fake"].loaded == [10, 20, 30]


def test_readers_block_perspective_switch(fake_env):
    entered = threading.Event()
    release = threading.Event()
    seen = []

    def reader():
        with vd.plot_data("g", 1) as m:
            entered.set()
            release.wait(5)
            seen.append(m.country_perspective)

    def switcher():
        with vd.plot_data("g", 2):
            seen.append("switched")

    t1 = threading.Thread(target=reader)
    t1.start()
    assert entered.wait(5)
    t2 = threading.Thread(target=switcher)
    t2.start()
    time.sleep(0.2)
    assert seen == []  # the switcher still waits for the reader's lock
    release.set()
    t1.join(5)
    t2.join(5)
    assert seen == [1, "switched"]


def test_registry_is_free_while_a_game_loads(fake_env):
    fake_env.delay = 0.3
    t = threading.Thread(target=lambda: vd.get_current_execution_plot_data("slow", 1))
    t.start()
    time.sleep(0.1)
    start = time.time()
    with vd._REGISTRY_LOCK:
        pass
    assert time.time() - start < 0.1
    t.join()


def test_drop_while_held_keeps_manager_usable(fake_env):
    with vd.plot_data("g", 1) as m:
        vd.drop_cached_plot_data("g")
        assert m.country_perspective == 1
    m2 = vd.get_current_execution_plot_data("g")
    assert m2 is not m
