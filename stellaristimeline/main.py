import logging
import multiprocessing as mp
import os.path
import pathlib
import pickle
import re
import threading
import time

import click

from stellaristimeline import save_parser, timeline, visualization, models

logging.basicConfig(level=logging.INFO)

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
STELLARIS_SAVE_DIR = pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"


def get_gamestateinfo_from_file(filename):
    parser = save_parser.SaveFileParser(filename)
    gamestateinfo = timeline.GameStateInfo()
    gamestateinfo.initialize(parser.parse_save())
    return filename, gamestateinfo


class SaveReader:
    def __init__(self, game_dir, threads=None):
        self.processed_saves = set()
        self.game_dir = pathlib.Path(game_dir)
        if threads is None:
            threads = max(1, mp.cpu_count() - 2)
        self.threads = threads
        self.work_pool = None
        self.running = True
        if self.threads > 1:
            self.work_pool = mp.Pool(threads)

    def check_for_new_saves(self):
        if not self.running:
            return
        new_files = [save_file for save_file in self.game_dir.glob("*.sav") if save_file not in self.processed_saves]
        if self.threads > 1:
            results = self.work_pool.map(get_gamestateinfo_from_file, new_files)
        else:
            results = [get_gamestateinfo_from_file(save_file) for save_file in new_files]
        for result in results:
            try:
                filename, gamestateinfo = result
                self.processed_saves.add(filename)
                yield gamestateinfo
            except Exception as e:
                logging.error(f"Exception {e} occured")
                pass

    def teardown(self):
        self.work_pool.close()
        self.work_pool.join()


@click.group()
def cli():
    pass


@cli.command()
@click.option('--pickle', type=click.Path(exists=True, file_okay=True, dir_okay=False))
def visualize(pickle):
    f_visualize(pickle)


def f_visualize(pickle_file_name):
    if pickle_file_name is not None:
        pickles = [pathlib.Path(pickle_file_name)]
    else:
        pickles = pathlib.Path("output/pickles/").glob("*.pickle")
    for pickle_file in pickles:
        gametimeline = load_timeline_from_pickle(pickle_file)
        galaxy_data = next(iter(gametimeline.time_line.values())).galaxy_data

        out_dir = BASE_DIR / pathlib.Path(f"output/{pickle_file.stem}/")
        print(out_dir.resolve())
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        static_plot = visualization.StaticGalaxyInformationPlot(galaxy_data, plot_filename=f"{out_dir}/galaxy.png")
        static_plot.make_plot()
        static_plot.save_plot()

        timeline_plot = visualization.EmpireProgressionPlot(gametimeline, plot_filename=f"{out_dir}/empires.png")
        timeline_plot.make_plot()
        timeline_plot.save_plot()


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--polling_interval', type=click.INT, default=5)
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def monitor_saves(save_path, threads, polling_interval):
    save_reader = SaveReader(save_path, threads=threads)
    t = threading.Thread(target=f_monitor_saves, daemon=True, args=(save_reader, polling_interval))
    t.start()
    while True:
        exit_prompt = click.prompt("Press x and confirm with enter to exit the program")
        if exit_prompt == "x" or exit_prompt == "X":
            save_reader.running = False
            break
    t.join()


def f_monitor_saves(save_reader, polling_interval):
    output_pickle = get_pickle_path(save_reader.game_dir)
    gametimeline = initialize_timeline(output_pickle)
    while True:
        try:
            for gamestateinfo in save_reader.check_for_new_saves():
                print(f"Processing gamestate {gamestateinfo}")
                if gamestateinfo.date not in gametimeline.time_line:
                    gametimeline.add_data(gamestateinfo)
                    append_gamestateinfo_to_pickle(gamestateinfo, output_pickle)

                if not save_reader.running:
                    break
        except Exception as e:
            logging.error(e)
            break
        if not save_reader.running:
            break
        time.sleep(polling_interval)
    save_reader.teardown()


@cli.command()
@click.option('--threads', type=click.INT)
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def parse_saves(save_path, threads):
    f_parse_saves(save_path, threads)


def f_parse_saves(save_path, threads=None):
    output_pickle = get_pickle_path(save_path)
    print(output_pickle)
    sr = SaveReader(save_path, threads=threads)
    gametimeline = initialize_timeline(output_pickle)

    print(f"Looking for new save files in {save_path}.")
    for gamestateinfo in sr.check_for_new_saves():
        if gamestateinfo.date not in gametimeline.time_line:
            gametimeline.add_data(gamestateinfo)
            append_gamestateinfo_to_pickle(gamestateinfo, output_pickle)


def initialize_timeline(output_pickle):
    if os.path.exists(output_pickle):
        gametimeline = load_timeline_from_pickle(output_pickle)
    else:
        gametimeline = timeline.Timeline()
    return gametimeline


def get_pickle_path(save_path):
    game_id = pathlib.Path(save_path).stem
    assert re.fullmatch(r'[a-z]+[0-9]*_-?[0-9]+', game_id) is not None
    pickle_dir = "output/pickles/"
    pickle_file = pathlib.Path(f"{pickle_dir}/{game_id}.pickle")
    if not os.path.exists(pickle_dir):
        os.makedirs(pickle_dir)
    return pickle_file


def load_timeline_from_pickle(pickle_file):
    gametimeline = timeline.Timeline()
    with open(pickle_file, "rb") as f:
        while True:
            try:
                gameinfo = pickle.load(f)
                gametimeline.add_data(gameinfo)
            except EOFError:
                break
    return gametimeline


def append_gamestateinfo_to_pickle(gamestateinfo, pickle_file):
    with open(pickle_file, "ab") as f:
        pickle.dump(gamestateinfo, f)
