import pytest

from stellarisdashboard import config


def test_obsolete_setting_is_ignored():
    # Settings files written by older versions can still contain removed keys
    config.initialize()
    settings = dict(config.CONFIG.get_dict(), read_all_countries=False)
    config.CONFIG.apply_dict(settings)
    assert not hasattr(config.CONFIG, "read_all_countries")
    assert "read_all_countries" not in config.CONFIG.get_dict()


# --- registry consistency


def test_registry_keys_match_their_specs():
    for key, spec in config.SETTINGS.items():
        assert spec.key == key
        assert spec.kind in config._KIND_PARSERS
        assert spec.category in config.CATEGORIES, key


def test_every_setting_has_a_default():
    assert set(config.DEFAULT_SETTINGS) == set(config.SETTINGS)
    cfg = config.Config()
    for key in config.SETTINGS:
        assert getattr(cfg, key) == config.DEFAULT_SETTINGS[key]


def test_defaults_are_valid():
    for key, spec in config.SETTINGS.items():
        default = config.DEFAULT_SETTINGS[key]
        if spec.kind == config.PATH:
            default = str(default)
        parsed = spec.parse(default)
        assert parsed is config.USE_DEFAULT or spec.comparable_value(parsed) == spec.comparable_value(
            config.DEFAULT_SETTINGS[key]
        ), key


def test_ui_settings_have_a_simple_kind():
    for key, spec in config.SETTINGS.items():
        if spec.in_ui:
            assert spec.kind != config.STRUCTURED, key
            assert spec.persisted, key
        if spec.kind == config.CHOICE:
            assert spec.get_choices(), key
        if spec.depends_on is not None:
            assert spec.depends_on in config.SETTINGS, key


def test_runtime_state_is_separate_from_settings():
    assert not set(config.RUNTIME_STATE_DEFAULTS) & set(config.SETTINGS)
    cfg = config.Config()
    for key, value in config.RUNTIME_STATE_DEFAULTS.items():
        assert getattr(cfg, key) == value
        assert key not in cfg.get_dict()


def test_every_ui_setting_has_a_label_and_description():
    from stellarisdashboard.dashboard_app import settings

    texts = settings.setting_texts()
    ui_keys = {key for key, spec in config.SETTINGS.items() if spec.in_ui}
    assert set(texts) == ui_keys
    for key, text in texts.items():
        assert text["label"] and text["description"], key
    assert set(settings.category_labels()) == set(config.CATEGORIES)


def test_removed_settings_are_not_in_the_registry():
    assert not config.REMOVED_SETTINGS & set(config.SETTINGS)


# --- validation


@pytest.mark.parametrize(
    "key,raw",
    [
        ("threads", "abc"),
        ("threads", 0),
        ("threads", 1.5),
        ("threads", True),
        ("port", 80),
        ("port", 70000),
        ("port", "http"),
        ("skip_saves", 101),
        ("skip_saves", -1),
        ("plot_height", 10),
        ("show_everything", "maybe"),
        ("show_everything", 3),
        ("show_everything", ["yes"]),
        ("tab_layout", ["Budget"]),
        ("tab_layout", "Budget"),
        ("market_resources", {"energy": 1}),
        ("market_resources", [{"name": "energy"}]),
        ("market_fee", [{"date": "soon", "fee": 0.3}]),
        ("market_fee", 0.3),
        ("log_level", "VERBOSE"),
        ("dashboard_language", "german"),
        ("stellaris_language", "english"),
        ("mp_username", {"name": "x"}),
        ("host", "local host"),
    ],
)
def test_invalid_values_from_the_file_fall_back_to_the_default(key, raw):
    cfg = config.Config()
    result = cfg.apply_dict({key: raw}, source=config.SOURCE_FILE)
    assert [r.key for r in result.rejected] == [key]
    rejected_key, rejected_value, reason = result.rejected[0]
    assert rejected_key == key and rejected_value == raw and reason
    assert getattr(cfg, key) == config.DEFAULT_SETTINGS[key]


def test_invalid_values_from_the_form_keep_the_previous_value():
    cfg = config.Config()
    cfg.apply_dict({"threads": 3, "port": 4321})
    result = cfg.apply_dict({"threads": "many", "port": "80", "skip_saves": "2"}, source=config.SOURCE_FORM)
    assert sorted(r.key for r in result.rejected) == ["port", "threads"]
    assert cfg.threads == 3
    assert cfg.port == 4321
    # valid settings of the same submission are applied
    assert cfg.skip_saves == 2
    assert result.changed == ["skip_saves"]


def test_reasons_are_human_readable():
    cfg = config.Config()
    result = cfg.apply_dict({"port": 80, "threads": "abc"})
    reasons = {r.key: r.reason for r in result.rejected}
    assert reasons["port"] == "Must be between 1024 and 65535."
    assert reasons["threads"] == "'abc' is not a whole number."
    assert result.rejected[0].translated_reason() == result.rejected[0].reason


@pytest.mark.parametrize(
    "key,raw,expected",
    [
        ("threads", "4", 4),
        ("threads", 4.0, 4),
        ("port", " 4321 ", 4321),
        ("show_everything", "on", True),
        ("show_everything", "true", True),
        ("show_everything", "False", False),
        ("show_everything", False, False),
        ("log_level", "debug", "DEBUG"),
        ("dashboard_language", "pt-BR", "pt_BR"),
        ("stellaris_language", "l_german", "l_german"),
        ("mp_username", 1234, "1234"),
        ("mp_username", " name ", "name"),
    ],
)
def test_valid_values_are_converted(key, raw, expected):
    cfg = config.Config()
    assert cfg.apply_dict({key: raw}).rejected == []
    assert getattr(cfg, key) == expected


@pytest.mark.parametrize("empty", ["", "  ", None])
def test_empty_values_reset_to_the_default(empty):
    cfg = config.Config(threads=4, mp_username="me", stellaris_language="l_german")
    result = cfg.apply_dict({"threads": empty, "mp_username": empty, "stellaris_language": empty})
    assert result.rejected == []
    assert cfg.threads == config.DEFAULT_SETTINGS["threads"]
    assert cfg.mp_username == ""
    assert cfg.stellaris_language == "l_english"


def test_modded_stellaris_language_is_accepted():
    cfg = config.Config()
    assert cfg.apply_dict({"stellaris_language": "l_klingon"}, source=config.SOURCE_FILE).rejected == []
    assert cfg.stellaris_language == "l_klingon"


def test_stellaris_language_choices_are_the_game_languages():
    choices = config.SETTINGS["stellaris_language"].get_choices()
    assert set(choices) == set(config.STELLARIS_LANGUAGES)
    assert len(config.STELLARIS_LANGUAGES) == 10
    assert choices["l_german"] == "Deutsch"


def test_missing_folder_is_rejected_from_the_form(tmp_path):
    cfg = config.Config()
    existing = tmp_path / "saves"
    existing.mkdir()
    assert cfg.apply_dict({"save_file_path": str(existing)}).rejected == []
    result = cfg.apply_dict({"save_file_path": str(tmp_path / "missing")})
    assert [r.key for r in result.rejected] == ["save_file_path"]
    assert "does not exist" in result.rejected[0].reason
    assert cfg.save_file_path == existing


def test_missing_folder_from_the_file_falls_back_to_the_default(tmp_path):
    cfg = config.Config()
    result = cfg.apply_dict({"save_file_path": str(tmp_path / "missing")}, source=config.SOURCE_FILE)
    assert [r.key for r in result.rejected] == ["save_file_path"]
    assert cfg.save_file_path == config.DEFAULT_SETTINGS["save_file_path"]


def test_a_file_is_not_a_folder(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("")
    cfg = config.Config()
    result = cfg.apply_dict({"stellaris_install_path": str(a_file), "base_output_path": str(a_file)})
    assert sorted(r.key for r in result.rejected) == ["base_output_path", "stellaris_install_path"]


def test_the_default_path_is_accepted_even_if_it_does_not_exist():
    cfg = config.Config()
    default = config.DEFAULT_SETTINGS["save_file_path"]
    assert cfg.apply_dict({"save_file_path": str(default)}, source=config.SOURCE_FILE).rejected == []
    assert cfg.save_file_path == default


def test_empty_path_resets_to_the_default(tmp_path):
    cfg = config.Config(save_file_path=tmp_path)
    assert cfg.apply_dict({"save_file_path": ""}).rejected == []
    assert cfg.save_file_path == config.DEFAULT_SETTINGS["save_file_path"]


def test_output_folder_is_not_created_when_applied(tmp_path):
    cfg = config.Config()
    cfg.apply_dict({"base_output_path": str(tmp_path / "new" / "output")})
    assert cfg.base_output_path == tmp_path / "new" / "output"
    assert not (tmp_path / "new").exists()


def test_unknown_and_removed_keys_are_ignored(caplog):
    cfg = config.Config()
    result = cfg.apply_dict({"no_such_setting": 1, "read_all_countries": True}, source=config.SOURCE_FILE)
    assert result.ignored == ["no_such_setting", "read_all_countries"]
    assert result.rejected == []
    assert "read_all_countries" in caplog.text and "no longer used" in caplog.text


def test_tab_layout_items_are_cleaned_up():
    cfg = config.Config()
    layout = {
        "Budget": ["energy_budget", 5],
        config.GALAXY_MAP_TAB: ["x"],
        config.MARKET_TAB: ["x"],
        "Broken": "not a list",
    }
    assert cfg.apply_dict({"tab_layout": layout}).rejected == []
    assert cfg.tab_layout == {"Budget": ["energy_budget"], config.MARKET_TAB: []}


def test_restart_required():
    cfg = config.Config()
    result = cfg.apply_dict({"threads": 2, "show_everything": True, "port": 4321})
    assert result.changed == ["threads", "show_everything", "port"]
    assert result.restart_required == ["threads", "port"]


def test_settings_from_form():
    form = {"threads": "2", "show_everything": "on", "tab_layout": "{}", "production": "on", "junk": "x"}
    settings = config.settings_from_form(form)
    assert settings["threads"] == "2"
    assert settings["show_everything"] == "on"
    # unchecked checkboxes are not submitted
    assert settings["hide_other_players"] is False
    # settings that are not on the settings page cannot be changed from the form
    assert "tab_layout" not in settings and "production" not in settings and "junk" not in settings


def test_reset_to_defaults():
    cfg = config.Config()
    cfg.apply_dict({"threads": 3, "port": 4321, "show_everything": True})
    result = cfg.reset_to_defaults(["threads", "show_everything"])
    assert result.changed == ["threads", "show_everything"]
    assert cfg.threads == 1 and cfg.show_everything is False
    assert cfg.port == 4321
    cfg.reset_to_defaults()
    assert cfg.port == config.DEFAULT_SETTINGS["port"]
    assert cfg == config.Config()


def test_reset_does_not_share_mutable_defaults():
    cfg = config.Config()
    cfg.tab_layout["Budget"].append("x")
    assert "x" not in config.DEFAULT_SETTINGS["tab_layout"]["Budget"]
    assert "x" not in config.DEFAULT_TAB_LAYOUT["Budget"]


def test_settings_view_shape():
    from stellarisdashboard.dashboard_app import settings, flask_app

    with flask_app.test_request_context():
        sections = settings.settings_view()
    assert [s["id"] for s in sections] == [
        c for c in config.CATEGORIES if any(spec.in_ui and spec.category == c for spec in config.SETTINGS.values())
    ]
    by_key = {s["key"]: s for section in sections for s in section["settings"]}
    assert set(by_key) == {key for key, spec in config.SETTINGS.items() if spec.in_ui}
    port = by_key["port"]
    assert port["kind"] == "int" and port["min"] == 1024 and port["max"] == 65535
    assert port["requires_restart"] and port["advanced"]
    assert port["default"] == "28053"
    assert by_key["hide_other_players"]["depends_on"] == "mp_username"
    assert by_key["save_file_path"]["status"]["level"] in ("ok", "warning")
    assert by_key["show_everything"]["status"] is None
    assert by_key["show_everything"]["default"] == "false"
    assert by_key["save_file_path"]["placeholder"] == str(config.DEFAULT_SETTINGS["save_file_path"])
    assert by_key["failed_saves_max_size_mb"]["unit"] == "MB"
    assert all(s["error"] is None for s in by_key.values())
    server = next(s for s in sections if s["id"] == "server")
    assert server["intro"] and server["basic"] == [] and {s["key"] for s in server["advanced"]} >= {"port", "host"}
    assert ("auto", "Same as Stellaris language") in by_key["dashboard_language"]["choices"]
    expected_keys = {
        "key", "kind", "value", "default", "is_default", "label", "description", "min", "max",
        "choices", "requires_restart", "advanced", "depends_on", "status",
        "saved", "unit", "placeholder", "pending_restart", "inactive", "inactive_note",
        "checks_folder", "error",
    }
    for setting in by_key.values():
        assert set(setting) == expected_keys


def test_settings_view_shows_a_modded_language(monkeypatch):
    from stellarisdashboard.dashboard_app import settings, flask_app

    monkeypatch.setattr(config, "CONFIG", config.Config(stellaris_language="l_klingon"))
    with flask_app.test_request_context():
        sections = settings.settings_view()
    language = next(s for section in sections for s in section["settings"] if s["key"] == "stellaris_language")
    assert language["value"] == "l_klingon"
    assert ("l_klingon", "l_klingon") in language["choices"]
    assert not language["is_default"]
