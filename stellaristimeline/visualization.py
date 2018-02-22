import enum
import logging
from typing import List, Dict, NamedTuple, Callable, Any
import dataclasses
from matplotlib import pyplot as plt

from stellaristimeline import models

COLOR_MAP = plt.get_cmap("viridis")


@enum.unique
class PlotStyle(enum.Enum):
    line = 0
    stacked = 1


@dataclasses.dataclass
class PlotSpecification:
    plot_id: str
    title: str
    plot_data_function: Callable[["EmpireProgressionPlotData"], Any]
    style: PlotStyle


POP_COUNT_GRAPH = PlotSpecification(
    plot_id='pop-count-graph',
    title="Total Population",
    plot_data_function=lambda pd: pd.pop_count,
    style=PlotStyle.line,
)
PLANET_COUNT_GRAPH = PlotSpecification(
    plot_id='planet-count-graph',
    title="Owned Planets",
    plot_data_function=lambda pd: pd.owned_planets,
    style=PlotStyle.line,
)
SURVEY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='survey-count-graph',
    title="Exploration",
    plot_data_function=lambda pd: pd.survey_count,
    style=PlotStyle.line,
)
TECHNOLOGY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='tech-count-graph',
    title="Researched Technologies",
    plot_data_function=lambda pd: pd.tech_count,
    style=PlotStyle.line,
)
MILITARY_POWER_GRAPH = PlotSpecification(
    plot_id='military-power-graph',
    title="Military Strength",
    plot_data_function=lambda pd: pd.military_power,
    style=PlotStyle.line,
)
FLEET_SIZE_GRAPH = PlotSpecification(
    plot_id='fleet-size-graph',
    title="Fleet Size",
    plot_data_function=lambda pd: pd.fleet_size,
    style=PlotStyle.line,
)
EMPIRE_DEMOGRAPHICS_GRAPH = PlotSpecification(
    plot_id='empire-demographics-graph',
    title="Empire Demographics",
    plot_data_function=lambda pd: pd.species_distribution,
    style=PlotStyle.stacked,
)
FACTION_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-faction-happiness-graph',
    title="Faction Happiness",
    plot_data_function=lambda pd: pd.faction_happiness,
    style=PlotStyle.line,
)
FACTION_SUPPORT_GRAPH = PlotSpecification(
    plot_id='empire-faction-support-graph',
    title="Faction Support",
    plot_data_function=lambda pd: pd.faction_support,
    style=PlotStyle.line,
)
INTERNAL_POLITICS_GRAPH = PlotSpecification(
    plot_id='empire-internal-politics-graph',
    title="Empire Politics",
    plot_data_function=lambda pd: pd.faction_size_distribution,
    style=PlotStyle.stacked,
)
EMPIRE_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-energy-budget-graph',
    title="Energy Budget (WARNING: INACCURATE!)",
    plot_data_function=lambda pd: pd.empire_budget_allocation,
    style=PlotStyle.stacked,
)
RESEARCH_ALLOCATION_GRAPH = PlotSpecification(
    plot_id='empire-research-allocation-graph',
    title="Empire Research",
    plot_data_function=lambda pd: pd.empire_research_allocation,
    style=PlotStyle.stacked,
)
PLOT_SPECIFICATIONS = {
    'pop-count-graph': POP_COUNT_GRAPH,
    'survey-count-graph': SURVEY_PROGRESS_GRAPH,
    'tech-count-graph': TECHNOLOGY_PROGRESS_GRAPH,
    'military-power-graph': MILITARY_POWER_GRAPH,
    'fleet-size-graph': FLEET_SIZE_GRAPH,
    'empire-demographics-graph': EMPIRE_DEMOGRAPHICS_GRAPH,
    'empire-internal-politics-graph': INTERNAL_POLITICS_GRAPH,
    'empire-research-allocation-graph': RESEARCH_ALLOCATION_GRAPH,
    'empire-energy-budget-graph': EMPIRE_ECONOMY_GRAPH,
}

THEMATICALLY_GROUPED_PLOTS = {
    "Science": [
        TECHNOLOGY_PROGRESS_GRAPH,
        RESEARCH_ALLOCATION_GRAPH,
        SURVEY_PROGRESS_GRAPH],
    "Politics": [
        INTERNAL_POLITICS_GRAPH,
        FACTION_HAPPINESS_GRAPH,
        FACTION_SUPPORT_GRAPH
    ],
    "Military": [
        FLEET_SIZE_GRAPH,
        MILITARY_POWER_GRAPH],
    "Economy": [
        POP_COUNT_GRAPH,
        PLANET_COUNT_GRAPH,
        EMPIRE_ECONOMY_GRAPH,
    ],
}

_CURRENT_EXECUTION_PLOT_DATA: Dict[str, "EmpireProgressionPlotData"] = {}


def get_current_execution_plot_data(game_name: str) -> "EmpireProgressionPlotData":
    global _CURRENT_EXECUTION_PLOT_DATA
    if game_name not in _CURRENT_EXECUTION_PLOT_DATA:
        session = models.SessionFactory()
        game = session.query(models.Game).filter_by(game_name=game_name).first()
        session.close()
        _CURRENT_EXECUTION_PLOT_DATA[game_name] = EmpireProgressionPlotData(game_name, show_everything=False)
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
        self.faction_happiness = None
        self.faction_support = None
        self.empire_budget_allocation = None
        self.empire_research_allocation = None
        self.show_everything = show_everything

    def initialize(self):
        self.dates: List[float] = []
        self.player_country: str = None
        self.pop_count: Dict[str, List[int]] = {}
        self.owned_planets: Dict[str, List[int]] = {}
        self.tech_count: Dict[str, List[int]] = {}
        self.survey_count: Dict[str, List[int]] = {}
        self.military_power: Dict[str, List[float]] = {}
        self.fleet_size: Dict[str, List[float]] = {}
        self.species_distribution: Dict[str, List[float]] = {}
        self.faction_size_distribution: Dict[str, List[float]] = {}
        self.faction_happiness: Dict[str, List[float]] = {}
        self.faction_support: Dict[str, List[float]] = {}
        self.empire_budget_allocation: Dict[str, List[float]] = dict(
            energy_production=[],
            army_expenses=[],
            buildings_expenses=[],
            pops_expenses=[],
            ships_expenses=[],
            stations_expenses=[],
        )
        self.empire_research_allocation = dict(physics=[], society=[], engineering=[])

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

    def make_plot_lists(self, full_data: List[float]):
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

    def iterate_data_sorted(self, plot_spec: PlotSpecification):
        data_dict = plot_spec.plot_data_function(self)
        for key, data in sorted(data_dict.items(), key=lambda x: (x[1][-1], x[0]), reverse=True):
            if key == "ROBOT_POP_SPECIES_1":
                key = "Robot"
            elif key == "ROBOT_POP_SPECIES_2":
                key = "Droid"
            elif key == "ROBOT_POP_SPECIES_3":
                key = "Synth"
            if plot_spec.style == PlotStyle.stacked:
                # For stacked plots, we need all entries!
                x, y = self.dates, data
            else:
                x, y = self.make_plot_lists(data)
            yield key, x, y

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
            self.species_distribution[species][-1] *= 100.0 / total_pop_count

    def _extract_player_empire_politics(self, country_data: models.CountryData):
        total_faction_pop_count = 0
        # first get the current size of each faction
        faction_sizes = {f: 0 for f in self.faction_size_distribution}
        faction_happiness = {f: 0 for f in self.faction_happiness}
        faction_support_dict = {f: 0 for f in self.faction_support}
        for faction_data in country_data.faction_support:
            faction = faction_data.faction.faction_name
            if faction not in self.faction_size_distribution:
                faction_sizes[faction] = 0
                faction_happiness[faction] = 0
                faction_support_dict[faction] = 0
            total_faction_pop_count += faction_data.members
            faction_sizes[faction] += faction_data.members
            faction_happiness[faction] += faction_data.happiness
            faction_support_dict[faction] += faction_data.support

        pop_count = self.pop_count[country_data.country.country_name][-1]
        faction_sizes[EmpireProgressionPlotData.NO_FACTION_POP_KEY] = pop_count - total_faction_pop_count

        # then add them to the data dictionary.
        for f in faction_sizes:
            if f not in self.faction_size_distribution:
                self.faction_size_distribution[f] = [0 for _ in range(len(self.dates) - 1)]
            self.faction_size_distribution[f].append(faction_sizes[f])
        for f in faction_happiness:
            if f not in self.faction_happiness:
                self.faction_happiness[f] = [float("nan") for _ in range(len(self.dates) - 1)]
                self.faction_support[f] = [float("nan") for _ in range(len(self.dates) - 1)]
            self.faction_happiness[f].append(faction_happiness[f])
            self.faction_support[f].append(faction_support_dict[f])
        for faction in faction_sizes:
            if len(self.faction_size_distribution[faction]) < len(self.dates):
                self.faction_size_distribution[faction].append(0)
                if faction in self.faction_happiness:
                    self.faction_happiness[faction].append(float("nan"))
                    self.faction_support[faction].append(float("nan"))
        for faction in self.faction_size_distribution:
            self.faction_size_distribution[faction][-1] *= 100.0 / pop_count

    def _extract_player_empire_budget_allocations(self, country_data: models.CountryData):
        self.empire_budget_allocation["energy_production"].append(country_data.energy_production)
        self.empire_budget_allocation["army_expenses"].append(country_data.energy_spending_army)

        # TODO: Division by 2 in the following entries is purely based on experimentation.
        self.empire_budget_allocation["buildings_expenses"].append(country_data.energy_spending_building / 2)
        self.empire_budget_allocation["pops_expenses"].append(country_data.energy_spending_pop / 2)
        self.empire_budget_allocation["ships_expenses"].append(country_data.energy_spending_ship / 2)
        self.empire_budget_allocation["stations_expenses"].append(country_data.energy_spending_station / 2)

    def _extract_player_empire_research_allocations(self, country_data: models.CountryData):
        total = country_data.physics_research + country_data.society_research + country_data.engineering_research
        self.empire_research_allocation["physics"].append(100.0 * country_data.physics_research / total)
        self.empire_research_allocation["society"].append(100.0 * country_data.society_research / total)
        self.empire_research_allocation["engineering"].append(100.0 * country_data.engineering_research / total)

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
        for plot_id, plot_spec in PLOT_SPECIFICATIONS.items():
            if plot_spec.style == PlotStyle.stacked:
                self._stacked_plot(plot_spec)
            else:
                self._line_plot(plot_spec)

    def _line_plot(self, plot_spec: PlotSpecification):
        ax = next(self.axes_iter)
        ax.set_title("Population Size")
        for i, (key, x, y) in enumerate(self.plot_data.iterate_data_sorted(plot_spec)):
            if y:
                plot_kwargs = self._get_country_plot_kwargs(key, i, len(self.plot_data.pop_count))
                ax.plot(x, y, **plot_kwargs)
        ax.legend()

    def _stacked_plot(self, plot_spec: PlotSpecification):
        ax = next(self.axes_iter)
        ax.set_title(plot_spec.title)
        stacked = []
        labels = []
        colors = []
        data = list(self.plot_data.iterate_data_sorted(plot_spec))[::-1]
        for i, (key, x, y) in enumerate(data):
            stacked.append(y)
            labels.append(key)
            colors.append(COLOR_MAP(i / len(data)))
        if stacked:
            ax.stackplot(self.plot_data.dates, stacked, labels=labels, colors=colors)
        ax.set_ylim((0.0, 1.0))
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
