from matplotlib import pyplot as plt


class StaticGalaxyInformationPlot:
    def __init__(self, galaxy_data_dict, plot_filename="./output/static_galaxy_plot.png"):
        self.galaxy_data = galaxy_data_dict
        self.fig = None
        self.axes = None
        self.plot_filename = plot_filename

    def make_plot(self):
        self.fig, self.axes = plt.subplots(2, 1, figsize=(16, 18))

        ax = self.axes[0]
        keys = list(self.galaxy_data["planet_type_distribution"].keys())
        values = list(self.galaxy_data["planet_type_distribution"].values())
        ax.bar(keys, values)

        ax = self.axes[1]
        keys = list(self.galaxy_data["planet_tiles_distribution"].keys())
        values = list(self.galaxy_data["planet_tiles_distribution"].values())
        ax.bar(keys, values)

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=300)
