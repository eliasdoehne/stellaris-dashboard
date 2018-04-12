import logging
import multiprocessing as mp
import threading

from stellarisdashboard import cli, dash_server, config

logger = logging.getLogger(__name__)


def main():
    threads = config.CONFIG.threads
    polling_interval = 0.5
    stop_event = threading.Event()
    t_save_monitor = threading.Thread(target=cli.f_monitor_saves,
                                      args=(threads, polling_interval),
                                      kwargs=dict(stop_event=stop_event))
    t_save_monitor.start()
    try:
        dash_server.start_server()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        stop_event.set()
        t_save_monitor.join()


if __name__ == '__main__':
    mp.freeze_support()
    main()
