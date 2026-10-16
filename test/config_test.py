from stellarisdashboard import config


def test_obsolete_setting_is_ignored():
    # Settings files written by older versions can still contain removed keys
    config.initialize()
    settings = dict(config.CONFIG.get_dict(), read_all_countries=False)
    config.CONFIG.apply_dict(settings)
    assert not hasattr(config.CONFIG, "read_all_countries")
    assert "read_all_countries" not in config.CONFIG.get_dict()
