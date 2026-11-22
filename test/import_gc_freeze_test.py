import contextlib
import gc
import weakref

import pytest

from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline


@pytest.fixture(autouse=True)
def no_frozen_objects():
    gc.unfreeze()
    yield
    gc.unfreeze()


def test_objects_are_frozen_during_the_import_only():
    with timeline._gc_frozen():
        assert gc.get_freeze_count() > 0
    assert gc.get_freeze_count() == 0


def test_unfrozen_after_an_exception():
    with pytest.raises(ValueError):
        with timeline._gc_frozen():
            raise ValueError
    assert gc.get_freeze_count() == 0


def test_repeated_imports_do_not_accumulate_frozen_objects():
    for _ in range(3):
        with timeline._gc_frozen():
            pass
        assert gc.get_freeze_count() == 0


def test_an_existing_freeze_is_left_alone():
    gc.freeze()
    frozen = gc.get_freeze_count()
    with timeline._gc_frozen():
        pass
    assert gc.get_freeze_count() == frozen


def test_process_gamestate_unfreezes_when_the_import_fails(monkeypatch):
    frozen_during_import = []

    def failing_get_db_session(game_id, write=False):
        @contextlib.contextmanager
        def cm():
            frozen_during_import.append(gc.get_freeze_count() > 0)
            raise RuntimeError("database unavailable")
            yield

        return cm()

    tle = timeline.TimelineExtractor()
    monkeypatch.setattr(tle, "_read_basic_game_info", lambda game_id: None)
    monkeypatch.setattr(
        tle, "basic_info", type("B", (), {"logger_str": ""})(), raising=False
    )
    monkeypatch.setattr(datamodel, "get_db_session", failing_get_db_session)
    with pytest.raises(RuntimeError):
        tle.process_gamestate("gameA", {})
    assert frozen_during_import == [True]
    assert gc.get_freeze_count() == 0


def test_garbage_of_the_import_is_collected_before_unfreezing():
    """Otherwise the next import freezes it again, and memory grows with every save."""

    class Node:
        pass

    enabled = gc.isenabled()
    gc.disable()  # only the explicit collection may free the cycle
    try:
        with timeline._gc_frozen():
            node = Node()
            node.self = node
            ref = weakref.ref(node)
            del node
        assert ref() is None
    finally:
        if enabled:
            gc.enable()
