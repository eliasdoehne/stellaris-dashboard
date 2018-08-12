import logging
import multiprocessing as mp
import threading

from stellarisdashboard import cli, dash_server

logger = logging.getLogger(__name__)


def main():
    polling_interval = 0.5
    stop_event = threading.Event()
    t_server = threading.Thread(target=dash_server.start_server, daemon=True)
    try:
        t_server.start()
        cli.f_monitor_saves(polling_interval, stop_event=stop_event)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        stop_event.set()


if __name__ == '__main__':
    mp.freeze_support()
    main()
