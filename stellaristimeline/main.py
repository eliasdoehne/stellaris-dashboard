import logging
import multiprocessing as mp
import pathlib
import threading
import time
import traceback
from typing import Any, Dict, Tuple

import click

from stellaristimeline import save_parser, timeline, visualization, models

logging.basicConfig(level=logging.INFO)

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"


class SaveReader:
    """
    Check the save path for new save games. Found save files are parsed and returned
    as gamestate dictionaries.
    """

    def __init__(self, game_dir, threads=None):
        self.processed_saves = set()
        self.game_dir = pathlib.Path(game_dir)
        if threads is None:
            threads = max(1, mp.cpu_count() - 2)
        self.threads = threads
        self.work_pool = None
        if self.threads > 1:
            self.work_pool = mp.Pool(threads)

    def check_for_new_saves(self) -> Tuple[str, Dict[str, Any]]:
        new_files = [save_file for save_file in self.game_dir.glob("**/*.sav")
                     if save_file not in self.processed_saves
                     and "ironman" not in str(save_file)]
        self.processed_saves.update(new_files)
        if self.threads > 1:
            results = [(f.parent.stem, self.work_pool.apply_async(save_parser.parse_save, (f,))) for f in new_files]
            while results:
                for game_name, r in results:
                    if r.ready():
                        if r.successful():
                            yield game_name, r.get()
                        else:
                            try:
                                r.get()
                            except Exception as e:
                                print(e)
                results = [(gn, r) for (gn, r) in results if not r.ready()]
                time.sleep(0.1)
        else:
            for save_file in new_files:
                yield save_file.parent.stem, save_parser.parse_save(save_file)

    def mark_all_existing_saves_processed(self):
        self.processed_saves |= set(self.game_dir.glob("**/*.sav"))

    def teardown(self):
        if self.threads > 1:
            self.work_pool.close()
            self.work_pool.join()


@click.group()
def cli():
    pass


@cli.command()
@click.argument('game_name', type=click.STRING)
def visualize(game_name):
    f_visualize_mpl(game_name)


def f_visualize_mpl(game_name: str):
    session = models.SessionFactory()
    plot_data = visualization.EmpireProgressionPlotData(game_name)
    try:
        game: models.Game = session.query(models.Game).filter_by(game_name=game_name).first()
        plot_data.initialize(game)
    except Exception as e:
        raise e
    finally:
        session.close()
    plot = visualization.MatplotLibVisualization(plot_data)
    plot.make_plots()
    plot.save_plot()


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--polling_interval', type=click.INT, default=1)
def monitor_saves(threads, polling_interval):
    f_monitor_saves(threads, polling_interval)


def f_monitor_saves(threads, polling_interval):
    save_reader = SaveReader(STELLARIS_SAVE_DIR, threads=threads)
    stop_event = threading.Event()
    t = threading.Thread(target=_monitor_saves, daemon=True, args=(stop_event, save_reader, polling_interval))
    t.start()
    while True:
        exit_prompt = click.prompt("Press x and confirm with enter to exit the program")
        if exit_prompt == "x" or exit_prompt == "X":
            stop_event.set()
            break
    save_reader.teardown()
    t.join()


def _monitor_saves(stop_event: threading.Event, save_reader: SaveReader, polling_interval: float):
    save_reader.mark_all_existing_saves_processed()
    wait_time = 0
    tle = timeline.TimelineExtractor()
    show_waiting_message = True
    while not stop_event.wait(wait_time):
        wait_time = polling_interval
        try:
            for game_name, gamestate_dict in save_reader.check_for_new_saves():
                show_waiting_message = True
                tle.process_gamestate(game_name, gamestate_dict)
                plot_data = visualization.get_current_execution_plot_data(game_name)
                plot_data.initialize()
                plot_data.update_with_new_gamestate()
            if show_waiting_message:
                show_waiting_message = False
                logging.info("Waiting for new saves...")
        except Exception as e:
            traceback.print_exc()
            logging.error(e)
            break
    save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
def parse_saves(threads):
    f_parse_saves(threads)


def f_parse_saves(threads=None):
    save_reader = SaveReader(STELLARIS_SAVE_DIR, threads=threads)
    tle = timeline.TimelineExtractor()
    for game_name, gamestate in save_reader.check_for_new_saves():
        tle.process_gamestate(game_name, gamestate)
        pass


if __name__ == '__main__':
    # f_parse_saves("saves/blargel/", threads=1)
    # f_visualize_mpl("saathidmandate2_-896351359")

    f_monitor_saves(8, 1)

    # while True:
    #     f_visualize("alariunion2_361012875")
    #     time.sleep(30)
