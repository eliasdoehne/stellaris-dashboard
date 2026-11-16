"""Reading and writing the settings file, and the settings page."""
import logging

import pytest
import yaml

from stellarisdashboard import config


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """config.yml in tmp_path, found through the environment variable (not the working directory)."""
    path = tmp_path / "settings" / "config.yml"
    path.parent.mkdir()
    monkeypatch.setenv(config.SETTINGS_FILE_ENV_VAR, str(path))
    return path


def _write(path, **settings):
    path.write_text(yaml.dump(settings, sort_keys=False))


def _load():
    cfg = config.Config()
    result = config._apply_existing_settings(cfg)
    return cfg, result


def _read(path):
    return yaml.safe_load(path.read_text())


def test_settings_file_path_from_environment(settings_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.settings_file_path() == settings_file
    monkeypatch.delenv(config.SETTINGS_FILE_ENV_VAR)
    assert config.settings_file_path() == tmp_path / "config.yml"


def test_settings_are_read_from_the_environment_location(settings_file):
    _write(settings_file, port=4321)
    cfg, result = _load()
    assert cfg.port == 4321
    assert result.changed == ["port"]


def test_initialize_does_not_write_files(settings_file, tmp_path, monkeypatch):
    output = tmp_path / "output"
    _write(settings_file, port=4321, base_output_path=str(output), threads="x")
    content_before = settings_file.read_bytes()
    monkeypatch.setattr(config, "CONFIG", None)
    monkeypatch.setattr(config, "LOAD_RESULT", None)
    monkeypatch.setattr(config, "initialize_logger", lambda: None)
    level = logging.getLogger().level
    try:
        config.initialize()
        assert config.CONFIG.port == 4321
        assert [r.key for r in config.LOAD_RESULT.rejected] == ["threads"]
    finally:
        logging.getLogger().setLevel(level)
    assert settings_file.read_bytes() == content_before
    assert sorted(p.name for p in settings_file.parent.iterdir()) == ["config.yml"]
    assert not output.exists()


def test_only_customized_settings_are_written(settings_file, tmp_path):
    cfg = config.Config()
    cfg.apply_dict({"port": 4321, "show_everything": True, "save_file_path": str(tmp_path)})
    cfg.write_to_file()
    text = settings_file.read_text()
    assert text.startswith("# Settings of the Stellaris Dashboard")
    assert "README" in text
    assert _read(settings_file) == {
        "save_file_path": str(tmp_path),
        "show_everything": True,
        "port": 4321,
    }
    reloaded, result = _load()
    assert result.rejected == []
    assert reloaded == cfg


def test_nothing_customized_writes_only_the_header(settings_file):
    config.Config().write_to_file()
    assert settings_file.read_text() == config.SETTINGS_FILE_HEADER
    assert _read(settings_file) is None


def test_old_file_with_every_default_is_not_customized(settings_file):
    # older versions wrote every setting, freezing the defaults of their version
    old_file = {key: config._to_yaml_value(key, value) for key, value in config.DEFAULT_SETTINGS.items()}
    old_file["port"] = 4321
    _write(settings_file, **old_file)
    cfg, result = _load()
    assert result.rejected == []
    assert cfg.get_persisted_dict() == {"port": 4321}
    cfg.write_to_file()
    assert _read(settings_file) == {"port": 4321}


def test_stored_default_path_is_not_persisted(settings_file):
    _write(settings_file, save_file_path=str(config.DEFAULT_SETTINGS["save_file_path"]))
    cfg, result = _load()
    assert result.rejected == []
    assert not cfg.is_customized("save_file_path")
    assert "save_file_path" not in cfg.get_persisted_dict()


def test_runtime_state_is_never_written(settings_file):
    cfg = config.Config()
    cfg.debug_mode = True
    cfg.normalize_stacked_plots = True
    cfg.write_to_file()
    assert _read(settings_file) is None


def test_write_replaces_the_file_atomically(settings_file):
    settings_file.write_text("port: 1234\n")
    cfg = config.Config(port=4321)
    cfg.write_to_file()
    assert _read(settings_file) == {"port": 4321}
    assert sorted(p.name for p in settings_file.parent.iterdir()) == ["config.yml"]


def test_write_creates_the_folder(tmp_path, monkeypatch):
    path = tmp_path / "new" / "config.yml"
    monkeypatch.setenv(config.SETTINGS_FILE_ENV_VAR, str(path))
    config.Config(port=4321).write_to_file()
    assert _read(path) == {"port": 4321}


def test_removed_settings_in_the_file_are_ignored(settings_file, caplog):
    _write(settings_file, read_all_countries=True, port=4321)
    cfg, result = _load()
    assert cfg.port == 4321
    assert result.ignored == ["read_all_countries"]
    assert "read_all_countries" in caplog.text and "no longer used" in caplog.text
    cfg.write_to_file()
    assert _read(settings_file) == {"port": 4321}


def test_invalid_values_in_the_file_use_the_default(settings_file):
    _write(
        settings_file,
        threads="many",
        port=80,
        show_everything="maybe",
        tab_layout=["Budget"],
        log_level="LOUD",
        skip_saves=3,
    )
    cfg, result = _load()
    assert sorted(r.key for r in result.rejected) == [
        "log_level", "port", "show_everything", "tab_layout", "threads",
    ]
    for key in ["threads", "port", "show_everything", "tab_layout", "log_level"]:
        assert getattr(cfg, key) == config.DEFAULT_SETTINGS[key]
    assert cfg.skip_saves == 3


@pytest.mark.parametrize("content", ["port: [1, 2", "just a string", "- a list"])
def test_unreadable_file_uses_defaults_and_is_kept(settings_file, content):
    settings_file.write_text(content)
    cfg, result = _load()
    assert cfg == config.Config()
    cfg.port = 4321
    cfg.write_to_file()
    assert _read(settings_file) == {"port": 4321}
    assert settings_file.with_name("config.yml.unreadable").read_text() == content


# --- the settings page


@pytest.fixture
def client(settings_file, monkeypatch, tmp_path):
    from stellarisdashboard import dashboard_app

    custom_layout = {"Budget": ["energy_budget"], "Economy": ["net_energy_income_graph"]}
    saves = tmp_path / "saves"
    saves.mkdir()
    _write(settings_file, tab_layout=custom_layout, save_file_path=str(saves), market_fee=[{"date": "2250.01.01", "fee": 0.1}])
    cfg, result = _load()
    assert result.rejected == []
    monkeypatch.setattr(config, "CONFIG", cfg)
    return dashboard_app.flask_app.test_client()


def _form(**overrides):
    """A complete form, as the settings page submits it: checked checkboxes only."""
    form = {}
    for key, spec in config.SETTINGS.items():
        if not spec.in_ui:
            continue
        value = config._to_yaml_value(key, getattr(config.CONFIG, key))
        if spec.kind == config.BOOL:
            if value:
                form[key] = "on"
        else:
            form[key] = str(value)
    form.update(overrides)
    return {k: v for k, v in form.items() if v is not None}


def _post(client, form):
    return client.post("/applysettings/", data=form, headers={"HX-Request": "true"})


def test_settings_page_renders(client):
    response = client.get("/settings/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for key, spec in config.SETTINGS.items():
        assert (f'name="{key}"' in page) == spec.in_ui, key


def test_valid_change_is_applied_and_persisted(client, settings_file):
    response = _post(client, _form(port="4321", skip_saves="3"))
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "toast--success" in body
    # port only applies after a restart
    assert "Restart the dashboard to apply" in body and "Port" in body
    assert config.CONFIG.port == 4321
    written = _read(settings_file)
    assert written["port"] == 4321
    assert written["skip_saves"] == 3


def test_invalid_value_is_rejected_and_the_previous_value_kept(client, settings_file):
    config.CONFIG.threads = 2
    response = _post(client, _form(threads="lots", port="80", skip_saves="4"))
    body = response.get_data(as_text=True)
    assert "toast--error" in body
    assert "Number of threads" in body and "is not a whole number" in body
    assert "Port" in body and "Must be between 1024 and 65535." in body
    assert config.CONFIG.threads == 2
    assert config.CONFIG.port == config.DEFAULT_SETTINGS["port"]
    # the valid setting of the same submission is applied
    assert config.CONFIG.skip_saves == 4
    written = _read(settings_file)
    assert written["threads"] == 2 and "port" not in written and written["skip_saves"] == 4


def test_missing_folder_is_rejected(client, tmp_path):
    previous = config.CONFIG.save_file_path
    body = _post(client, _form(save_file_path=str(tmp_path / "missing"))).get_data(as_text=True)
    assert "does not exist" in body
    assert config.CONFIG.save_file_path == previous


def test_unchecked_checkbox_is_false(client, settings_file):
    config.CONFIG.show_everything = True
    config.CONFIG.hide_other_players = True
    _post(client, _form(show_everything=None, hide_other_players=None))
    assert config.CONFIG.show_everything is False
    assert config.CONFIG.hide_other_players is False
    assert _read(settings_file)["hide_other_players"] is False


def test_yaml_only_settings_survive_a_form_save(client, settings_file):
    _post(client, _form(port="4321", tab_layout="{}", market_fee="[]"))
    written = _read(settings_file)
    assert written["tab_layout"] == {"Budget": ["energy_budget"], "Economy": ["net_energy_income_graph"]}
    assert written["market_fee"] == [{"date": "2250.01.01", "fee": 0.1}]
    assert config.CONFIG.tab_layout == written["tab_layout"]


def test_plain_post_redirects_to_the_settings_page(client):
    response = client.post("/applysettings/", data=_form())
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/")
