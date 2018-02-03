import logging
import multiprocessing as mp
import pathlib
import threading

import click

from stellaristimeline import save_parser, timeline, visualization, models

logging.basicConfig(level=logging.INFO)

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"


class SaveReader:
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
        self.tle = timeline.TimelineExtractor()

    def check_for_new_saves(self):
        new_files = [save_file for save_file in self.game_dir.glob("*.sav")
                     if save_file not in self.processed_saves
                     and "ironman" not in str(save_file)]
        self.processed_saves.update(new_files)
        if self.threads > 1:
            results = self.work_pool.map(save_parser.parse_save, new_files)
        else:
            results = [save_parser.parse_save(save_file) for save_file in new_files]
        for gamestate_dict in results:
            self.tle.process_gamestate(self.game_name, gamestate_dict)

    def mark_all_existing_saves_processed(self):
        self.processed_saves |= set(self.game_dir.glob("*.sav"))

    def teardown(self):
        self.work_pool.close()
        self.work_pool.join()


@click.group()
def cli():
    pass


@cli.command()
@click.argument('game_name', type=click.STRING)
def visualize(game_name):
    session = models.SessionFactory()
    plot = visualization.EmpireProgressionPlot()
    try:
        player_country = None
        game: models.Game = session.query(models.Game).filter_by(game_name=game_name).first()
        for gs in game.game_states:
            for cs in gs.country_states:
                if cs.is_player:
                    player_country = cs.country_name
                    break
            if player_country:
                break
        plot.make_plot(game, player_country)

    except Exception as e:
        raise e
    finally:
        session.close()
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
    save_reader.running = False
    t.join()


def _monitor_saves(stop_event: threading.Event, save_reader: SaveReader, polling_interval: float):
    # save_reader.mark_all_existing_saves_processed()
    wait_time = 0.01
    while not stop_event.wait(wait_time):
        wait_time = polling_interval
        try:
            save_reader.check_for_new_saves()
        except Exception as e:
            logging.error(e)
            break
    save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def parse_saves(save_path, threads):
    f_parse_saves(save_path, threads)


def f_parse_saves(save_path, threads=None):
    sr = SaveReader(save_path, threads=threads)
    sr.check_for_new_saves()
