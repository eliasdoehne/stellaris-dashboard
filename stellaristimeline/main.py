import logging
import multiprocessing as mp
import pathlib
import pickle
import threading
import time

import click

from stellaristimeline import save_parser, timeline, visualization

logging.basicConfig(level=logging.INFO)


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
@click.argument('pickle_file', type=click.Path(exists=True, file_okay=True, dir_okay=False))
def visualize_results(pickle_file_name):
    f_visualize_results(pickle_file_name)


def f_visualize_results(pickle_file_name):
    with open(pickle_file_name, "rb") as f:
        gametimeline = pickle.load(f)
    static_plot = visualization.StaticGalaxyInformationPlot(gametimeline)
    static_plot.make_plot()

    timeline_plot = visualization.EmpireProgressionPlot(gametimeline)
    timeline_plot.make_plot()
    timeline_plot.save_plot()

    static_plotter = visualization.StaticGalaxyInformationPlot(next(iter(gametimeline.time_line.values())).galaxy_data)
    static_plotter.make_plot()
    static_plotter.save_plot()


@cli.command()
@click.option('--pickle_file_name', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option('--threads', type=click.INT)
@click.option('--polling_interval', type=click.INT, default=1)
@click.argument('output_file', type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def monitor_for_new_saves(pickle_file_name, output_file, threads, polling_interval, save_path):
    sr = SaveReader(save_path, threads=threads)
    if pickle_file_name is not None:
        logging.info(f"Loading existing timeline {pickle_file_name}")
        with open(pickle_file_name, "rb") as f:
            gametimeline = pickle.load(f)
    else:
        gametimeline = timeline.Timeline()

    t = threading.Thread(target=f_monitor_for_new_saves, daemon=True, args=(sr, gametimeline, output_file, polling_interval))
    t.start()
    while True:
        exit = click.prompt("Press x and confirm with enter to exit the program")
        if exit == "x" or exit == "X":
            sr.running = False
            break
    t.join()
    f_monitor_for_new_saves(sr, gametimeline, output_file, polling_interval)


def f_monitor_for_new_saves(sr, gametimeline, output_file, polling_interval):
    print("Looking for save files")
    while True:
        for gamestateinfo in sr.check_for_new_saves():
            print(f"Processing gamestate {gamestateinfo}")
            if output_file is None:
                filename_base = "_".join(gamestateinfo.game_name.lower().split())
                output_file = pathlib.Path(f"output/timeline_{filename_base}.pickle")
            gametimeline.add_data(gamestateinfo)
            if not sr.running:
                break
        if not sr.running:
            break
        time.sleep(polling_interval)
    sr.teardown()
    logging.info(f"Saving timeline to {output_file}")
    with open(output_file, "wb") as f:
        pickle.dump(gametimeline, f)


@cli.command()
@click.option('--pickle_file_name', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option('--threads', type=click.INT)
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def parse_existing_saves(pickle_file_name, threads, save_path):
    f_parse_existing_saves(pickle_file_name, threads, save_path)


def f_parse_existing_saves(pickle_file_name, threads, save_path):
    sr = SaveReader(save_path, threads=threads)
    if pickle_file_name is not None:
        logging.info(f"Loading existing timeline {pickle_file_name}")
        with open(pickle_file_name, "rb") as f:
            gametimeline = pickle.load(f)
    else:
        gametimeline = timeline.Timeline()
    print(f"Looking for new save files in {save_path}.")
    for gamestateinfo in sr.check_for_new_saves():
        if pickle_file_name is None:
            filename_base = "_".join(gamestateinfo.game_name.lower().split())
            pickle_file_name = pathlib.Path(f"output/timeline_{filename_base}.pickle")
        gametimeline.add_data(gamestateinfo)
    with open(pickle_file_name, "wb") as f:
        logging.info(f"Saving timeline to {pickle_file_name}")
        pickle.dump(gametimeline, f)
