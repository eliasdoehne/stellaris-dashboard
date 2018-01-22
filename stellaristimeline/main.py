import logging
import multiprocessing as mp
import os.path
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


@click.group()
def cli():
    pass


@cli.command()
@click.argument('pickle_file_name', type=click.Path(exists=True, file_okay=True, dir_okay=False))
def visualize_results(pickle_file_name):
    f_visualize_results(pickle_file_name)


def f_visualize_results(pickle_file_name):
    gametimeline = load_timeline_from_pickle(pickle_file_name)
    static_plot = visualization.StaticGalaxyInformationPlot(next(iter(gametimeline.time_line.values())).galaxy_data)
    static_plot.make_plot()

    timeline_plot = visualization.EmpireProgressionPlot(gametimeline)
    timeline_plot.make_plot()
    timeline_plot.save_plot()

    static_plotter = visualization.StaticGalaxyInformationPlot(next(iter(gametimeline.time_line.values())).galaxy_data)
    static_plotter.make_plot()
    static_plotter.save_plot()


@cli.command()
@click.option('--threads', type=click.INT)
@click.option('--polling_interval', type=click.INT, default=5)
@click.argument('output_file', type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def monitor_for_new_saves(threads, polling_interval, output_file, save_path):
    save_reader = SaveReader(save_path, threads=threads)
    t = threading.Thread(target=f_monitor_for_new_saves, daemon=True, args=(save_reader, polling_interval, output_file))
    t.start()
    while True:
        exit = click.prompt("Press x and confirm with enter to exit the program")
        if exit == "x" or exit == "X":
            save_reader.running = False
            break
    t.join()


def f_monitor_for_new_saves(save_reader, polling_interval, output_file):
    if os.path.exists(output_file):
        gametimeline = load_timeline_from_pickle(output_file)
    else:
        gametimeline = timeline.Timeline()

    while True:
        try:
            for gamestateinfo in save_reader.check_for_new_saves():
                print(f"Processing gamestate {gamestateinfo}")
                if output_file is None:
                    filename_base = "_".join(gamestateinfo.game_name.lower().split())
                    output_file = pathlib.Path(f"output/timeline_{filename_base}.pickle")

                if gamestateinfo.date not in gametimeline.time_line:
                    gametimeline.add_data(gamestateinfo)
                    append_gamestateinfo_to_pickle(gamestateinfo, output_file)

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
@click.argument('output_pickle', type=click.Path(file_okay=True, dir_okay=False))
@click.argument('save_path', type=click.Path(exists=True, file_okay=False, dir_okay=True))
def parse_existing_saves(output_pickle, threads, save_path):
    f_parse_existing_saves(output_pickle, threads, save_path)


def f_parse_existing_saves(output_pickle, threads, save_path):
    sr = SaveReader(save_path, threads=threads)
    if os.path.exists(output_pickle):
        gametimeline = load_timeline_from_pickle(output_pickle)
    else:
        gametimeline = timeline.Timeline()

    print(f"Looking for new save files in {save_path}.")
    for gamestateinfo in sr.check_for_new_saves():
        if output_pickle is None:
            filename_base = "_".join(gamestateinfo.game_name.lower().split())
            output_pickle = pathlib.Path(f"output/timeline_{filename_base}.pickle")
        if gamestateinfo.date not in gametimeline.time_line:
            gametimeline.add_data(gamestateinfo)
            append_gamestateinfo_to_pickle(gamestateinfo, output_pickle)
