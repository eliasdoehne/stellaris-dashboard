import threading

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
def inits(monkeypatch):
    """Fake database with gamestates 10, 20, 30; returns the perspectives of the initializations."""
    dates = [10, 20, 30]
    spec = vd.PlotSpecification(
        plot_id="fake",
        title="fake",
        style=vd.PlotStyle.line,
        data_container_factory=FakeContainer,
    )
    monkeypatch.setattr(
        datamodel,
        "count_gamestates_since",
        lambda game, since: len([d for d in dates if d > since]),
    )
    monkeypatch.setattr(
        datamodel,
        "get_gamestates_since",
        lambda game, since: (FakeGamestate(d) for d in dates if d > since),
    )
    monkeypatch.setattr(
        vd, "get_plot_specifications_for_tab_layout", lambda: {"t": [spec]}
    )
    monkeypatch.setattr(vd, "get_market_graphs", lambda resources: [])
    monkeypatch.setattr(datamodel, "get_db_session", lambda name: FakeSession())
    monkeypatch.setattr(vd, "_CURRENT_EXECUTION_PLOT_DATA", vd.LRUDict(3))
    calls = []
    original_init = vd.PlotDataManager.initialize

    def counting_initialize(self):
        calls.append(self.country_perspective)
        original_init(self)

    monkeypatch.setattr(vd.PlotDataManager, "initialize", counting_initialize)
    return calls


def test_perspective_switch_is_applied(inits):
    with vd.plot_data("g", 5) as m:
        assert m.data_containers_by_plot_id["fake"].loaded == [10, 20, 30]
    with vd.plot_data("g", None) as m2:
        assert m2 is m
        assert m.country_perspective is None
        container = m.data_containers_by_plot_id["fake"]
        assert container.perspective is None and container.loaded == [10, 20, 30]
    assert inits == [5, None]


def test_concurrent_calls_for_a_game_are_serialized(inits):
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
    t2.join(0.2)
    assert t2.is_alive()  # waits for the reader
    release.set()
    t1.join(5)
    t2.join(5)
    assert seen == [1, "switched"]
    assert inits == [1, 2]


def test_drop_cached_plot_data(inits):
    with vd.plot_data("g", 1) as m:
        pass
    vd.drop_cached_plot_data("g")
    with vd.plot_data("g", 1) as m2:
        assert m2 is not m
    assert inits == [1, 1]


def test_failing_initialize_leaves_nothing_cached(inits, monkeypatch):
    def fail(self):
        raise RuntimeError("broken database")

    with monkeypatch.context() as patch:
        patch.setattr(vd.PlotDataManager, "initialize", fail)
        with pytest.raises(RuntimeError):
            with vd.plot_data("g", 1):
                pass
    assert "g" not in vd._CURRENT_EXECUTION_PLOT_DATA
    with vd.plot_data("g", 1) as m:
        assert m.data_containers_by_plot_id["fake"].loaded == [10, 20, 30]
