from typing import List, Dict, Union

from matplotlib import pyplot as plt

from stellaristimeline import models

COLOR_MAP = plt.get_cmap("plasma")

_CURRENT_EXECUTION_PLOT_DATA: Dict[str, models.Game] = {}


def get_current_execution_plot_data(game: models.Game) -> "EmpireProgressionPlotData":
    global _CURRENT_EXECUTION_PLOT_DATA
    if game.game_name not in _CURRENT_EXECUTION_PLOT_DATA:
        _CURRENT_EXECUTION_PLOT_DATA[game.game_name] = EmpireProgressionPlotData()
        _CURRENT_EXECUTION_PLOT_DATA[game.game_name].initialize(game)
    return _CURRENT_EXECUTION_PLOT_DATA[game.game_name]


def show_tech_info(country: models.CountryState):
    return country.is_player or country.has_research_agreement_with_player or country.attitude_towards_player.reveals_technology_info()


def show_economic_info(country: models.CountryState):
    return country.is_player or country.has_sensor_link_with_player or country.attitude_towards_player.reveals_economy_info()


def show_demographic_info(country: models.CountryState):
    return country.is_player or country.has_sensor_link_with_player or country.attitude_towards_player.reveals_demographic_info()


def show_geography_info(country: models.CountryState):
    return country.is_player or country.attitude_towards_player.reveals_geographic_info()


def show_military_info(country: models.CountryState):
    return country.is_player or country.has_sensor_link_with_player or country.attitude_towards_player.reveals_military_info()


class EmpireProgressionPlotData:
    DEFAULT_VAL = float("nan")

    def __init__(self, show_everything=False):
        self.dates = None
        self.player_country = None
        self.pop_count = None
        self.owned_planets = None
        self.tech_count = None
        self.survey_count = None
        self.military_power = None
        self.fleet_size = None
        self.species_distribution = None
        self.show_everything = show_everything

    def initialize(self, game: models.Game = None):
        self.dates: List[float] = []
        self.player_country: List[str] = []
        self.pop_count: Dict[str, List[int]] = {}
        self.owned_planets: Dict[str, List[int]] = {}
        self.tech_count: Dict[str, List[int]] = {}
        self.survey_count: Dict[str, List[int]] = {}
        self.military_power: Dict[str, List[float]] = {}
        self.fleet_size: Dict[str, List[float]] = {}
        self.species_distribution: Dict[str, List[float]] = {}
        if game:
            for gs in game.game_states:
                self.process_gamestate(gs)

    def process_gamestate(self, gs: models.GameState):
        self.dates.append(gs.date / 360.0)
        for country in gs.country_states:
            if country.is_player:
                self.player_country.append(country.country_name)
            self._extract_pop_count(country)
            self._extract_planet_count(country)
            self._extract_tech_count(country)
            self._extract_exploration_progress(country)
            self._extract_military_strength(country)
            self._extract_fleet_size(country)
            self._extract_player_empire_demographics(country)
        for data_dict in [self.pop_count, self.owned_planets, self.tech_count, self.survey_count, self.military_power, self.fleet_size]:
            for key in data_dict:
                if len(data_dict[key]) < len(self.dates):
                    data_dict[key].append(EmpireProgressionPlotData.DEFAULT_VAL)

    @staticmethod
    def iterate_data_sorted(data_dict: Dict[str, List[Union[int, float]]]):
        for country, data in sorted(data_dict.items(), key=lambda x: (x[1][-1], x[0]), reverse=True):
            yield country, data

    def _extract_pop_count(self, country):
        if self.show_everything or show_demographic_info(country):
            new_val = sum(pc.pop_count for pc in country.pop_counts)
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.pop_count, country.country_name, new_val)

    def _extract_planet_count(self, country):
        if self.show_everything or show_geography_info(country):
            new_val = country.owned_planets
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.owned_planets, country.country_name, new_val)

    def _extract_tech_count(self, country):
        if self.show_everything or show_tech_info(country):
            new_val = country.tech_progress
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.tech_count, country.country_name, new_val)

    def _extract_exploration_progress(self, country):
        if self.show_everything or show_tech_info(country):
            new_val = country.exploration_progress
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.survey_count, country.country_name, new_val)

    def _extract_military_strength(self, country):
        if self.show_everything or show_military_info(country):
            new_val = country.military_power
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.military_power, country.country_name, new_val)

    def _extract_fleet_size(self, country):
        if self.show_everything or show_military_info(country):
            new_val = country.fleet_size
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.fleet_size, country.country_name, new_val)

    def _extract_player_empire_demographics(self, country):
        if country.is_player:
            total_pop_count = 0
            current_species_count = {s: 0 for s in self.species_distribution}
            for pc in country.pop_counts:
                species = pc.species_name
                if species not in self.species_distribution:
                    current_species_count[species] = 0
                current_species_count[species] += pc.pop_count
                total_pop_count += pc.pop_count
            for s, c in current_species_count.items():
                if s not in self.species_distribution:
                    self.species_distribution[s] = [0 for _ in range(len(self.dates) - 1)]
                self.species_distribution[s].append(c)
            for species in current_species_count:
                if len(self.species_distribution[species]) < len(self.dates):
                    self.species_distribution[species].append(0)
            for species in self.species_distribution:
                self.species_distribution[species][-1] /= total_pop_count

    def _add_new_value_to_data_dict(self, data_dict, key, new_val):
        if key not in data_dict:
            data_dict[key] = [EmpireProgressionPlotData.DEFAULT_VAL for _ in range(len(self.dates) - 1)]
        data_dict[key].append(new_val)


class MatplotLibVisualization:
    """ Make a static visualization using matplotlib. """

    def __init__(self, plot_data, plot_filename="./output/empire_demographics_plot.png"):
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_data: EmpireProgressionPlotData = plot_data
        self.plot_filename = plot_filename

    def make_plots(self):
        self._initialize_axes()
        self.pop_count_plot()
        self.owned_planets_plot()
        self.tech_count_plot()
        self.survey_count_plot()
        self.military_power_plot()
        self.fleet_size_plot()
        self.empire_demographics_plot()

    def pop_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Population Size")
        for i, (country, pop_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.pop_count)):
            if all(pc == EmpireProgressionPlotData.DEFAULT_VAL for pc in pop_count):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.pop_count))
            ax.plot(self.plot_data.dates, pop_count, **plot_kwargs)
        ax.legend()

    def owned_planets_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Owned Planets")
        for i, (country, owned_planets) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.owned_planets)):
            if all(op == EmpireProgressionPlotData.DEFAULT_VAL for op in owned_planets):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.owned_planets))
            ax.plot(self.plot_data.dates, owned_planets, **plot_kwargs)
        ax.legend()

    def tech_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Researched Technologies")
        for i, (country, tech_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.tech_count)):
            if all(tc == EmpireProgressionPlotData.DEFAULT_VAL for tc in tech_count):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.tech_count))
            ax.plot(self.plot_data.dates, tech_count, **plot_kwargs)
        ax.legend()

    def survey_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Surveyed Bodies")
        for i, (country, surveyed_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.survey_count)):
            if all(sc == EmpireProgressionPlotData.DEFAULT_VAL for sc in surveyed_count):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.survey_count))
            ax.plot(self.plot_data.dates, surveyed_count, **plot_kwargs)
        ax.legend()

    def military_power_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Military Power")
        for i, (country, military_power) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.military_power)):
            if all(mp == EmpireProgressionPlotData.DEFAULT_VAL for mp in military_power):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.military_power))
            ax.plot(self.plot_data.dates, military_power, **plot_kwargs)
        ax.legend()

    def fleet_size_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Fleet Size")
        for i, (country, fleet_size) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.fleet_size)):
            if all(fs == EmpireProgressionPlotData.DEFAULT_VAL for fs in fleet_size):
                continue
            plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.fleet_size))
            ax.plot(self.plot_data.dates, fleet_size, **plot_kwargs)
        ax.legend()

    def empire_demographics_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Species Distribution")
        y = []
        labels = []
        colors = []
        data_iter = reversed(list(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.species_distribution)))
        for i, (species, species_count) in enumerate(data_iter):
            y.append(species_count)
            labels.append(species)
            colors.append(COLOR_MAP(i / len(self.plot_data.species_distribution)))
        ax.stackplot(self.plot_data.dates, y, labels=labels, colors=colors)
        ax.set_ylim((0, 1.0))
        ax.legend()

    def _initialize_axes(self):
        self.fig, self.axes = plt.subplots(3, 3, figsize=(32, 16))
        self.axes_iter = iter(self.axes.flat)
        self.fig.suptitle(f"{self.plot_data.player_country[-1]}\n{models.days_to_date(self.plot_data.dates[0])} - {models.days_to_date(self.plot_data.dates[-1])}")
        for ax in self.axes.flat:
            ax.set_xlim((self.plot_data.dates[0], self.plot_data.dates[-1]))
            ax.set_xlabel(f"Time (Years)")

    def _get_country_plot_kwargs(self, country_name: str, i: int, num_lines: int):
        linewidth = 1
        c = COLOR_MAP(i / num_lines)
        label = f"{country_name}"
        if country_name == self.plot_data.player_country[i]:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=250)
        plt.close("all")
