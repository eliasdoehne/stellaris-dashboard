import socket

import pytest

from stellarisdashboard import __main__ as dashboard_main
from stellarisdashboard import config


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "http://127.0.0.1:28053/"),
        ("0.0.0.0", "http://localhost:28053/"),
        ("", "http://localhost:28053/"),
        ("::1", "http://[::1]:28053/"),
        ("my-pc", "http://my-pc:28053/"),
    ],
)
def test_dashboard_url(host, expected):
    assert dashboard_main.dashboard_url(host, 28053) == expected


def test_opens_the_browser_once_the_server_listens(monkeypatch):
    opened = []
    monkeypatch.setattr(dashboard_main.webbrowser, "open", opened.append)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        dashboard_main.open_browser_when_ready("127.0.0.1", port, timeout=5)
    assert opened == [f"http://127.0.0.1:{port}/"]


def test_does_not_open_the_browser_if_the_server_does_not_start(monkeypatch):
    opened = []
    monkeypatch.setattr(dashboard_main.webbrowser, "open", opened.append)
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    dashboard_main.open_browser_when_ready("127.0.0.1", port, timeout=0.3)
    assert opened == []


def test_browser_errors_are_not_raised(monkeypatch):
    def fail(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(dashboard_main.webbrowser, "open", fail)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        dashboard_main.open_browser_when_ready("127.0.0.1", server.getsockname()[1], timeout=5)


def test_open_browser_is_off_by_default():
    assert config.DEFAULT_SETTINGS["open_browser"] is False
    assert config.SETTINGS["open_browser"].category == "interface"
