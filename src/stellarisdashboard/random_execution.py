import logging
import pathlib
import threading
import time

from stellarisdashboard import cli, dash_server

STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"
logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    THREADS = 8
    # cli.f_parse_saves(THREADS)
    cli.f_visualize_mpl("xanidsuzerainty_-1935780457", show_everything=True)

    # save_reader = cli.SaveReader(STELLARIS_SAVE_DIR, threads=THREADS)
    # stop_event = threading.Event()
    # t_save_monitor = threading.Thread(target=cli._monitor_saves, daemon=False, args=(stop_event, save_reader, 10))
    # t_save_monitor.start()
    # t_dash = threading.Thread(target=dash_server.start_server, daemon=False, args=())
    # t_dash.start()
    # while True:
    #     time.sleep(5)
    #     if cli.MOST_RECENTLY_UPDATED_GAME is not None and cli.MOST_RECENTLY_UPDATED_GAME != dash_server.SELECTED_GAME_NAME:
    #         print("Updating selected game in dash:")
    #         print(cli.MOST_RECENTLY_UPDATED_GAME)
    #         dash_server.update_selected_game(cli.MOST_RECENTLY_UPDATED_GAME)

    # f_parse_saves(6)
    # f_visualize_mpl("saathidmandate2_-896351359", show_everything=True)
    # f_visualize_mpl("alvanianholypolity_1520526598", show_everything=True)
    # f_visualize_mpl("neboritethrong_-145199095", show_everything=True)
    # f_visualize_mpl("saathidmandate_-1898185517", show_everything=True)
    # f_visualize_mpl("alariunion2_361012875", show_everything=True)

    # main.f_visualize_mpl("unitednationsofearth_-15512622", show_everything=True)

    # while True:
    #     f_visualize_mpl("unitednationsofearth_-15512622", show_everything=True)
    #     time.sleep(30)

    # import models
    # print(sorted(models.PopEthics))
