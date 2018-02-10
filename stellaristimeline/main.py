import logging
import multiprocessing as mp
import pathlib
import threading
import time
import traceback

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
        self.game_name = self.game_dir.stem
        logging.info(f"parsing files in {self.game_dir} for game \"{self.game_name}\"")
        if threads is None:
            threads = max(1, mp.cpu_count() - 2)
        self.threads = threads
        self.work_pool = None
        if self.threads > 1:
            self.work_pool = mp.Pool(threads)

    def check_for_new_saves(self):
        new_files = [save_file for save_file in self.game_dir.glob("*.sav")
                     if save_file not in self.processed_saves
                     and "ironman" not in str(save_file)]
        self.processed_saves.update(new_files)
        if self.threads > 1:
            results = [self.work_pool.apply_async(save_parser.parse_save, (f,)) for f in new_files]
            while results:
                for r in results:
                    if r.ready():
                        if r.successful():
                            gamestate_dict = r.get()
                            yield gamestate_dict
                        else:
                            try:
                                r.get()
                            except Exception as e:
                                print(e)
                results = [r for r in results if not r.ready()]
                time.sleep(0.1)
        else:
            for save_file in new_files:
                yield save_parser.parse_save(save_file)

    def mark_all_existing_saves_processed(self):
        self.processed_saves |= set(self.game_dir.glob("*.sav"))

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


def f_visualize_mpl(game_name):
    session = models.SessionFactory()
    plot_data = visualization.EmpireProgressionPlotData()
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
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def monitor_saves(save_path, threads, polling_interval):
    save_reader = SaveReader(save_path, threads=threads)
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
    wait_time = 0.01
    tle = timeline.TimelineExtractor()
    show_waiting_message = True
    while not stop_event.wait(wait_time):
        wait_time = polling_interval
        try:
            for gamestate_dict in save_reader.check_for_new_saves():
                show_waiting_message = True
                game_state = tle.process_gamestate(save_reader.game_name, gamestate_dict)
                if game_state is not None:
                    plot_data = visualization.get_current_execution_plot_data(game_state.game)
                    plot_data.process_gamestate(game_state)
            if show_waiting_message:
                show_waiting_message = False
                print("Waiting for new saves...")
        except Exception as e:
            traceback.print_exc()
            logging.error(e)
            break
    save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def parse_saves(save_path, threads):
    f_parse_saves(save_path, threads)


def f_parse_saves(save_path, threads=None):
    save_reader = SaveReader(save_path, threads=threads)
    tle = timeline.TimelineExtractor()
    for gamestate in save_reader.check_for_new_saves():
        tle.process_gamestate(save_reader.game_name, gamestate)
        pass


if __name__ == '__main__':
    # f_parse_saves("saves/blargel/", threads=1)
    f_visualize_mpl("saathidmandate2_-896351359")

    # while True:
    #     f_visualize("alariunion2_361012875")
    #     time.sleep(30)
