import pytest

from stellarisdashboard import config as dashboard_config


@pytest.fixture(scope="session", autouse=True)
def initialize_config():
    dashboard_config.initialize()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "skip_github_actions: mark tests to only run locally"
    )
