import atexit
import os
import pathlib
import shutil
import tempfile

import pytest

# Keep the tests away from the settings and output folder of the working directory:
# the settings are read from (and only ever written to) a temporary folder.
_TEST_DIR = pathlib.Path(tempfile.mkdtemp(prefix="stellaris-dashboard-test-"))
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
os.environ["STELLARIS_DASHBOARD_CONFIG"] = str(_TEST_DIR / "config.yml")

from stellarisdashboard import config as dashboard_config  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def initialize_config():
    dashboard_config.initialize()
    dashboard_config.CONFIG.base_output_path = _TEST_DIR / "output"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "skip_github_actions: mark tests to only run locally"
    )
