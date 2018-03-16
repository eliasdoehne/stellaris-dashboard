import logging
import multiprocessing as mp
import threading
import time

from stellarisdashboard import cli, dash_server, save_parser, config

logger = logging.getLogger(__name__)


def main():
    mp.freeze_support()
    threads = config.CONFIG.threads
    t_save_monitor = threading.Thread(target=cli.f_monitor_saves, daemon=False, args=(threads, 10))
    t_save_monitor.start()
    t_dash = threading.Thread(target=dash_server.start_server, daemon=False, args=())
    t_dash.start()
    while True:
        try:
            # update the selected game when a save game from a different game is detected.
            # This is a workaround since the game selection dropdown
            # from dash does not seem to work in the in-game browser.
            time.sleep(5)
            if save_parser.MOST_RECENTLY_UPDATED_GAME is not None and save_parser.MOST_RECENTLY_UPDATED_GAME != dash_server.SELECTED_GAME_NAME:
                logger.debug("Updating selected game in dash!")
                logger.debug(save_parser.MOST_RECENTLY_UPDATED_GAME)
                dash_server.update_selected_game(save_parser.MOST_RECENTLY_UPDATED_GAME)
        except KeyboardInterrupt as e:
            break


if __name__ == '__main__':
    main()
