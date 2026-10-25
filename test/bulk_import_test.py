import contextlib
import json
import logging
import os
import threading
import zipfile

import pytest

from stellarisdashboard import bulk_import, cli, config, datamodel
from stellarisdashboard.dashboard_app import visualization_data
from stellarisdashboard.parsing import save_copies, save_parser, timeline


def _meta(date, name="Earth Custodianship"):
    return f'version="Cygnus v4.5.2"\nname="{name}"\ndate="{date}"\n'


def make_save(folder, file_name, date=None, name="Earth Custodianship"):
    """Create a tiny fake .sav; the meta member holds the date unless date is None."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    with zipfile.ZipFile(path, "w") as zf:
        if date is not None:
            zf.writestr("meta", _meta(date, name))
        zf.writestr("gamestate", "")
    return path


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path, tmp_path_factory):
    bulk_import._save_info_cache.clear()
    monkeypatch.setattr(bulk_import, "_current_job", None)
    monkeypatch.setattr(bulk_import, "_imported_dates", lambda game_id: set())
    monkeypatch.setattr(config.CONFIG, "save_file_path", tmp_path)
    monkeypatch.setattr(config.CONFIG, "skip_saves", 0)
    monkeypatch.setattr(config.CONFIG, "save_name_filter", "")
    monkeypatch.setattr(config.CONFIG, "threads", 1)
    # outside the save folder, so kept copies of failed saves are not picked up as saves
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path_factory.mktemp("output"))
    monkeypatch.setattr(config.CONFIG, "keep_failed_saves", True)
    monkeypatch.setattr(config.CONFIG, "backup_saves", False)
    yield
    bulk_import._save_info_cache.clear()


# --- read_save_info ---


def test_read_save_info_from_meta(tmp_path):
    path = make_save(tmp_path / "gameA", "autosave.sav", "2200.03.01")
    info = bulk_import.read_save_info(path)
    assert info.date == datamodel.date_to_days("2200.03.01")
    assert info.empire_name == "Earth Custodianship"
    assert info.date_str == "2200.03.01"


def test_read_save_info_falls_back_to_filename_without_meta(tmp_path):
    path = make_save(tmp_path / "gameA", "autosave_2201.02.01.sav", None)
    info = bulk_import.read_save_info(path)
    assert info.date == datamodel.date_to_days("2201.02.01")
    assert info.empire_name == ""


def test_read_save_info_falls_back_to_filename_for_non_zip(tmp_path):
    folder = tmp_path / "gameA"
    folder.mkdir()
    path = folder / "autosave_2201.02.01.sav"
    path.write_bytes(b"garbage")
    assert bulk_import.read_save_info(path).date == datamodel.date_to_days("2201.02.01")


def test_read_save_info_unknown_date(tmp_path):
    folder = tmp_path / "gameA"
    folder.mkdir()
    path = folder / "mysave.sav"
    path.write_bytes(b"garbage")
    info = bulk_import.read_save_info(path)
    assert info.date is None
    assert info.date_str == "?"


def test_read_save_info_cache_invalidated_when_file_changes(tmp_path):
    path = make_save(tmp_path / "gameA", "a.sav", "2200.03.01")
    first = bulk_import.read_save_info(path)
    assert bulk_import.read_save_info(path) is first
    make_save(tmp_path / "gameA", "a.sav", "2200.05.01", name="Other Empire")
    second = bulk_import.read_save_info(path)
    assert second is not first
    assert second.date == datamodel.date_to_days("2200.05.01")
    assert second.empire_name == "Other Empire"


# --- get_import_plan ---


def _dates(saves):
    return [s.date_str for s in saves]


def test_plan_orders_chronologically_and_excludes_imported(tmp_path, monkeypatch):
    game = tmp_path / "gameA"
    make_save(game, "c.sav", "2200.03.01")
    make_save(game, "a.sav", "2200.01.01")
    make_save(game, "b.sav", "2200.02.01")
    make_save(game, "d.sav", "2200.04.01")
    monkeypatch.setattr(
        bulk_import,
        "_imported_dates",
        lambda g: {datamodel.date_to_days("2200.01.01"), datamodel.date_to_days("2200.03.01")},
    )
    plan = bulk_import.get_import_plan("gameA", tmp_path)
    assert _dates(plan.saves) == ["2200.01.01", "2200.02.01", "2200.03.01", "2200.04.01"]
    assert _dates(plan.to_import) == ["2200.02.01", "2200.04.01"]
    assert plan.num_saves == 4
    assert plan.num_imported == 2
    assert plan.num_filtered_out == 0
    assert plan.empire_name == "Earth Custodianship"
    assert plan.newest_imported_date == datamodel.date_to_days("2200.03.01")
    assert plan.newest_imported_date_str == "2200.03.01"
    # 2200.02.01 is older than the newest imported save
    assert plan.num_older_than_imported == 1


def test_plan_without_imported_saves(tmp_path):
    make_save(tmp_path / "gameA", "a.sav", "2200.01.01")
    plan = bulk_import.get_import_plan("gameA", tmp_path)
    assert plan.num_older_than_imported == 0
    assert plan.newest_imported_date is None
    assert plan.newest_imported_date_str == ""


def test_plan_plans_duplicate_dates_once(tmp_path):
    game = tmp_path / "gameA"
    make_save(game, "a.sav", "2200.01.01")
    make_save(game, "b.sav", "2200.01.01")
    make_save(game, "c.sav", "2200.02.01")
    plan = bulk_import.get_import_plan("gameA", tmp_path)
    assert plan.num_saves == 3
    assert _dates(plan.to_import) == ["2200.01.01", "2200.02.01"]
    assert plan.to_import[0].path.name == "a.sav"


def test_plan_exclude(tmp_path):
    game = tmp_path / "gameA"
    a = make_save(game, "a.sav", "2200.01.01")
    make_save(game, "b.sav", "2200.02.01")
    plan = bulk_import.get_import_plan("gameA", tmp_path, exclude={a})
    assert [s.path.name for s in plan.saves] == ["b.sav"]
    assert [s.path.name for s in plan.to_import] == ["b.sav"]


def test_plan_refused_without_saves(tmp_path):
    (tmp_path / "gameA").mkdir()
    with pytest.raises(bulk_import.ImportRefused):
        bulk_import.get_import_plan("gameA", tmp_path)
    make_save(tmp_path / "gameB", "a.sav", "2200.01.01")
    with pytest.raises(bulk_import.ImportRefused):
        bulk_import.get_import_plan("gameA", tmp_path)


def test_plan_everything_excluded_is_empty(tmp_path):
    a = make_save(tmp_path / "gameA", "a.sav", "2200.01.01")
    plan = bulk_import.get_import_plan("gameA", tmp_path, exclude={a})
    assert plan.saves == [] and plan.to_import == []


def test_plan_skip_saves(tmp_path, monkeypatch):
    game = tmp_path / "gameA"
    for month in range(1, 5):
        make_save(game, f"s{month}.sav", f"2200.0{month}.01")
    monkeypatch.setattr(config.CONFIG, "skip_saves", 1)
    plan = bulk_import.get_import_plan("gameA", tmp_path)
    assert _dates(plan.to_import) == ["2200.02.01", "2200.04.01"]
    assert plan.num_filtered_out == 2
    assert plan.num_saves == 4


def test_plan_save_name_filter(tmp_path, monkeypatch):
    game = tmp_path / "gameA"
    make_save(game, "autosave_1.sav", "2200.01.01")
    make_save(game, "manual.sav", "2200.02.01")
    make_save(game, "AUTOSAVE_2.sav", "2200.03.01")
    monkeypatch.setattr(config.CONFIG, "save_name_filter", "autosave")
    plan = bulk_import.get_import_plan("gameA", tmp_path)
    assert _dates(plan.to_import) == ["2200.01.01", "2200.03.01"]
    assert plan.num_filtered_out == 1


# --- list_save_games ---


def test_list_save_games(tmp_path, monkeypatch):
    make_save(tmp_path / "gameA", "a.sav", "2200.01.01")
    make_save(tmp_path / "gameA", "b.sav", "2200.02.01")
    newer = make_save(tmp_path / "gameB", "c.sav", "2200.01.01")
    os.utime(newer, (4_000_000_000, 4_000_000_000))
    monkeypatch.setattr(datamodel, "get_available_games_dict", lambda: {"gameA": {"num_saves": 2}})
    games = bulk_import.list_save_games(tmp_path)
    by_id = {g.game_id: g for g in games}
    assert by_id["gameA"].num_saves == 2 and by_id["gameA"].num_imported == 2
    assert by_id["gameB"].num_saves == 1 and by_id["gameB"].num_imported == 0
    # most recently saved first
    assert [g.game_id for g in games] == ["gameB", "gameA"]


def test_list_save_games_missing_folder(tmp_path):
    assert bulk_import.list_save_games(tmp_path / "nope") == []


# --- ImportJob ---


@pytest.fixture
def dropped_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(visualization_data, "drop_cached_plot_data", calls.append)
    return calls


def _fake_parse(monkeypatch, fail_name=None, on_first_save=None, gate=None, calls=None):
    """Replace cli.f_parse_saves; the save named fail_name fails to parse."""
    log = logging.getLogger("stellarisdashboard.parsing.timeline")

    def fake(
        save_path=None,
        save_files=None,
        use_worker_processes=False,
        stop_event=None,
        on_save_processed=None,
        **kw,
    ):
        if calls is not None:
            calls.append(list(save_files))
        for i, f in enumerate(save_files):
            if stop_event.is_set():
                return
            if gate is not None and i == 0:
                gate.wait(10)
                if stop_event.is_set():
                    return
            if on_first_save is not None and i == 0:
                on_first_save()
            if f.name == fail_name:
                try:
                    raise ValueError("boom")
                except ValueError:
                    log.exception("Error while reading save")
                on_save_processed(f.parent.stem, None)
            else:
                info = bulk_import.read_save_info(f)
                on_save_processed(f.parent.stem, {"date": info.date_str})

    monkeypatch.setattr(cli, "f_parse_saves", fake)


def _three_saves(tmp_path):
    game = tmp_path / "gameA"
    for m in (1, 2, 3):
        make_save(game, f"s{m}.sav", f"2200.0{m}.01")
    return game


def test_finished_job_with_error(tmp_path, monkeypatch, dropped_caches):
    _three_saves(tmp_path)
    _fake_parse(monkeypatch, fail_name="s2.sav")
    job = bulk_import.start_import("gameA")
    job._thread.join(timeout=10)
    assert not job._thread.is_alive()
    assert bulk_import.get_current_job() is job
    assert job.status == bulk_import.ImportJob.FINISHED
    assert not job.is_active
    assert job.total == 3 and job.done == 3
    assert job.current_date == "2200.03.01"
    assert job.num_errors == 1
    assert len(job.errors) == 1
    assert "s2.sav" in job.errors[0] and "boom" in job.errors[0]
    assert dropped_caches == ["gameA"]
    assert job.elapsed_seconds >= 0


def test_start_import_refuses_when_all_imported(tmp_path, monkeypatch):
    _three_saves(tmp_path)
    all_dates = {datamodel.date_to_days(f"2200.0{m}.01") for m in (1, 2, 3)}
    monkeypatch.setattr(bulk_import, "_imported_dates", lambda g: all_dates)
    with pytest.raises(bulk_import.ImportRefused, match="already imported"):
        bulk_import.start_import("gameA")
    assert bulk_import.get_current_job() is None


def test_start_import_refuses_missing_save_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "save_file_path", tmp_path / "nope")
    with pytest.raises(bulk_import.ImportRefused):
        bulk_import.start_import("gameA")


def test_second_import_refused_and_cancel(tmp_path, monkeypatch, dropped_caches):
    _three_saves(tmp_path)
    make_save(tmp_path / "gameB", "x.sav", "2200.01.01")
    gate = threading.Event()
    _fake_parse(monkeypatch, gate=gate)
    job = bulk_import.start_import("gameA")
    try:
        assert job.is_active
        with pytest.raises(bulk_import.ImportRefused, match="already running"):
            bulk_import.start_import("gameB")
        assert bulk_import.cancel_import() is job
        assert job.status == bulk_import.ImportJob.CANCELLING
        assert job.is_active
    finally:
        gate.set()
        job._thread.join(timeout=10)
    assert job.status == bulk_import.ImportJob.CANCELLED
    assert job.done < job.total
    assert dropped_caches == ["gameA"]
    # a new import can start once the old one has ended
    gate.set()
    job2 = bulk_import.start_import("gameB")
    job2._thread.join(timeout=10)
    assert job2.status == bulk_import.ImportJob.FINISHED


def test_cancel_without_job():
    assert bulk_import.cancel_import() is None


def test_failed_job(tmp_path, monkeypatch, dropped_caches):
    _three_saves(tmp_path)

    def broken(**kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(cli, "f_parse_saves", broken)
    job = bulk_import.start_import("gameA")
    job._thread.join(timeout=10)
    assert job.status == bulk_import.ImportJob.FAILED
    assert job.num_errors == 1 and "db gone" in job.errors[0]
    assert dropped_caches == ["gameA"]


def test_defer_to_running_import(tmp_path, monkeypatch, dropped_caches):
    _three_saves(tmp_path)
    assert bulk_import.defer_to_running_import("gameA") is False  # no job
    gate = threading.Event()
    _fake_parse(monkeypatch, gate=gate)
    job = bulk_import.start_import("gameA")
    try:
        assert bulk_import.defer_to_running_import("gameA") is True
        assert bulk_import.defer_to_running_import("gameB") is False
        assert job.num_deferred_by_watcher == 1
    finally:
        gate.set()
        job._thread.join(timeout=10)
    assert bulk_import.defer_to_running_import("gameA") is False  # finished


def test_no_deferring_after_cancel(tmp_path, monkeypatch, dropped_caches):
    _three_saves(tmp_path)
    gate = threading.Event()
    _fake_parse(monkeypatch, gate=gate)
    job = bulk_import.start_import("gameA")
    try:
        bulk_import.cancel_import()
        assert bulk_import.defer_to_running_import("gameA") is False
    finally:
        gate.set()
        job._thread.join(timeout=10)


def test_deferred_save_is_imported_after_first_batch(tmp_path, monkeypatch, dropped_caches):
    game = _three_saves(tmp_path)
    calls = []
    new_files = []

    def new_save_appears():
        if new_files:
            return
        new_files.append(make_save(game, "s4.sav", "2200.04.01"))
        assert bulk_import.defer_to_running_import("gameA") is True

    _fake_parse(monkeypatch, on_first_save=new_save_appears, calls=calls)
    job = bulk_import.start_import("gameA")
    job._thread.join(timeout=10)
    assert job.status == bulk_import.ImportJob.FINISHED
    assert len(calls) == 2
    assert [f.name for f in calls[0]] == ["s1.sav", "s2.sav", "s3.sav"]
    assert calls[1] == new_files
    assert job.total == 4 and job.done == 4
    assert job.current_date == "2200.04.01"
    assert job.num_deferred_by_watcher == 1
    assert bulk_import.defer_to_running_import("gameA") is False


def test_save_written_during_import_is_imported_without_deferral(tmp_path, monkeypatch, dropped_caches):
    # e.g. a save the busy watcher skipped
    game = _three_saves(tmp_path)
    calls = []
    new_files = []

    def new_save_appears():
        if not new_files:
            new_files.append(make_save(game, "s4.sav", "2200.04.01"))

    _fake_parse(monkeypatch, on_first_save=new_save_appears, calls=calls)
    job = bulk_import.start_import("gameA")
    job._thread.join(timeout=10)
    assert calls[1] == new_files
    assert job.total == 4 and job.done == 4


def test_process_gamestate_holds_process_wide_lock(monkeypatch):
    held = []

    class FakeSession:
        def rollback(self):
            pass

    def fake_get_db_session(game_id, write=False):
        @contextlib.contextmanager
        def cm():
            held.append(timeline._process_gamestate_lock.locked())
            yield FakeSession()

        return cm()

    tle = timeline.TimelineExtractor()
    monkeypatch.setattr(tle, "_read_basic_game_info", lambda game_id: None)
    monkeypatch.setattr(tle, "basic_info", type("B", (), {"logger_str": ""})(), raising=False)
    monkeypatch.setattr(tle, "_get_or_add_game_to_db", lambda game_id: None)
    monkeypatch.setattr(tle, "_check_if_gamestate_exists", lambda db_game: True)
    monkeypatch.setattr(datamodel, "get_db_session", fake_get_db_session)
    monkeypatch.setattr(timeline.sqlalchemy.event, "listen", lambda *a, **kw: None)
    tle.process_gamestate("gameA", {})
    assert held == [True]
    assert not timeline._process_gamestate_lock.locked()


# --- BatchSavePathMonitor ---


def test_batch_monitor_save_files_exact_and_ordered(tmp_path):
    game = tmp_path / "gameA"
    a = make_save(game, "a.sav", "2200.01.01")
    b = make_save(game, "b.sav", "2200.02.01")
    make_save(game, "c.sav", "2200.03.01")
    monitor = save_parser.BatchSavePathMonitor(tmp_path, save_files=[b, a])
    assert monitor.get_new_savefiles() == [b, a]


def test_batch_monitor_without_save_files_scans(tmp_path):
    a = make_save(tmp_path / "gameA", "a.sav", "2200.01.01")
    monitor = save_parser.BatchSavePathMonitor(tmp_path)
    assert monitor.get_new_savefiles() == [a]


def test_batch_monitor_failed_save_yields_none(tmp_path, monkeypatch):
    game = tmp_path / "gameA"
    a = make_save(game, "a.sav", "2200.01.01")
    b = make_save(game, "b.sav", "2200.02.01")
    c = make_save(game, "c.sav", "2200.03.01")

    def fake_parse(path):
        if path == b:
            raise ValueError("corrupt")
        return {"date": path.stem}

    monkeypatch.setattr(save_parser, "parse_save", fake_parse)
    monitor = save_parser.BatchSavePathMonitor(
        tmp_path, save_files=[a, b, c], use_worker_processes=False
    )
    results = list(monitor.get_gamestates_and_check_for_new_files())
    assert results == [
        ("gameA", {"date": "a"}, a),
        ("gameA", None, b),
        ("gameA", {"date": "c"}, c),
    ]
    assert (save_copies.failed_saves_path() / "gameA" / "b.sav").is_file()


# --- cli.f_parse_saves ---


class _FakeMonitor:
    results = []
    init_args = None

    def __init__(self, save_path, game_name_prefix="", save_files=None, use_worker_processes=False):
        _FakeMonitor.init_args = (save_path, game_name_prefix, save_files, use_worker_processes)

    def get_gamestates_and_check_for_new_files(self):
        yield from _FakeMonitor.results


class _FakeExtractor:
    processed = []

    def __init__(self):
        self.failures = []
        self.number_of_parsed_saves = 0

    def process_gamestate(self, game_name, gamestate, replace_existing=False):
        _FakeExtractor.processed.append((game_name, gamestate))
        self.number_of_parsed_saves += 1


@pytest.fixture
def fake_pipeline(monkeypatch):
    _FakeExtractor.processed = []
    _FakeMonitor.results = []
    monkeypatch.setattr(save_parser, "BatchSavePathMonitor", _FakeMonitor)
    monkeypatch.setattr(timeline, "TimelineExtractor", _FakeExtractor)
    return _FakeMonitor


def test_f_parse_saves_passes_options_and_reports_unparseable(tmp_path, fake_pipeline):
    files = [tmp_path / "a.sav", tmp_path / "b.sav", tmp_path / "c.sav"]
    fake_pipeline.results = [
        ("g", {"date": 1}, files[0]),
        ("g", None, files[1]),
        ("g", {"date": 3}, files[2]),
    ]
    reported = []
    cli.f_parse_saves(
        save_path=tmp_path,
        save_files=files,
        use_worker_processes=True,
        on_save_processed=lambda g, d: reported.append((g, d)),
    )
    assert fake_pipeline.init_args == (tmp_path, "", files, True)
    assert reported == [("g", {"date": 1}), ("g", None), ("g", {"date": 3})]
    assert _FakeExtractor.processed == [("g", {"date": 1}), ("g", {"date": 3})]


def test_f_parse_saves_stop_event(tmp_path, fake_pipeline):
    fake_pipeline.results = [("g", {"date": d}, tmp_path / f"{d}.sav") for d in (1, 2, 3)]
    stop = threading.Event()
    reported = []

    def on_processed(game, gamestate):
        reported.append(gamestate)
        stop.set()

    cli.f_parse_saves(save_path=tmp_path, stop_event=stop, on_save_processed=on_processed)
    assert reported == [{"date": 1}]
    assert _FakeExtractor.processed == [("g", {"date": 1})]


def test_f_parse_saves_stop_event_already_set(tmp_path, fake_pipeline):
    fake_pipeline.results = [("g", {"date": 1}, tmp_path / "a.sav")]
    stop = threading.Event()
    stop.set()
    cli.f_parse_saves(save_path=tmp_path, stop_event=stop)
    assert _FakeExtractor.processed == []


# --- reprocessing kept copies of failed saves ---


def _keep(game, name, date, error="Traceback...\nValueError: bad value"):
    """Create a kept copy of a failed save together with its record."""
    path = make_save(save_copies.failed_saves_path() / game, name, date)
    failures = [save_copies.failure(save_copies.STAGE_IMPORT, error)]
    save_copies.record_path(path).write_text(
        json.dumps(dict(game_id=game, game_date=date, failed_at="2300-01-02T03:04:05", failures=failures))
    )
    return path


def test_list_kept_copies():
    _keep("gameB", "b.sav", "2200.01.01")
    _keep("gameA", "late.sav", "2210.01.01")
    early = _keep("gameA", "early.sav", "2205.05.05")
    save_copies.record_path(early).unlink()  # a copy without a record is still listed
    copies = save_copies.list_kept_copies()
    assert [(c.game_id, c.path.name) for c in copies] == [
        ("gameA", "early.sav"),
        ("gameA", "late.sav"),
        ("gameB", "b.sav"),
    ]
    assert copies[0].game_date == "2205.05.05" and copies[0].reasons == []
    assert copies[1].reasons == ["import: ValueError: bad value"]
    assert copies[1].failed_at == "2300-01-02T03:04:05"
    assert [c.path for c in save_copies.list_kept_copies("gameB")] == [copies[2].path]


def test_list_kept_copies_without_folder():
    assert save_copies.list_kept_copies() == []


def test_start_reprocess_refuses_without_copies():
    with pytest.raises(bulk_import.ImportRefused, match="no kept copies"):
        bulk_import.start_reprocess()
    assert bulk_import.get_current_job() is None


def test_reprocess_all_games(monkeypatch, dropped_caches):
    kept = [
        _keep("gameB", "b.sav", "2200.01.01"),
        _keep("gameA", "a2.sav", "2201.01.01"),
        _keep("gameA", "a1.sav", "2200.06.01"),
    ]
    calls = []
    _fake_parse(monkeypatch, calls=calls)
    job = bulk_import.start_reprocess()
    job._thread.join(timeout=10)
    assert job.status == bulk_import.ImportJob.FINISHED
    assert job.is_reprocess and job.total == 3 and job.done == 3
    # one pass over all games, in order of game and date
    assert calls == [[kept[2], kept[1], kept[0]]]
    assert sorted(dropped_caches) == ["gameA", "gameB"]


def test_reprocess_one_game_replaces_existing(monkeypatch, dropped_caches):
    _keep("gameA", "a.sav", "2200.01.01")
    kept_b = _keep("gameB", "b.sav", "2200.01.01")
    seen = {}

    def fake(save_files=None, on_save_processed=None, **kw):
        seen.update(kw, files=list(save_files))
        for f in save_files:
            on_save_processed(f.parent.stem, {"date": "2200.01.01"})

    monkeypatch.setattr(cli, "f_parse_saves", fake)
    job = bulk_import.start_reprocess("gameB")
    job._thread.join(timeout=10)
    assert seen["files"] == [kept_b]
    assert seen["replace_existing"] is True
    assert seen["save_path"] == save_copies.failed_saves_path()
    assert dropped_caches == ["gameB"]


def test_reprocess_reports_errors_and_refuses_second_job(monkeypatch, dropped_caches):
    _keep("gameA", "a1.sav", "2200.01.01")
    _keep("gameA", "a2.sav", "2200.02.01")
    gate = threading.Event()
    _fake_parse(monkeypatch, fail_name="a2.sav", gate=gate)
    job = bulk_import.start_reprocess("gameA")
    with pytest.raises(bulk_import.ImportRefused, match="already running"):
        bulk_import.start_reprocess()
    gate.set()
    job._thread.join(timeout=10)
    assert job.num_errors == 1 and "a2.sav" in job.errors[0]


def test_reprocess_does_not_defer_the_watcher(monkeypatch, dropped_caches):
    _keep("gameA", "a.sav", "2200.01.01")
    gate = threading.Event()
    _fake_parse(monkeypatch, gate=gate)
    job = bulk_import.start_reprocess("gameA")
    assert not bulk_import.defer_to_running_import("gameA")
    gate.set()
    job._thread.join(timeout=10)


def test_clean_import_of_kept_copy_removes_it(monkeypatch):
    kept = _keep("gameA", "a.sav", "2200.01.01")
    monkeypatch.setattr(timeline, "TimelineExtractor", _FakeExtractor)
    _FakeExtractor.processed = []
    cli.f_import_gamestate("gameA", {"date": "2200.01.01"}, kept, replace_existing=True)
    assert not kept.exists() and not save_copies.record_path(kept).exists()
    assert _FakeExtractor.processed == [("gameA", {"date": "2200.01.01"})]


def test_failed_saves_page(monkeypatch, dropped_caches):
    from stellarisdashboard.dashboard_app import flask_app, import_page

    _keep("gameA", "a.sav", "2200.01.01", error="x\nKeyError: 'boom'")
    # the views are called directly: the app's before-request hooks need the whole Dash layout
    with flask_app.test_request_context("/import/failed"):
        html = import_page.import_failed_saves()
    assert "a.sav" in html and "KeyError: &#39;boom&#39;" in html
    assert "Reprocess all 1 failed save" in html
    _fake_parse(monkeypatch)
    with flask_app.test_request_context(
        "/import/reprocess", method="POST", headers={"HX-Request": "true"}
    ):
        resp = import_page.start_reprocess()
    assert resp.headers["HX-Trigger"] == "reprocess-started"
    assert "eprocessing" in resp.get_data(as_text=True)  # "Reprocessing" or, if already done, "Finished reprocessing"
    bulk_import.get_current_job()._thread.join(timeout=10)


def test_list_kept_copies_ignores_glob_and_traversal_in_game_id():
    _keep("gameA", "a.sav", "2200.01.01")
    outside = make_save(save_copies.failed_saves_path().parent / "outside", "x.sav", "2200.01.01")
    assert outside.exists()
    for bad in ("../outside", "*", "game[A]", "gam?A", ".."):
        assert save_copies.list_kept_copies(bad) == []
        with pytest.raises(bulk_import.ImportRefused):
            bulk_import.start_reprocess(bad)
    assert bulk_import.get_current_job() is None


def test_reprocess_all_with_a_single_game_targets_that_game(monkeypatch, dropped_caches):
    from stellarisdashboard.dashboard_app import flask_app, import_page

    _keep("gameA", "a.sav", "2200.01.01")
    _fake_parse(monkeypatch)
    job = bulk_import.start_reprocess()
    job._thread.join(timeout=10)
    assert job.game_id == "gameA"
    with flask_app.test_request_context("/import/status"):
        html = import_page.import_status()
    assert "Finished reprocessing" in html and "game_name=gameA" in html.replace("?", "&").replace("/timeline/&", "")


def test_reprocess_of_several_games_has_no_game_links(monkeypatch, dropped_caches):
    from stellarisdashboard.dashboard_app import flask_app, import_page

    _keep("gameA", "a.sav", "2200.01.01")
    _keep("gameB", "b.sav", "2200.01.01")
    _fake_parse(monkeypatch)
    job = bulk_import.start_reprocess()
    job._thread.join(timeout=10)
    with flask_app.test_request_context("/import/status"):
        html = import_page.import_status()
    assert "Timeline Graphs" not in html


def test_finished_bulk_import_wording(tmp_path, monkeypatch, dropped_caches):
    from stellarisdashboard.dashboard_app import flask_app, import_page

    _three_saves(tmp_path)
    _fake_parse(monkeypatch)
    job = bulk_import.start_import("gameA")
    job._thread.join(timeout=10)
    with flask_app.test_request_context("/import/status"):
        assert "Finished importing" in import_page.import_status()


def test_reprocess_skips_copy_that_disappeared(monkeypatch, dropped_caches):
    gone = _keep("gameA", "gone.sav", "2200.01.01")
    kept = _keep("gameA", "kept.sav", "2200.02.01")
    real = bulk_import.read_save_info

    def read(path):
        if path == gone:
            raise FileNotFoundError(path)
        return real(path)

    monkeypatch.setattr(bulk_import, "read_save_info", read)
    calls = []
    _fake_parse(monkeypatch, calls=calls)
    job = bulk_import.start_reprocess()
    job._thread.join(timeout=10)
    assert calls == [[kept]] and job.total == 1


def test_copy_that_fails_again_stays_listed_with_refreshed_record(monkeypatch):
    kept = _keep("gameA", "a.sav", "2200.01.01", error="old\nValueError: old")
    save_copies.keep_failed_save(
        kept, "gameA", [save_copies.failure(save_copies.STAGE_IMPORT, "new\nKeyError: new")], "2200.01.01"
    )
    (copy,) = save_copies.list_kept_copies()
    assert copy.path == kept and copy.reasons == ["import: KeyError: new"]
