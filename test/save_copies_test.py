import contextlib
import json
import os
import pathlib
import time
import zipfile

import pytest
import sqlalchemy

from stellarisdashboard import cli, config, datamodel
from stellarisdashboard.parsing import save_copies, timeline

GAMESTATE = {
    "date": "2300.01.01",
    "galaxy": {},
    "country": {},
}


class _AddsCountry(timeline.AbstractGamestateDataProcessor):
    ID = "ok"

    def extract_data_from_gamestate(self, dependencies):
        self._session.add(
            datamodel.Country(
                game=self._db_game, country_id_in_game=1, country_name=self.ID
            )
        )

    def data(self):
        return self.ID


class _Good(_AddsCountry):
    ID = "good"


class _Bad(_AddsCountry):
    ID = "bad"

    def extract_data_from_gamestate(self, dependencies):
        super().extract_data_from_gamestate(dependencies)
        self._session.flush()
        raise RuntimeError("boom")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config.CONFIG, "base_output_path", tmp_path / "out")
    monkeypatch.setattr(config.CONFIG, "keep_failed_saves", True)
    monkeypatch.setattr(config.CONFIG, "backup_saves", False)
    monkeypatch.setattr(config.CONFIG, "failed_saves_max_size_mb", 1000)
    monkeypatch.setattr(config.CONFIG, "debug_mode", False)
    return config.CONFIG


@pytest.fixture
def db(tmp_path, monkeypatch, settings):
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    datamodel.Base.metadata.create_all(engine)
    session_factory = sqlalchemy.orm.sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_db_session(game_id, write=False):
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(datamodel, "get_db_session", get_db_session)
    return session_factory


@pytest.fixture
def save(tmp_path):
    path = tmp_path / "saves" / "game" / "autosave.sav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"save content")
    return path


def _make_save(path: pathlib.Path, size=10, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    path.with_suffix(".json").write_text("{}")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


FAILURES = [save_copies.failure(save_copies.STAGE_PARSE, "bad parse")]


def test_copy_name():
    mtime = 1_700_000_000.0
    assert save_copies.copy_name(pathlib.Path("a/autosave.sav"), "2300.01.01", mtime) == "autosave.sav"
    assert (
        save_copies.copy_name(pathlib.Path("a/ironman.sav"), "2300.01.01", mtime)
        == "ironman_2300.01.01.sav"
    )
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(mtime))
    assert save_copies.copy_name(pathlib.Path("a/ironman.sav"), None, mtime) == f"ironman_{stamp}.sav"


def test_read_save_date(tmp_path):
    path = tmp_path / "x.sav"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("meta", 'version="Test"\ndate="2312.05.01"\nname="x"\n')
        z.writestr("gamestate", "")
    assert save_copies.read_save_date(path) == "2312.05.01"

    not_zip = tmp_path / "y.sav"
    not_zip.write_bytes(b"nope")
    assert save_copies.read_save_date(not_zip) is None


def test_select_copies_to_delete_oldest_first():
    a, b, c = (pathlib.Path(n) for n in "abc")
    sizes = {a: 10, b: 10, c: 10}
    mtimes = {a: 3, b: 1, c: 2}
    assert save_copies.select_copies_to_delete([a, b, c], sizes, mtimes, 15) == [b, c]
    assert save_copies.select_copies_to_delete([a, b, c], sizes, mtimes, 30) == []


def test_select_copies_to_delete_never_deletes_keep():
    a, b = pathlib.Path("a"), pathlib.Path("b")
    sizes = {a: 100, b: 10}
    mtimes = {a: 1, b: 2}
    assert save_copies.select_copies_to_delete([a, b], sizes, mtimes, 5, keep=a) == [b]


def test_enforce_size_limit(tmp_path):
    base = tmp_path / "failed"
    old = _make_save(base / "g1" / "old.sav", 100, mtime=1000)
    mid = _make_save(base / "g2" / "mid.sav", 100, mtime=2000)
    new = _make_save(base / "g2" / "new.sav", 100, mtime=3000)

    deleted = save_copies.enforce_size_limit(base, 210, keep=new)
    assert deleted == [old]
    assert not old.exists() and not old.with_suffix(".json").exists()
    assert not (base / "g1").exists()
    assert mid.exists() and new.exists()

    save_copies.enforce_size_limit(base, 1, keep=new)
    assert not mid.exists() and not mid.with_suffix(".json").exists()
    assert new.exists() and new.with_suffix(".json").exists()


def test_keep_failed_save_copies_and_writes_record(settings, save):
    failures = [save_copies.failure(save_copies.STAGE_PROCESSOR, "err", processor="bad")]
    dest = save_copies.keep_failed_save(save, "game", failures, "2300.01.01")

    assert dest == settings.base_output_path / "failed_saves" / "game" / "autosave.sav"
    assert dest.read_bytes() == b"save content"
    record = json.loads(dest.with_suffix(".json").read_text())
    assert record["save_file"] == str(save)
    assert record["game_id"] == "game"
    assert record["game_date"] == "2300.01.01"
    assert record["failures"] == failures
    assert record["failures"][0]["stage"] == "processor"
    assert record["failures"][0]["processor"] == "bad"


def test_keep_failed_save_disabled_or_no_failures(settings, save, monkeypatch):
    assert save_copies.keep_failed_save(save, "game", []) is None
    monkeypatch.setattr(settings, "keep_failed_saves", False)
    assert save_copies.keep_failed_save(save, "game", FAILURES) is None
    assert not save_copies.failed_saves_path().exists()


def test_keep_failed_save_on_kept_copy_refreshes_record(settings, save):
    dest = save_copies.keep_failed_save(save, "game", FAILURES, "2300.01.01")
    new_failures = [save_copies.failure(save_copies.STAGE_IMPORT, "second")]
    again = save_copies.keep_failed_save(dest, "game", new_failures, "2300.01.01")

    assert again == dest
    assert list(dest.parent.glob("*.sav")) == [dest]
    record = json.loads(dest.with_suffix(".json").read_text())
    assert record["save_file"] == str(save)
    assert record["failures"] == new_failures


def test_keep_failed_save_missing_source_does_not_raise(settings, tmp_path):
    assert save_copies.keep_failed_save(tmp_path / "missing.sav", "game", FAILURES) is None


def test_remove_kept_copy(settings, save):
    dest = save_copies.keep_failed_save(save, "game", FAILURES)
    assert save_copies.is_kept_copy(dest)
    assert not save_copies.is_kept_copy(save)
    save_copies.remove_kept_copy(dest)
    assert not dest.exists()
    assert not dest.with_suffix(".json").exists()
    assert not dest.parent.exists()


def test_back_up_save(settings, save, monkeypatch):
    assert save_copies.back_up_save(save, "game") is None
    assert not save_copies.backup_saves_path().exists()

    monkeypatch.setattr(settings, "backup_saves", True)
    dest = save_copies.back_up_save(save, "game")
    assert dest == settings.base_output_path / "save_backups" / "game" / "autosave.sav"
    assert dest.read_bytes() == b"save content"

    assert save_copies.back_up_save(save, "game") == dest
    assert list(dest.parent.iterdir()) == [dest]


def test_back_up_save_skips_kept_copies(settings, save, monkeypatch):
    kept = save_copies.keep_failed_save(save, "game", FAILURES)
    monkeypatch.setattr(settings, "backup_saves", True)
    assert save_copies.back_up_save(kept, "game") is None
    assert not save_copies.backup_saves_path().exists()


def test_extractor_records_processor_failure(db, monkeypatch):
    extractor = timeline.TimelineExtractor()
    monkeypatch.setattr(extractor, "_data_processors", lambda: iter([_Good(), _Bad()]))
    extractor.process_gamestate("g", dict(GAMESTATE))
    assert len(extractor.failures) == 1
    assert extractor.failures[0]["stage"] == "processor"
    assert extractor.failures[0]["processor"] == "bad"
    assert "boom" in extractor.failures[0]["error"]


def test_replace_existing_replaces_gamestate(db, monkeypatch):
    def run(replace_existing):
        extractor = timeline.TimelineExtractor()
        monkeypatch.setattr(extractor, "_data_processors", lambda: iter([_Good()]))
        extractor.process_gamestate("g", dict(GAMESTATE), replace_existing=replace_existing)
        return extractor

    assert run(False).number_of_parsed_saves == 1
    assert run(False).number_of_parsed_saves == 0
    second = run(True)
    assert second.number_of_parsed_saves == 1
    with db() as session:
        assert session.query(datamodel.GameState).count() == 1


def test_f_import_gamestate_keeps_and_removes_copy(db, settings, save, monkeypatch):
    monkeypatch.setattr(
        timeline.TimelineExtractor, "_data_processors", lambda self: iter([_Bad()])
    )
    tle = cli.f_import_gamestate("game", dict(GAMESTATE), save)
    assert len(tle.failures) == 1

    kept = settings.base_output_path / "failed_saves" / "game" / "autosave.sav"
    assert kept.exists()
    record = json.loads(kept.with_suffix(".json").read_text())
    assert record["failures"][0]["processor"] == "bad"
    assert record["game_date"] == "2300.01.01"

    monkeypatch.setattr(
        timeline.TimelineExtractor, "_data_processors", lambda self: iter([_Good()])
    )
    tle = cli.f_import_gamestate("game", dict(GAMESTATE), kept, replace_existing=True)
    assert not tle.failures
    assert tle.number_of_parsed_saves == 1
    assert not kept.exists()
    assert not kept.with_suffix(".json").exists()
    with db() as session:
        assert session.query(datamodel.GameState).count() == 1
