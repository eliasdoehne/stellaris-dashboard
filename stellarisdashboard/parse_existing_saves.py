import logging
import multiprocessing as mp

from stellarisdashboard import cli, config

logger = logging.getLogger(__name__)


def main():
    """Entry point for the default execution:

    1. Start the flask server which hosts the visualizations
    2. Begin monitoring for new save files
    """
    try:
        cli.f_parse_saves()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")


if __name__ == "__main__":
    mp.freeze_support()
    config.initialize()
    main()
