import numpy as np
from matplotlib import pyplot as plt

from stellaristimeline import models

COLOR_MAP = plt.get_cmap("plasma")


def show_tech_info(countrystate: models.CountryState):
    return countrystate.has_research_agreement_with_player or countrystate.attitude_towards_player.reveals_technology_info()


def show_economic_info(countrystate: models.CountryState):
    return countrystate.has_sensor_link_with_player or countrystate.attitude_towards_player.reveals_economy_info()


def show_demographic_info(countrystate: models.CountryState):
    return countrystate.has_sensor_link_with_player or countrystate.attitude_towards_player.reveals_demographic_info()


def show_geography_info(countrystate: models.CountryState):
    return countrystate.attitude_towards_player.reveals_geographic_info()


def show_military_info(countrystate: models.CountryState):
    return countrystate.has_sensor_link_with_player or countrystate.attitude_towards_player.reveals_military_info()


class EmpireProgressionPlot:
    def __init__(self, plot_filename="./output/empire_demographics_plot.png"):
        self.game = None
        self.player_country_name = None
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_filename = plot_filename
        self.t_axis = None
        self._session = None

    def make_plot(self, game: models.Game, player_country_name: str):
        self.game = game
        self.player_country_name = player_country_name

        self.initialize_axes()
        self.pop_count_plot()
        self.planet_count_plot()
        self.tech_count_plot()
        self.exploration_progress_plot()
        self.empire_demographics_plot()
        self.military_strength_plot()
        self.fleet_size_plot()

        self.game = None

    def initialize_axes(self):
        self.fig, self.axes = plt.subplots(3, 3, figsize=(40, 25))
        self.axes_iter = iter(self.axes.flat)
        self.t_axis = np.zeros(len(self.game.game_states))
        for i, gs in enumerate(self.game.game_states):
            self.t_axis[i] = 2200 + gs.date / 360
        self.fig.suptitle(f"{self.player_country_name}\n{self.t_axis[0]} - {self.t_axis[-1]}")
        for ax in self.axes.flat:
            ax.set_xlim((self.t_axis[0], self.t_axis[-1]))

    def pop_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Population Size")
        ax.set_ylabel("Number of Empire Pops")
        total_pop_count = {}

        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_demographic_info(country_state):
                        continue
                if country not in total_pop_count:
                    total_pop_count[country] = float("nan") * np.ones(self.t_axis.shape)

                total_pop_count[country][i] = sum(pc.pop_count for pc in country_state.pop_counts)
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

        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_geography_info(country_state):
                        continue
                if country not in owned_planets_dict:
                    owned_planets_dict[country] = float("nan") * np.ones(self.t_axis.shape)
                owned_planets_dict[country][i] = country_state.owned_planets

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
        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_tech_info(country_state):
                        continue
                if country not in tech_count:
                    tech_count[country] = float("nan") * np.ones(self.t_axis.shape)
                tech_count[country][i] = country_state.tech_progress

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
        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_tech_info(country_state):
                        continue
                if country not in survey_count:
                    survey_count[country] = float("nan") * np.ones(self.t_axis.shape)
                survey_count[country][i] = country_state.exploration_progress

        for i, country in enumerate(self._iterate_countries_in_order(survey_count)):
            techs = survey_count[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(survey_count))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def empire_demographics_plot(self):
        ax = next(self.axes_iter)
        ax.set_title(f"Empire Demographics ({self.player_country_name})")
        ax.set_ylabel(f"Distribution of Species")
        species_distribution = {}
        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                if country_state.is_player:
                    total_pop_count = 0
                    for pc in country_state.pop_counts:
                        species = pc.species_name
                        if species not in species_distribution:
                            species_distribution[species] = np.zeros(self.t_axis.shape)
                        species_distribution[species][i] += pc.pop_count
                        total_pop_count += pc.pop_count
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
        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_military_info(country_state):
                        continue
                if country not in military_power:
                    military_power[country] = float("nan") * np.ones(self.t_axis.shape)
                military_power[country][i] = country_state.military_power

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
        for i, gs in enumerate(self.game.game_states):
            for country_state in gs.country_states:
                country = country_state.country_name
                if country != self.player_country_name:
                    if not show_military_info(country_state):
                        continue
                if country not in fleet_size:
                    fleet_size[country] = float("nan") * np.ones(self.t_axis.shape)
                fleet_size[country][i] = country_state.fleet_size

        for i, country in enumerate(self._iterate_countries_in_order(fleet_size)):
            techs = fleet_size[country]
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(fleet_size))
            ax.plot(self.t_axis, techs, **plot_kwargs)
        ax.legend()

    def _iterate_countries_in_order(self, data_dict):
        for country, data in sorted(data_dict.items(), key=lambda x: (x[1][-1], x[0])):
            yield country

    def _get_country_plot_kwargs(self, country_name: str, idx: int, num_lines: int):
        linewidth = 1
        c = COLOR_MAP(idx / num_lines)
        label = f"{country_name}"
        if country_name == self.player_country_name:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=150)
