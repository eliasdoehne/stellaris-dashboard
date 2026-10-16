import copy

import pytest
import yaml

from stellarisdashboard import config, legacy_config_defaults

OLD_MARKET_RESOURCES = legacy_config_defaults.LEGACY_MARKET_RESOURCES[0]
OLD_TAB_LAYOUTS = legacy_config_defaults.LEGACY_TAB_LAYOUTS


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """Run in an empty directory, so that config.yml is read from and written to tmp_path."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_settings(directory, **settings):
    # keep any output created by the config out of the real working directory
    settings.setdefault("base_output_path", str(directory / "output"))
    with open(directory / "config.yml", "w") as f:
        yaml.dump(settings, f, default_flow_style=False, sort_keys=False)


def _load_config():
    cfg = config.Config()
    config._apply_existing_settings(cfg)
    return cfg


def _read_written_settings(directory):
    with open(directory / "config.yml") as f:
        return yaml.safe_load(f)


def _comparable_layout(layout):
    return config._comparable_setting("tab_layout", layout)


def test_old_market_resources_are_ignored_on_load(settings_dir):
    assert OLD_MARKET_RESOURCES != config.DEFAULT_MARKET_RESOURCES
    _write_settings(settings_dir, market_resources=OLD_MARKET_RESOURCES)
    cfg = _load_config()
    assert cfg.market_resources == config.DEFAULT_MARKET_RESOURCES


@pytest.mark.parametrize("old_layout", OLD_TAB_LAYOUTS)
def test_old_tab_layouts_are_ignored_on_load(settings_dir, old_layout):
    _write_settings(settings_dir, tab_layout=old_layout)
    cfg = _load_config()
    assert _comparable_layout(cfg.tab_layout) == _comparable_layout(
        config.DEFAULT_TAB_LAYOUT
    )


def test_oldest_tab_layout_lacks_graphs_of_current_default():
    # sanity check of the test data: the stale layout really is stale
    assert _comparable_layout(OLD_TAB_LAYOUTS[0]) != _comparable_layout(
        config.DEFAULT_TAB_LAYOUT
    )


def test_old_layout_with_dynamic_markets_tab_filled_is_still_old(settings_dir):
    layout = copy.deepcopy(OLD_TAB_LAYOUTS[-1])
    layout[config.MARKET_TAB] = ["some_market_graph"]
    _write_settings(settings_dir, tab_layout=layout)
    cfg = _load_config()
    assert _comparable_layout(cfg.tab_layout) == _comparable_layout(
        config.DEFAULT_TAB_LAYOUT
    )


def test_all_legacy_defaults_are_recognized():
    for layout in OLD_TAB_LAYOUTS:
        assert config._is_legacy_default("tab_layout", layout)
    for resources in legacy_config_defaults.LEGACY_MARKET_RESOURCES:
        assert config._is_legacy_default("market_resources", resources)


def test_customized_market_resources_and_tab_layout_are_kept_and_persisted(
    settings_dir,
):
    custom_resources = [{"name": "energy", "base_price": 1}]
    custom_layout = {
        "Budget": ["energy_budget"],
        "Economy": ["net_energy_income_graph"],
    }
    _write_settings(
        settings_dir, market_resources=custom_resources, tab_layout=custom_layout
    )
    cfg = _load_config()
    assert cfg.market_resources == custom_resources
    assert cfg.tab_layout == custom_layout

    cfg.write_to_file()
    written = _read_written_settings(settings_dir)
    assert written["market_resources"] == custom_resources
    assert written["tab_layout"] == custom_layout

    reloaded = _load_config()
    assert reloaded.market_resources == custom_resources
    assert reloaded.tab_layout == custom_layout


def test_reordered_tab_layout_is_a_customization(settings_dir):
    default = config.DEFAULT_TAB_LAYOUT
    reordered = {tab: default[tab] for tab in reversed(list(default))}
    _write_settings(settings_dir, tab_layout=reordered)
    cfg = _load_config()
    assert list(cfg.tab_layout) == list(reordered)
    cfg.write_to_file()
    assert list(_read_written_settings(settings_dir)["tab_layout"]) == list(reordered)


def test_defaults_are_not_written_to_file(settings_dir):
    cfg = _load_config()
    cfg.write_to_file()
    written = _read_written_settings(settings_dir)
    assert "market_resources" not in written
    assert "tab_layout" not in written
    # other settings are still persisted
    assert written["port"] == config.DEFAULT_SETTINGS["port"]
    assert "market_fee" in written


def test_old_defaults_are_removed_from_file_on_next_write(settings_dir):
    _write_settings(
        settings_dir,
        market_resources=OLD_MARKET_RESOURCES,
        tab_layout=OLD_TAB_LAYOUTS[0],
        port=12345,
    )
    cfg = _load_config()
    cfg.write_to_file()
    written = _read_written_settings(settings_dir)
    assert "market_resources" not in written
    assert "tab_layout" not in written
    assert written["port"] == 12345


def test_settings_page_write_path_keeps_defaults_out_of_file(settings_dir):
    # the settings page calls apply_dict() with the previous tab_layout, then write_to_file()
    cfg = _load_config()
    previous = cfg.get_adjustable_settings_dict()
    cfg.apply_dict(
        {
            "port": 4321,
            "tab_layout": previous["tab_layout"],
            "market_fee": previous["market_fee"],
        }
    )
    cfg.write_to_file()
    written = _read_written_settings(settings_dir)
    assert written["port"] == 4321
    assert "market_resources" not in written
    assert "tab_layout" not in written


def test_default_dicts_are_unchanged_by_loading_and_writing(settings_dir):
    layout_before = copy.deepcopy(config.DEFAULT_TAB_LAYOUT)
    resources_before = copy.deepcopy(config.DEFAULT_MARKET_RESOURCES)
    _write_settings(settings_dir, tab_layout=OLD_TAB_LAYOUTS[0])
    cfg = _load_config()
    cfg.write_to_file()
    assert config.DEFAULT_TAB_LAYOUT == layout_before
    assert config.DEFAULT_MARKET_RESOURCES == resources_before
