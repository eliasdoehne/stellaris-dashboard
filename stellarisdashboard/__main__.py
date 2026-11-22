import multiprocessing as mp

# In a frozen build the import worker process runs this script again. Handle that before the heavy
# imports below, so the worker starts quickly and without loading the web server. Otherwise a no-op.
mp.freeze_support()

from stellarisdashboard import _force_imports

_force_imports.force_imports()
import logging
import socket
import threading
import time
import webbrowser

from stellarisdashboard import cli, config
from stellarisdashboard.dashboard_app import start_server

logger = logging.getLogger(__name__)


def dashboard_url(host: str, port: int) -> str:
    """The address of the dashboard in a browser on this computer."""
    if host in ("", "0.0.0.0", "::"):
        host = "localhost"
    elif ":" in host:
        host = f"[{host}]"  # IPv6 address
    return f"http://{host}:{port}/"


def open_browser_when_ready(host: str, port: int, timeout: float = 30.0):
    """Open the dashboard in the default browser once the server accepts connections. Never raises."""
    try:
        url = dashboard_url(host, port)
        connect_host = "localhost" if host in ("", "0.0.0.0", "::") else host
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((connect_host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.25)
        else:
            logger.warning(f"Not opening the browser: the dashboard did not start within {timeout:.0f} seconds")
            return
        logger.info(f"Opening {url} in the browser")
        webbrowser.open(url)
    except Exception:
        logger.warning("Could not open the dashboard in the browser", exc_info=True)


def main():
    """Entry point for the default execution:

    1. Start the flask server which hosts the visualizations
    2. Begin monitoring for new save files
    """
    stop_event = threading.Event()
    t_server = threading.Thread(target=start_server, daemon=True)
    try:
        t_server.start()
        if config.CONFIG.open_browser:
            threading.Thread(
                target=open_browser_when_ready,
                args=(config.CONFIG.host, config.CONFIG.port),
                name="open-browser",
                daemon=True,
            ).start()
        cli.f_monitor_saves(stop_event=stop_event)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        stop_event.set()


if __name__ == "__main__":
    mp.freeze_support()
    main()
