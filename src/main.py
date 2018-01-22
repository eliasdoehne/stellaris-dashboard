import logging
import pathlib
import pickle
import time
import multiprocessing as mp

import save_parser
import timeline
import visualization

logging.basicConfig(level=logging.INFO)
BASE_SAVE_DIR = pathlib.Path("saves/lokkenmechanists_1256936305/")


# BASE_SAVE_DIR = pathlib.Path("/home/elias/.local/share/Paradox Interactive/Stellaris/save games/")


def get_gamestateinfo_from_file(filename):
    parser = save_parser.SaveFileParser(filename)
    gamestateinfo = timeline.GameStateInfo()
    gamestateinfo.initialize(parser.parse_save())
    return filename, gamestateinfo


class SaveReader:
    def __init__(self, game_dir, threads=None):
        self.processed_saves = set()
        self.game_dir = game_dir
        if threads is None:
            threads = max(1, mp.cpu_count() - 2)
        self.threads = threads
        self.work_pool = None
        if self.threads > 1:
            self.work_pool = mp.Pool(threads)

    def check_for_new_saves(self):
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


def visualize_results(tl):
    static_plot = visualization.StaticGalaxyInformationPlot(tl)
    static_plot.make_plot()


def main(pickle_file_name=None):
    sr = SaveReader(BASE_SAVE_DIR)
    if pickle_file_name is not None:
        logging.info(f"Loading existing timeline {pickle_file_name}")
        with open(pickle_file_name, "rb") as f:
            gametimeline = pickle.load(f)
    else:
        gametimeline = timeline.Timeline()
    print(f"Looking for new save files in {BASE_SAVE_DIR}.")
    while True:
        found_new_saves = False
        for gamestateinfo in sr.check_for_new_saves():
            if pickle_file_name is None:
                filename_base = "_".join(gamestateinfo.game_name.lower().split())
                pickle_file_name = pathlib.Path(f"output/timeline_{filename_base}.pickle")
            found_new_saves = True
            gametimeline.add_data(gamestateinfo)
        if found_new_saves:
            with open(pickle_file_name, "wb") as f:
                logging.info(f"Saving timeline to {pickle_file_name}")
                pickle.dump(gametimeline, f)
        break
        time.sleep(5)


def main2():
    with open("output/timeline_lokken_mechanists.pickle", "rb") as f:
        gametimeline = pickle.load(f)
    static_plotter = visualization.StaticGalaxyInformationPlot(next(iter(gametimeline.time_line.values())).galaxy_data)
    static_plotter.make_plot()
    static_plotter.save_plot()

    timeline_plot = visualization.EmpireDemographicsPlot(gametimeline)
    timeline_plot.make_plot()
    timeline_plot.save_plot()


if __name__ == "__main__":
    # main(pickle_file_name="output/timeline_lokken_mechanists.pickle")
    main()
    main2()
