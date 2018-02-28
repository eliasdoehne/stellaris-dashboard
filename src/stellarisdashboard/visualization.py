import enum
import logging
from typing import List, Dict, Callable, Any

import dataclasses
import math
from matplotlib import pyplot as plt

from stellarisdashboard import models

logger = logging.getLogger(__name__)


@enum.unique
class PlotStyle(enum.Enum):
    line = 0
    stacked = 1
    budget = 2


@dataclasses.dataclass
class PlotSpecification:
    plot_id: str
    title: str
    plot_data_function: Callable[["EmpireProgressionPlotData"], Any]
    style: PlotStyle
    yrange: float = None


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
TECHNOLOGY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='tech-count-graph',
    title="Researched Technologies",
    plot_data_function=lambda pd: pd.tech_count,
    style=PlotStyle.line,
)
RESEARCH_ALLOCATION_GRAPH = PlotSpecification(
    plot_id='empire-research-allocation-graph',
    title="Research Allocation",
    plot_data_function=lambda pd: pd.empire_research_allocation,
    yrange=(0, 100),
    style=PlotStyle.stacked,
)
RESEARCH_OUTPUT_GRAPH = PlotSpecification(
    plot_id='empire-research-output-graph',
    title="Research Output",
    plot_data_function=lambda pd: pd.empire_research_output,
    style=PlotStyle.stacked,
)
SURVEY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='survey-count-graph',
    title="Exploration",
    plot_data_function=lambda pd: pd.survey_count,
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
    title="Species in your Empire",
    plot_data_function=lambda pd: pd.species_distribution,
    yrange=(0, 100.0),
    style=PlotStyle.stacked,
)
FACTION_SIZE_GRAPH = PlotSpecification(
    plot_id='empire-internal-politics-graph',
    title="Faction Size",
    plot_data_function=lambda pd: pd.faction_size_distribution,
    yrange=(0, 100.0),
    style=PlotStyle.stacked,
)
FACTION_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-faction-happiness-graph',
    title="Faction Happiness",
    plot_data_function=lambda pd: pd.faction_happiness,
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
FACTION_SUPPORT_GRAPH = PlotSpecification(
    plot_id='empire-faction-support-graph',
    title="Faction Support",
    plot_data_function=lambda pd: pd.faction_support,
    yrange=(0, 1.0),
    style=PlotStyle.stacked,
)
EMPIRE_ENERGY_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-energy-budget-graph',
    title="Energy Budget",
    plot_data_function=lambda pd: pd.empire_energy_budget,
    style=PlotStyle.budget,
)
EMPIRE_MINERAL_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-mineral-budget-graph',
    title="Mineral Budget",
    plot_data_function=lambda pd: pd.empire_mineral_budget,
    style=PlotStyle.budget,
)
EMPIRE_FOOD_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-food-budget-graph',
    title="Food",
    plot_data_function=lambda pd: pd.empire_food_budget,
    style=PlotStyle.budget,
)

THEMATICALLY_GROUPED_PLOTS = {
    "Science": [
        TECHNOLOGY_PROGRESS_GRAPH,
        RESEARCH_OUTPUT_GRAPH,
        RESEARCH_ALLOCATION_GRAPH,
        SURVEY_PROGRESS_GRAPH,
    ],
    "Population": [
        POP_COUNT_GRAPH,
        EMPIRE_DEMOGRAPHICS_GRAPH,
        FACTION_SIZE_GRAPH,
        FACTION_SUPPORT_GRAPH,
        FACTION_HAPPINESS_GRAPH,
    ],
    "Military": [
        FLEET_SIZE_GRAPH,
        MILITARY_POWER_GRAPH
    ],
    "Economy": [
        PLANET_COUNT_GRAPH,
        EMPIRE_ENERGY_ECONOMY_GRAPH,
        EMPIRE_MINERAL_ECONOMY_GRAPH,
        EMPIRE_FOOD_ECONOMY_GRAPH,
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
            logger.warning(f"Warning: Game {game_name} could not be found in database!")
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
        self.empire_energy_budget = None
        self.empire_mineral_budget = None
        self.empire_food_budget = None
        self.empire_research_output = None
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
        self.empire_energy_budget: Dict[str, List[float]] = dict(
            base_income=[],
            production=[],
            trade_income=[],
            mission_income=[],
            army_expenses=[],
            building_expenses=[],
            pop_expenses=[],
            ship_expenses=[],
            station_expenses=[],
            colonization_expenses=[],
            starbase_expenses=[],
            trade_expenses=[],
            mission_expenses=[],
        )
        self.empire_mineral_budget: Dict[str, List[float]] = dict(
            production=[],
            trade_income=[],
            pop_expenses=[],
            ship_expenses=[],
            trade_expenses=[],
        )
        self.empire_food_budget: Dict[str, List[float]] = dict(
            production=[],
            trade_income=[],
            consumption=[],
            trade_expenses=[],
        )
        self.empire_research_output = dict(physics=[], society=[], engineering=[])
        self.empire_research_allocation = dict(physics=[], society=[], engineering=[])

    def _extract_player_empire_budget_allocations(self, gs: models.GameState):
        # For some reason, some budget values have to be halved...
        self.empire_energy_budget["base_income"].append(gs.energy_income_base)
        self.empire_energy_budget["trade_income"].append(gs.energy_income_trade)
        self.empire_energy_budget["production"].append(gs.energy_income_production / 2)
        self.empire_energy_budget["mission_income"].append(gs.energy_income_mission / 2)

        self.empire_energy_budget["army_expenses"].append(gs.energy_spending_army / 2)
        self.empire_energy_budget["building_expenses"].append(gs.energy_spending_building / 2)
        self.empire_energy_budget["pop_expenses"].append(gs.energy_spending_pop / 2)
        self.empire_energy_budget["ship_expenses"].append(gs.energy_spending_ship / 2)
        self.empire_energy_budget["station_expenses"].append(gs.energy_spending_station / 2)
        self.empire_energy_budget["colonization_expenses"].append(gs.energy_spending_colonization)
        self.empire_energy_budget["starbase_expenses"].append(gs.energy_spending_starbases / 2)
        self.empire_energy_budget["mission_expenses"].append(gs.energy_spending_mission / 2)
        self.empire_energy_budget["trade_expenses"].append(gs.energy_spending_trade)

        self.empire_mineral_budget["production"].append(gs.mineral_income_production)
        self.empire_mineral_budget["trade_income"].append(gs.mineral_income_trade / 2)
        self.empire_mineral_budget["pop_expenses"].append(gs.mineral_spending_pop / 2)
        self.empire_mineral_budget["ship_expenses"].append(gs.mineral_spending_ship / 2)
        self.empire_mineral_budget["trade_expenses"].append(gs.mineral_income_trade / 2)

        self.empire_food_budget["production"].append(gs.food_income_production)
        self.empire_food_budget["trade_income"].append(gs.food_income_trade)
        self.empire_food_budget["consumption"].append(- gs.food_spending)
        self.empire_food_budget["trade_expenses"].append(- gs.food_spending_trade)

    def update_with_new_gamestate(self):
        date = 360.0 * self.dates[-1] if self.dates else -1
        for gs in models.get_gamestates_since(self.game_name, date):
            self.process_gamestate(gs)

    def process_gamestate(self, gs: models.GameState):
        self.dates.append(gs.date / 360.0)
        for country_data in gs.country_data:
            if self.player_country is None and country_data.country.is_player:
                self.player_country = country_data.country.country_name
            self._extract_pop_count(country_data)
            self._extract_planet_count(country_data)
            self._extract_tech_count(country_data)
            self._extract_exploration_progress(country_data)
            self._extract_military_strength(country_data)
            self._extract_fleet_size(country_data)
            if country_data.country.is_player:
                self._extract_player_empire_demographics(country_data)
                self._extract_player_empire_politics(country_data)
                self._extract_player_empire_research(country_data)
        self._extract_player_empire_budget_allocations(gs)

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
            if plot_spec.style == PlotStyle.line:
                # For line plots, we can compress the entries, as each line is independent
                x, y = self.make_plot_lists(data)
            else:
                # other plots need to add data points to each other => return everything
                x, y = self.dates, data
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
        faction_sizes = {}
        faction_happiness = {}
        faction_support_dict = {}
        for faction_data in sorted(country_data.faction_support, key=lambda fdata: fdata.faction.ethics):
            faction = faction_data.faction.faction_name
            if faction not in faction_sizes:
                faction_sizes[faction] = 0
                faction_happiness[faction] = 0
                faction_support_dict[faction] = 0
            total_faction_pop_count += faction_data.members
            faction_sizes[faction] += faction_data.members
            faction_happiness[faction] += faction_data.happiness
            faction_support_dict[faction] += faction_data.support

        for f in self.faction_size_distribution:
            if f not in faction_sizes:
                faction_sizes[f] = 0
        for f in self.faction_happiness:
            if f not in faction_happiness:
                faction_happiness[f] = 0
                faction_support_dict[f] = 0

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

    def _extract_player_empire_research(self, country_data: models.CountryData):
        self.empire_research_output["physics"].append(country_data.physics_research)
        self.empire_research_output["society"].append(country_data.society_research)
        self.empire_research_output["engineering"].append(country_data.engineering_research)
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
    COLOR_MAP = plt.get_cmap("viridis")

    def __init__(self, plot_data, plot_filename_base=None):
        self.fig = None
        self.axes = None
        self.axes_iter = None
        self.plot_data: EmpireProgressionPlotData = plot_data
        if plot_filename_base is None:
            plot_filename_base = f"./output/{self.plot_data.game_name}_{{plot_id}}.png"
        self.plot_filename_base = plot_filename_base

    def make_plots(self):
        for category, plot_specifications in THEMATICALLY_GROUPED_PLOTS.items():
            self._initialize_axes(category, plot_specifications)
            for plot_spec in plot_specifications:
                ax = next(self.axes_iter)
                if plot_spec.style == PlotStyle.stacked:
                    self._stacked_plot(ax, plot_spec)
                elif plot_spec.style == PlotStyle.budget:
                    self._budget_plot(ax, plot_spec)
                else:
                    self._line_plot(ax, plot_spec)
                if plot_spec.yrange is not None:
                    ax.set_ylim(plot_spec.yrange)
            self.save_plot(self.plot_filename_base.format(plot_id=category))

    def _line_plot(self, ax, plot_spec: PlotSpecification):
        ax.set_title(plot_spec.title)
        for i, (key, x, y) in enumerate(self.plot_data.iterate_data_sorted(plot_spec)):
            if y:
                plot_kwargs = self._get_country_plot_kwargs(key, i, len(plot_spec.plot_data_function(self.plot_data)))
                ax.plot(x, y, **plot_kwargs)
        ax.legend()

    def _stacked_plot(self, ax, plot_spec: PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked = []
        labels = []
        colors = []
        data = list(self.plot_data.iterate_data_sorted(plot_spec))
        for i, (key, x, y) in enumerate(data):
            stacked.append(y)
            labels.append(key)
            colors.append(MatplotLibVisualization.COLOR_MAP(i / len(data)))
        if stacked:
            ax.stackplot(self.plot_data.dates, stacked, labels=labels, colors=colors, alpha=0.75)
        ax.legend(loc='upper left')

    def _budget_plot(self, ax, plot_spec: PlotSpecification):
        ax.set_title(plot_spec.title)
        stacked_pos = []
        labels_pos = []
        colors_pos = []
        stacked_neg = []
        labels_neg = []
        colors_neg = []
        data = sorted(self.plot_data.iterate_data_sorted(plot_spec), key=lambda tup: tup[-1][-1], reverse=True)
        data = [(key, x_values, y_values) for (key, x_values, y_values) in data if not all(y == 0 for y in y_values)]
        net = [0 for _ in self.plot_data.dates]
        for i, (key, x_values, y_values) in enumerate(data):
            color_val = i / (len(data) - 1)
            if y_values[-1] > 0:
                stacked_pos.append(y_values)
                labels_pos.append(key)
                colors_pos.append(MatplotLibVisualization.COLOR_MAP(color_val))
            else:
                stacked_neg.append(y_values)
                labels_neg.append(key)
                colors_neg.append(MatplotLibVisualization.COLOR_MAP(color_val))
            for j, y in enumerate(y_values):
                net[j] += y
        ax.stackplot(self.plot_data.dates, stacked_neg, labels=labels_neg, colors=colors_neg, alpha=0.75)
        ax.stackplot(self.plot_data.dates, stacked_pos, labels=labels_pos, colors=colors_pos, alpha=0.75)
        ax.plot(self.plot_data.dates, net, label="Net income", color="k")
        ax.legend(loc='upper left')

    def _initialize_axes(self, category: str, plot_specifications: List[PlotSpecification]):
        num_plots = len(plot_specifications)
        cols = int(math.sqrt(num_plots))
        rows = int(math.ceil(num_plots / cols))
        figsize = (16 * cols, 9 * rows)
        self.fig, self.axes = plt.subplots(rows, cols, figsize=figsize)
        self.axes_iter = iter(self.axes.flat)
        title_lines = [
            f"{self.plot_data.player_country}",
            f"{category}",
            f"{models.days_to_date(360 * self.plot_data.dates[0])} - {models.days_to_date(360 * self.plot_data.dates[-1])}"
        ]
        self.fig.suptitle("\n".join(title_lines))
        for ax in self.axes.flat:
            ax.set_xlim((self.plot_data.dates[0], self.plot_data.dates[-1]))
            ax.set_xlabel(f"Time (Years)")

    def _get_country_plot_kwargs(self, country_name: str, i: int, num_lines: int):
        linewidth = 1
        c = MatplotLibVisualization.COLOR_MAP(i / num_lines)
        label = f"{country_name}"
        if country_name == self.plot_data.player_country:
            linewidth = 2
            c = "r"
            label += " (player)"
        return {"label": label, "c": c, "linewidth": linewidth}

    def save_plot(self, plot_filename):
        plt.savefig(plot_filename, dpi=250)
        plt.close("all")
