import pytest

from stellarisdashboard import config


@pytest.fixture(scope="session", autouse=True)
def initialize_config():
    config.initialize()
