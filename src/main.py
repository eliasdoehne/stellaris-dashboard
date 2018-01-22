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
    def __init__(self, game_dir, threads=4):
        self.processed_saves = set()
        self.game_dir = game_dir
        self.threads = threads
        # self.work_pool = mp.Pool(threads)

    def check_for_new_saves(self):
        for save_file in self.game_dir.glob("*.sav"):
            if save_file not in self.processed_saves:
                logging.info("Found new save file {}".format(save_file))
                filename, gamestateinfo = get_gamestateinfo_from_file(save_file)
                self.processed_saves.add(save_file)
                yield gamestateinfo


def visualize_results(tl):
    static_plot = visualization.StaticGalaxyInformationPlot(tl)
    static_plot.make_plot()


def main():
    sr = SaveReader(BASE_SAVE_DIR)
    pickle_file_name = None
    tl = None

    print(f"Looking for new save files in {BASE_SAVE_DIR}.")
    while True:
        for gamestateinfo in sr.check_for_new_saves():
            if pickle_file_name is None:
                pickle_file_name = '_'.join(gamestateinfo.game_name.lower().split())
                pickle_file_name = f"output/timeline_{pickle_file_name}.pickle"
                try:
                    with open(pickle_file_name, "rb") as f:
                        tl = pickle.load(f)
                except Exception:
                    tl = timeline.Timeline()
            tl.add_data(gamestateinfo)
        with open(pickle_file_name, "wb") as f:
            pickle.dump(tl, f)
        break
        time.sleep(5)


def main2():
    with open("output/timeline_lokken_mechanists.pickle", "rb") as f:
        tl = pickle.load(f)
    static_plotter = visualization.StaticGalaxyInformationPlot(next(iter(tl.time_line.values())).galaxy_data)
    static_plotter.make_plot()
    static_plotter.save_plot()

    timeline_plot = visualization.EmpireDemographicsPlot(tl)
    timeline_plot.make_plot()
    timeline_plot.save_plot()


if __name__ == "__main__":
    # main()
    main2()
