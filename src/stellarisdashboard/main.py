import logging
import pathlib
import threading

import time

from stellardashboard import cli, dash_server, visualization

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"
logging.basicConfig(level=logging.INFO, filename=BASE_DIR / "stellaris_dashboard.log")

logger = logging.getLogger(__name__)


def main():
    save_reader = cli.SaveReader(STELLARIS_SAVE_DIR, threads=4)
    stop_event = threading.Event()
    t_save_monitor = threading.Thread(target=cli._monitor_saves, daemon=False, args=(stop_event, save_reader, 10))
    t_save_monitor.start()
    t_dash = threading.Thread(target=dash_server.start_server, daemon=False, args=())
    t_dash.start()
    while True:
        time.sleep(5)
        if cli.MOST_RECENTLY_UPDATED_GAME is not None and cli.MOST_RECENTLY_UPDATED_GAME != dash_server.SELECTED_GAME_NAME:
            print("Updating selected game in dash!")
            print(cli.MOST_RECENTLY_UPDATED_GAME)
            dash_server.update_selected_game(cli.MOST_RECENTLY_UPDATED_GAME)


if __name__ == '__main__':
    main()
