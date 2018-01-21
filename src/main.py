import logging
import pathlib
import pickle
import time

import save_parser
import timeline
import visualization

logging.basicConfig(level=logging.INFO)
BASE_SAVE_DIR = pathlib.Path("saves/lokkenmechanists_1256936305/")


class SaveReader:
    def __init__(self, game_dir):
        self.processed_saves = set()
        self.game_dir = game_dir

    def check_for_new_saves(self):
        for save_file in self.game_dir.glob("*.sav"):
            if save_file not in self.processed_saves:
                logging.info("Found new save file {}".format(save_file))
                self.processed_saves.add(save_file)
                parser = save_parser.SaveFileParser(save_file)
                yield parser.parse_save()


def visualize_results(tl):
    static_plot = visualization.StaticGalaxyInformationPlot(tl)
    static_plot.make_plot()


def main():
    sr = SaveReader(BASE_SAVE_DIR)
    tl = timeline.Timeline()

    while True:
        for gamestate in sr.check_for_new_saves():
            tl.add_data(gamestate)
            break
        time.sleep(3)

        break

    with open("output/test_timeline.pickle", "wb") as f:
        pickle.dump(tl, f)


def main2():
    with open("output/test_timeline.pickle", "rb") as f:
        tl = pickle.load(f)
    plotter = visualization.StaticGalaxyInformationPlot(tl.galaxy_data)
    plotter.make_plot()
    plotter.save_plot()


if __name__ == "__main__":
    # main()
    main2()
