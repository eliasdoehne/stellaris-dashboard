import logging
from typing import List, Dict, Union

from matplotlib import pyplot as plt

from stellaristimeline import models

COLOR_MAP = plt.get_cmap("viridis")

_CURRENT_EXECUTION_PLOT_DATA: Dict[str, "EmpireProgressionPlotData"] = {}


def get_current_execution_plot_data(game_name: str) -> "EmpireProgressionPlotData":
    global _CURRENT_EXECUTION_PLOT_DATA
    if game_name not in _CURRENT_EXECUTION_PLOT_DATA:
        session = models.SessionFactory()
        game = session.query(models.Game).filter_by(game_name=game_name).first()
        session.close()
        _CURRENT_EXECUTION_PLOT_DATA[game_name] = EmpireProgressionPlotData(game_name, show_everything=True)
        if game:
            _CURRENT_EXECUTION_PLOT_DATA[game_name].initialize()
        else:
            logging.warning(f"Warning: Game {game_name} could not be found in database!")
    _CURRENT_EXECUTION_PLOT_DATA[game_name].update_with_new_gamestate()
    return _CURRENT_EXECUTION_PLOT_DATA[game_name]


def show_tech_info(country_data: models.CountryData):
    return country_data.country.is_player or country_data.has_research_agreement_with_player or country_data.attitude_towards_player.reveals_technology_info()


def show_economic_info(country_data: models.CountryData):
    return country_data.country.is_player or country_data.has_sensor_link_with_player or country_data.attitude_towards_player.reveals_economy_info()


def show_demographic_info(country_data: models.CountryData):
    return country_data.country.is_player or country_data.has_sensor_link_with_player or country_data.attitude_towards_player.reveals_demographic_info()


def show_geography_info(country_data: models.CountryData):
    return country_data.country.is_player or country_data.attitude_towards_player.reveals_geographic_info()


def show_military_info(country_data: models.CountryData):
    return country_data.country.is_player or country_data.has_sensor_link_with_player or country_data.attitude_towards_player.reveals_military_info()


class EmpireProgressionPlotData:
    DEFAULT_VAL = float("nan")
    NO_FACTION_POP_KEY = "No faction (including robots and slaves)"

    def __init__(self, game_name, show_everything=False):
        self.game_name = game_name
        self.dates = None
        self.player_country = None
        self.pop_count = None
        self.owned_planets = None
        self.tech_count = None
        self.survey_count = None
        self.military_power = None
        self.fleet_size = None
        self.species_distribution = None
        self.faction_size_distribution = None
        self.empire_budget_allocation = None
        self.empire_research_allocation = None
        self.show_everything = show_everything

    def initialize(self):
        self.dates: List[float] = []
        self.player_country = None
        self.pop_count: Dict[str, List[int]] = {}
        self.owned_planets: Dict[str, List[int]] = {}
        self.tech_count: Dict[str, List[int]] = {}
        self.survey_count: Dict[str, List[int]] = {}
        self.military_power: Dict[str, List[float]] = {}
        self.fleet_size: Dict[str, List[float]] = {}
        self.species_distribution: Dict[str, List[float]] = {}
        self.faction_size_distribution: Dict[str, List[float]] = {}
        self.empire_budget_allocation: Dict[str, List[float]] = {}

    def update_with_new_gamestate(self):
        date = 360.0 * self.dates[-1] if self.dates else -1
        for gs in models.get_gamestates_since(self.game_name, date):
            self.process_gamestate(gs)

    def process_gamestate(self, gs: models.GameState):
        self.dates.append(gs.date / 360.0)
        for country_data in gs.country_data:
            if self.player_country is None and country_data.country.is_player:
                self.player_country = country_data.country.country_name
                print(self.player_country)
            self._extract_pop_count(country_data)
            self._extract_planet_count(country_data)
            self._extract_tech_count(country_data)
            self._extract_exploration_progress(country_data)
            self._extract_military_strength(country_data)
            self._extract_fleet_size(country_data)
            if country_data.country.is_player:
                self._extract_player_empire_demographics(country_data)
                self._extract_player_empire_politics(country_data)
                self._extract_player_empire_research_allocations(country_data)
                self._extract_player_empire_budget_allocations(country_data)

        # Pad every dict with the default value if no real value was added to keep them consistent with dates list
        for data_dict in [self.pop_count, self.owned_planets, self.tech_count, self.survey_count, self.military_power, self.fleet_size]:
            for key in data_dict:
                if len(data_dict[key]) < len(self.dates):
                    data_dict[key].append(EmpireProgressionPlotData.DEFAULT_VAL)

    def make_plot_lists(self, full_data):
        dates = []
        plot_list = []
        for value, date in zip(full_data, self.dates):
            if value is EmpireProgressionPlotData.DEFAULT_VAL:
                if not plot_list:
                    continue
                elif plot_list[-1] is not value:
                    dates.append(date)
                    plot_list.append(value)
            elif not plot_list or plot_list[-1] != value:
                dates.append(date)
                plot_list.append(value)
            elif plot_list[-1] == value:
                if len(plot_list) > 1 and plot_list[-2] == plot_list[-1]:
                    dates[-1] = date
                else:
                    dates.append(date)
                    plot_list.append(value)
        return dates, plot_list

    @staticmethod
    def iterate_data_sorted(data_dict: Dict[str, List[Union[int, float]]]):
        return_last_keys = {EmpireProgressionPlotData.NO_FACTION_POP_KEY}
        return_last = []
        for key, data in sorted(data_dict.items(), key=lambda x: (x[1][-1], x[0]), reverse=True):
            if key in return_last_keys:
                return_last.append((key, data))
                continue
            if key == "ROBOT_POP_SPECIES_1":
                key = "Robot"
            elif key == "ROBOT_POP_SPECIES_2":
                key = "Droid"
            elif key == "ROBOT_POP_SPECIES_3":
                key = "Synth"
            yield key, data
        for key, data in return_last:
            yield key, data

    def _extract_pop_count(self, country_data: models.CountryData):
        if self.show_everything or show_demographic_info(country_data):
            new_val = sum(pc.pop_count for pc in country_data.pop_counts)
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.pop_count, country_data.country.country_name, new_val)

    def _extract_planet_count(self, country_data: models.CountryData):
        if self.show_everything or show_geography_info(country_data):
            new_val = country_data.owned_planets
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.owned_planets, country_data.country.country_name, new_val)

    def _extract_tech_count(self, country_data: models.CountryData):
        if self.show_everything or show_tech_info(country_data):
            new_val = country_data.tech_progress
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.tech_count, country_data.country.country_name, new_val)

    def _extract_exploration_progress(self, country_data: models.CountryData):
        if self.show_everything or show_tech_info(country_data):
            new_val = country_data.exploration_progress
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.survey_count, country_data.country.country_name, new_val)

    def _extract_military_strength(self, country_data: models.CountryData):
        if self.show_everything or show_military_info(country_data):
            new_val = country_data.military_power
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.military_power, country_data.country.country_name, new_val)

    def _extract_fleet_size(self, country_data: models.CountryData):
        if self.show_everything or show_military_info(country_data):
            new_val = country_data.fleet_size
        else:
            new_val = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.fleet_size, country_data.country.country_name, new_val)

    def _extract_player_empire_demographics(self, country_data: models.CountryData):
        total_pop_count = 0
        current_species_count = {s: 0 for s in self.species_distribution}
        for pc in country_data.pop_counts:
            species = pc.species.species_name
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

    def _extract_player_empire_politics(self, country_data: models.CountryData):
        total_faction_pop_count = 0
        # first get the current size of each faction
        faction_sizes = {f: 0 for f in self.faction_size_distribution}
        for faction_support in country_data.faction_support:
            faction = faction_support.faction.faction_name
            if faction not in self.faction_size_distribution:
                faction_sizes[faction] = 0
            faction_sizes[faction] += faction_support.members
            total_faction_pop_count += faction_support.members

        pop_count = self.pop_count[country_data.country.country_name][-1]
        faction_sizes[EmpireProgressionPlotData.NO_FACTION_POP_KEY] = pop_count - total_faction_pop_count

        # then add them to the data dictionary.
        for f, members in faction_sizes.items():
            if f not in self.faction_size_distribution:
                self.faction_size_distribution[f] = [0 for _ in range(len(self.dates) - 1)]
            self.faction_size_distribution[f].append(members)
        for faction in faction_sizes:
            if len(self.faction_size_distribution[faction]) < len(self.dates):
                self.faction_size_distribution[faction].append(0)
        for faction in self.faction_size_distribution:
            if total_faction_pop_count:  # avoid dividing by 0 => in the beginning, there are no factions
                self.faction_size_distribution[faction][-1] /= pop_count

    def _extract_player_empire_budget_allocations(self, country_data: models.CountryData):
        pass

    def _extract_player_empire_research_allocations(self, country_data: models.CountryData):
        if not self.empire_research_allocation:
            self.empire_research_allocation: Dict[str, List[float]] = dict(physics=[], society=[], engineering=[])
        total = country_data.physics_research + country_data.society_research + country_data.engineering_research
        self.empire_research_allocation["physics"].append(country_data.physics_research / total)
        self.empire_research_allocation["society"].append(country_data.society_research / total)
        self.empire_research_allocation["engineering"].append(country_data.engineering_research / total)

    def _add_new_value_to_data_dict(self, data_dict, key, new_val):
        if key not in data_dict:
            data_dict[key] = [EmpireProgressionPlotData.DEFAULT_VAL for _ in range(len(self.dates) - 1)]
        data_dict[key].append(new_val)


class MatplotLibVisualization:
    """ Make a static visualization using matplotlib. """

    def __init__(self, plot_data, plot_filename=None):
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_data: EmpireProgressionPlotData = plot_data
        if plot_filename is None:
            plot_filename = f"./output/empire_plot_{self.plot_data.game_name}.png"
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
        self.empire_internal_politics_plot()
        self.empire_research_allocations_plot()

    def pop_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Population Size")
        for i, (country, pop_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.pop_count)):
            dates, plot_pop_count = self.plot_data.make_plot_lists(pop_count)
            if plot_pop_count:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.pop_count))
                ax.plot(dates, plot_pop_count, **plot_kwargs)
        ax.legend()

    def owned_planets_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Owned Planets")
        for i, (country, owned_planets) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.owned_planets)):
            dates, plot_owned_planets = self.plot_data.make_plot_lists(owned_planets)
            if plot_owned_planets:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.owned_planets))
                ax.plot(dates, plot_owned_planets, **plot_kwargs)
        ax.legend()

    def tech_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Researched Technologies")
        for i, (country, tech_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.tech_count)):
            dates, plot_tech_count = self.plot_data.make_plot_lists(tech_count)
            if plot_tech_count:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.tech_count))
                ax.plot(dates, plot_tech_count, **plot_kwargs)
        ax.legend()

    def survey_count_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Surveyed Bodies")
        for i, (country, surveyed_count) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.survey_count)):
            dates, plot_survey_count = self.plot_data.make_plot_lists(surveyed_count)
            if plot_survey_count:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.survey_count))
                ax.plot(dates, plot_survey_count, **plot_kwargs)
        ax.legend()

    def military_power_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Military Power")
        for i, (country, military_power) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.military_power)):
            dates, plot_military_power = self.plot_data.make_plot_lists(military_power)
            if plot_military_power:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.military_power))
                ax.plot(dates, plot_military_power, **plot_kwargs)
        ax.legend()

    def fleet_size_plot(self):
        ax = next(self.axes_iter)
        ax.set_title("Fleet Size")
        for i, (country, fleet_size) in enumerate(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.fleet_size)):
            dates, plot_fleet_size = self.plot_data.make_plot_lists(fleet_size)
            if plot_fleet_size:
                plot_kwargs = self._get_country_plot_kwargs(country, i, len(self.plot_data.fleet_size))
                ax.plot(dates, plot_fleet_size, **plot_kwargs)
        ax.legend()

    def empire_demographics_plot(self):
        ax = next(self.axes_iter)
        ax.set_title(f"Species Distribution in {self.plot_data.player_country}")
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

    def empire_internal_politics_plot(self):
        ax = next(self.axes_iter)
        ax.set_title(f"Faction allegiance in {self.plot_data.player_country}")
        y = []
        labels = []
        colors = []
        data_iter = reversed(list(EmpireProgressionPlotData.iterate_data_sorted(self.plot_data.faction_size_distribution)))
        for i, (faction, supporter_count) in enumerate(data_iter):
            y.append(supporter_count)
            labels.append(faction)
            colors.append(COLOR_MAP(i / len(self.plot_data.faction_size_distribution)))
        ax.stackplot(self.plot_data.dates, y, labels=labels, colors=colors)
        ax.set_ylim((0, 1.0))
        ax.legend()

    def empire_research_allocations_plot(self):
        ax = next(self.axes_iter)
        ax.set_title(f"Relative allocation of research output in {self.plot_data.player_country}")
        y = list(self.plot_data.empire_research_allocation.values())
        labels = list(self.plot_data.empire_research_allocation.keys())
        colors = [COLOR_MAP(0.0), COLOR_MAP(0.5), COLOR_MAP(1.0)]
        ax.stackplot(self.plot_data.dates, y, labels=labels, colors=colors)
        ax.set_ylim((0, 1.0))
        ax.legend()

    def _initialize_axes(self):
        self.fig, self.axes = plt.subplots(3, 3, figsize=(40, 24))
        self.axes_iter = iter(self.axes.flat)
        self.fig.suptitle(f"{self.plot_data.player_country}\n{models.days_to_date(360 * self.plot_data.dates[0])} - {models.days_to_date(360 * self.plot_data.dates[-1])}")
        for ax in self.axes.flat:
            ax.set_xlim((self.plot_data.dates[0], self.plot_data.dates[-1]))
            ax.set_xlabel(f"Time (Years)")

    def _get_country_plot_kwargs(self, country_name: str, i: int, num_lines: int):
        linewidth = 1
        c = COLOR_MAP(i / num_lines)
        label = f"{country_name}"
        if country_name == self.plot_data.player_country:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self):
        plt.savefig(self.plot_filename, dpi=250)
        plt.close("all")
