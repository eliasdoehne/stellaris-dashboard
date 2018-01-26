import numpy as np
from matplotlib import pyplot as plt

from stellaristimeline import timeline, models

COLOR_MAP = plt.get_cmap("viridis")


class EmpireProgressionPlot:
    def __init__(self, gametimeline: timeline.Timeline, plot_filename="./output/empire_demographics_plot.png"):
        self.gametimeline = gametimeline
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_filename = plot_filename
        self.t_axis = None
        self.start_date = timeline.StellarisDate(2200, 1, 1)

    def make_plot(self):
        self.initialize_axes()
        self.pop_count_plot()
        self.planet_count_plot()
        self.tech_count_plot()
        self.exploration_progress_plot()
        self.empire_demographics_plot()
        self.military_strength_plot()
        self.fleet_size_plot()

    def initialize_axes(self):
        self.fig, self.axes = plt.subplots(3, 3, figsize=(40, 25))
        self.fig.suptitle(f"{self.gametimeline.game_name}\n{self.start_date} - {max(self.gametimeline.time_line.keys())}")
        self.axes_iter = iter(self.axes.flat)
        self.t_axis = np.zeros(len(self.gametimeline.time_line))
        for i, date in enumerate(sorted(self.gametimeline.time_line)):
            self.t_axis[i] = 2200 + (date - self.start_date) / 360.0
        for ax in self.axes.flat:
            ax.set_xlim((self.t_axis[0], self.t_axis[-1]))

    def pop_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Population Size")
        ax.set_ylabel("Number of Empire Pops")
        total_pop_count = {}

        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, demographics_data in gamestateinfo.demographics_data.items():
                country = gamestateinfo.country_data[country_id]["name"]
                if country not in total_pop_count:
                    total_pop_count[country] = np.zeros(self.t_axis.shape)
                total_pop_count[country][i] = sum(demographics_data.values())

        for i, country in enumerate(self._iterate_countries_in_order(total_pop_count)):
            pop_count = total_pop_count[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(total_pop_count))
            ax.plot(self.t_axis, pop_count, **plot_kwargs)
        ax.legend()

    def planet_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Empire Size")
        ax.set_ylabel("Number of Owned Planets")
        owned_planets_dict = {}

        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, owned_planets in gamestateinfo.owned_planets.items():
                country = gamestateinfo.country_data[country_id]["name"]
                if country not in owned_planets_dict:
                    owned_planets_dict[country] = np.zeros(self.t_axis.shape)
                owned_planets_dict[country][i] = owned_planets

        for i, country in enumerate(self._iterate_countries_in_order(owned_planets_dict)):
            pop_count = owned_planets_dict[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(owned_planets_dict))
            ax.plot(self.t_axis, pop_count, **plot_kwargs)
        ax.legend()

    def tech_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Technology Progress")
        ax.set_ylabel("Number of Researched Technologies")
        tech_count = {}
        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, country_data in gamestateinfo.country_data.items():
                country = country_data["name"]
                if country not in tech_count:
                    tech_count[country] = np.zeros(self.t_axis.shape)
                tech_count[country][i] = len(country_data["tech_progress"])

        for i, country in enumerate(self._iterate_countries_in_order(tech_count)):
            techs = tech_count[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(tech_count))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def exploration_progress_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Exploration Progress")
        ax.set_ylabel("Number of Surveyed Objects/Systems (?)")
        survey_count = {}
        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, country_data in gamestateinfo.country_data.items():
                country = country_data["name"]
                if country not in survey_count:
                    survey_count[country] = np.zeros(self.t_axis.shape)
                survey_count[country][i] = country_data["exploration_progress"]

        for i, country in enumerate(self._iterate_countries_in_order(survey_count)):
            techs = survey_count[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(survey_count))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def empire_demographics_plot(self):
        ax = next(self.axes_iter)
        ax.set_title(f"Empire Demographics")
        ax.set_ylabel(f"Distribution of Species within {self.gametimeline.game_name}")
        species_distribution = {}
        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            total_pop_count = 0
            demo_data = gamestateinfo.demographics_data[gamestateinfo.player_country]
            for species_id, species_count in demo_data.items():
                species = gamestateinfo.species_list[species_id]["name"]
                if species not in species_distribution:
                    species_distribution[species] = np.zeros(self.t_axis.shape)
                species_distribution[species][i] = species_count
                total_pop_count += species_count
            for species in species_distribution:
                species_distribution[species][i] /= total_pop_count

        y = []
        labels = []
        colors = []
        for i, species in enumerate(self._iterate_countries_in_order(species_distribution)):
            count = species_distribution[species]
            y.append(count)
            labels.append(species)
            colors.append(COLOR_MAP(i / len(species_distribution)))

        ax.stackplot(self.t_axis, y, labels=labels, colors=colors)
        ax.set_ylim((0, 1.0))
        ax.legend()

    def military_strength_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Military Power")
        ax.set_ylabel("Total Fleet Strength")
        military_power = {}
        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, country_data in gamestateinfo.country_data.items():
                country = country_data["name"]
                if country not in military_power:
                    military_power[country] = np.zeros(self.t_axis.shape)
                military_power[country][i] = country_data["military_power"]

        for i, country in enumerate(self._iterate_countries_in_order(military_power)):
            techs = military_power[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(military_power))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def fleet_size_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Fleet Size")
        ax.set_ylabel("Number of Ships (?)")
        fleet_size = {}
        for i, (date, gamestateinfo) in enumerate(sorted(self.gametimeline.time_line.items())):
            for country_id, country_data in gamestateinfo.country_data.items():
                country = country_data["name"]
                if country not in fleet_size:
                    fleet_size[country] = np.zeros(self.t_axis.shape)
                fleet_size[country][i] = country_data["fleet_size"]

        for i, country in enumerate(self._iterate_countries_in_order(fleet_size)):
            techs = fleet_size[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(fleet_size))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def _iterate_countries_in_order(self, data_dict):
        if self.gametimeline.game_name in data_dict:
            yield self.gametimeline.game_name
        for country, data in sorted(data_dict.items(), key=lambda x: (x[1][-1], x[0])):
            if country == self.gametimeline.game_name:
                continue
            yield country

    def _get_country_plot_kwargs(self, country: str, idx: int, num_lines: int):
        linewidth = 1
        c = COLOR_MAP(idx / num_lines)
        label = f"{country}"
        if country == self.gametimeline.game_name:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=150)
