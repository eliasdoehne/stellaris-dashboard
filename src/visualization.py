import numpy as np
from matplotlib import pyplot as plt

import timeline


class StaticGalaxyInformationPlot:
    def __init__(self, galaxy_data_dict, plot_filename="./output/static_galaxy_plot.png"):
        self.galaxy_data = galaxy_data_dict
        self.fig = None
        self.axes = None
        self.plot_filename = plot_filename

    def make_plot(self):
        self.fig, self.axes = plt.subplots(4, 1, figsize=(12, 18))
        self.fig.suptitle("Distribution of Celestial Bodies")

        ax = self.axes[0]
        ax.set_title("Number of non-inhabitable \"planet\" objects")
        keys = [key for key in self.galaxy_data["planet_class_distribution"].keys() if key not in timeline.COLONIZABLE_PLANET_CLASSES]
        keys = sorted(keys, key=lambda x: self.galaxy_data["planet_class_distribution"][x])
        values = [self.galaxy_data["planet_class_distribution"].get(key, 0) for key in keys]
        ax.bar(range(len(values)), values, tick_label=keys)

        cmap = plt.get_cmap("viridis")
        color_dict = dict(zip(timeline.PLANET_CLIMATES, [cmap(x) for x in np.linspace(0, 1.0, len(timeline.PLANET_CLIMATES))]))

        ax = self.axes[1]
        ax.set_title("Number of inhabitable planet objects by planet climate")
        keys = [key for key in timeline.COLONIZABLE_PLANET_CLASSES if key in self.galaxy_data["planet_class_distribution"]]
        keys = sorted(keys, key=self._sort_planets_by_climate_and_frequency)

        print(keys)
        values = [self.galaxy_data["planet_class_distribution"].get(key, 0) for key in keys]
        colors = [color_dict[timeline.CLIMATE_CLASSIFICATION[key]] for key in keys]
        ax.bar(range(len(values)), values, tick_label=keys, color=colors)

        ax = self.axes[2]
        ax.set_title("Number of workable planet tiles by climate")
        keys = sorted(self.galaxy_data["planet_tiles_distribution"].keys(), key=self._sort_planets_by_climate_and_frequency)
        values = [self.galaxy_data["planet_tiles_distribution"].get(key, 0) for key in keys]
        colors = [color_dict[timeline.CLIMATE_CLASSIFICATION[key]] for key in keys]
        ax.bar(range(len(values)), values, tick_label=keys, color=colors)

        ax = self.axes[3]
        ax.set_title("Average planet size by climate")
        keys = [k for k in keys if k in self.galaxy_data["planet_tiles_distribution"]]
        values = [self.galaxy_data["planet_tiles_distribution"].get(key, 0) / self.galaxy_data["planet_class_distribution"].get(key, 0) for key in keys]
        colors = [color_dict[timeline.CLIMATE_CLASSIFICATION[key]] for key in keys]
        ax.bar(range(len(values)), values, tick_label=keys, color=colors)

    def _sort_planets_by_climate_and_frequency(self, planet_class):
        total_planet_count = sum(self.galaxy_data["planet_class_distribution"].values())
        return total_planet_count * timeline.PLANET_CLIMATES.index(timeline.CLIMATE_CLASSIFICATION[planet_class]) + self.galaxy_data["planet_class_distribution"].get(planet_class, 0)

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=300)


class EmpireDemographicsPlot:
    def __init__(self, gametimeline: timeline.Timeline, plot_filename="./output/empire_demographics_plot.png"):
        self.gametimeline = gametimeline
        self.fig = None
        self.axes = None
        self.plot_filename = plot_filename

    def make_plot(self):
        self.fig, self.axes = plt.subplots(2, 1, figsize=(16, 18))

        ax = self.axes[0]
        start_date = timeline.StellarisDate(2200, 1, 1)
        t_axis = np.zeros(len(self.gametimeline.time_line))
        pop_count_per_species = {}
        planet_count = np.zeros(t_axis.shape)
        tech_count = np.zeros(t_axis.shape)
        t_axis = np.zeros(t_axis.shape)
        t_axis = np.zeros(t_axis.shape)
        for i, (date, gamestateinfo) in enumerate(self.gametimeline.time_line.items()):
            t_axis[i] = date - start_date

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=300)
