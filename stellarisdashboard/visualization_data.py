import enum
import logging
import random
import time
from typing import List, Dict, Callable, Any, Tuple, Iterable

import dataclasses
import networkx as nx

from stellarisdashboard import models, config

logger = logging.getLogger(__name__)

COLOR_PHYSICS = (0.12, 0.4, 0.66)
COLOR_SOCIETY = (0.23, 0.59, 0.35)
COLOR_ENGINEERING = (0.75, 0.59, 0.12)


@enum.unique
class PlotStyle(enum.Enum):
    """ Defines the kind of visualization associated with a given PlotSpecification (defined below)"""
    line = 0
    stacked = 1
    budget = 2


@dataclasses.dataclass
class PlotSpecification:
    """
    This class is used to define all available visualizations in a way that is
    independent of the frontend, such that they can be defined in a single place for
    both matplotlib and plotly.
    """
    plot_id: str
    title: str

    # This function specifies how the associated data can be obtained from the EmpireProgressionPlotData instance.
    plot_data_function: Callable[["EmpireProgressionPlotData"], Any]
    style: PlotStyle
    yrange: Tuple[float, float] = None

    x_axis_label: str = "Time (years after 2200.01.01)"
    y_axis_label: str = ""


### Define PlotSpecifications for all currently supported plots
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
SYSTEM_COUNT_GRAPH = PlotSpecification(
    plot_id='system-count-graph',
    title="Controlled Systems",
    plot_data_function=lambda pd: pd.controlled_systems,
    style=PlotStyle.line,
)
NET_MINERAL_INCOME_GRAPH = PlotSpecification(
    plot_id='net-mineral-income-graph',
    title="Net Mineral Income",
    plot_data_function=lambda pd: pd.net_mineral_income,
    style=PlotStyle.line,
)
NET_ENERGY_INCOME_GRAPH = PlotSpecification(
    plot_id='net-energy-income-graph',
    title="Net Energy Income",
    plot_data_function=lambda pd: pd.net_energy_income,
    style=PlotStyle.line,
)
NET_ALLOYS_INCOME_GRAPH = PlotSpecification(
    plot_id='net-alloys-income-graph',
    title="Net Alloys Income",
    plot_data_function=lambda pd: pd.net_alloys_income,
    style=PlotStyle.line,
)
NET_CONSUMER_GOODS_INCOME_GRAPH = PlotSpecification(
    plot_id='net-consumer-goods-income-graph',
    title="Net Consumer Goods Income",
    plot_data_function=lambda pd: pd.net_consumer_goods_income,
    style=PlotStyle.line,
)
NET_FOOD_INCOME_GRAPH = PlotSpecification(
    plot_id='net-food-income-graph',
    title="Net Food Income",
    plot_data_function=lambda pd: pd.net_food_income,
    style=PlotStyle.line,
)
TECHNOLOGY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='tech-count-graph',
    title="Researched Technologies",
    plot_data_function=lambda pd: pd.tech_count,
    style=PlotStyle.line,
)
RESEARCH_OUTPUT_GRAPH = PlotSpecification(
    plot_id='empire-research-output-graph',
    title="Research Output",
    plot_data_function=lambda pd: pd.empire_research_output,
    style=PlotStyle.stacked,
)
TOTAL_RESEARCH_OUTPUT_GRAPH = PlotSpecification(
    plot_id='empire-research-output-comparison-graph',
    title="Total Research Output",
    plot_data_function=lambda pd: pd.total_research_output,
    style=PlotStyle.line,
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
SPECIES_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-species-distribution-graph',
    title="Species Demographics",
    plot_data_function=lambda pd: pd.species_distribution,
    style=PlotStyle.stacked,
)
SPECIES_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-species-crime-graph',
    title="Crime by Species",
    plot_data_function=lambda pd: pd.crime_by_species,
    style=PlotStyle.line,
)
SPECIES_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-species-happiness-graph',
    title="Happiness by Species",
    plot_data_function=lambda pd: pd.happiness_by_species,
    style=PlotStyle.line,
)
FACTION_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-faction-distribution-graph',
    title="Faction Demographics",
    plot_data_function=lambda pd: pd.faction_distribution,
    style=PlotStyle.stacked,
)
FACTION_SUPPORT_GRAPH = PlotSpecification(
    plot_id='empire-faction-support-graph',
    title="Faction Support",
    plot_data_function=lambda pd: pd.support_by_faction,
    style=PlotStyle.stacked,
)
FACTION_APPROVAL_GRAPH = PlotSpecification(
    plot_id='empire-faction-approval-graph',
    title="Faction Approval",
    plot_data_function=lambda pd: pd.approval_by_faction,
    style=PlotStyle.line,
)
FACTION_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-faction-crime-graph',
    title="Crime by Faction",
    plot_data_function=lambda pd: pd.crime_by_faction,
    style=PlotStyle.line,
)
FACTION_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-faction-happiness-graph',
    title="Happiness by Faction",
    plot_data_function=lambda pd: pd.happiness_by_faction,
    style=PlotStyle.line,
)
PLANET_POP_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-planet-pop-distribution-graph',
    title="Population by Planet",
    plot_data_function=lambda pd: pd.planet_pop_distribution,
    style=PlotStyle.stacked,
)
PLANET_MIGRATION_GRAPH = PlotSpecification(
    plot_id='empire-planet-migration-graph',
    title="Migration by Planet",
    plot_data_function=lambda pd: pd.migration_by_planet,
    style=PlotStyle.line,
)
PLANET_AMENITIES_GRAPH = PlotSpecification(
    plot_id='empire-planet-amenities-graph',
    title="Free Amenities by Planet",
    plot_data_function=lambda pd: pd.free_amenities_by_planet,
    style=PlotStyle.line,
)
PLANET_STABILITY_GRAPH = PlotSpecification(
    plot_id='empire-planet-stability-graph',
    title="Stability by Planet",
    plot_data_function=lambda pd: pd.stability_by_planet,
    style=PlotStyle.line,
)
PLANET_HOUSING_GRAPH = PlotSpecification(
    plot_id='empire-planet-housing-graph',
    title="Free Housing by Planet",
    plot_data_function=lambda pd: pd.free_housing_by_planet,
    style=PlotStyle.line,
)
PLANET_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-planet-crime-graph',
    title="Crime by Planet",
    plot_data_function=lambda pd: pd.crime_by_planet,
    style=PlotStyle.line,
)
PLANET_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-planet-happiness-graph',
    title="Happiness by Planet",
    plot_data_function=lambda pd: pd.happiness_by_planet,
    style=PlotStyle.line,
)
ETHOS_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-ethos-distribution-graph',
    title="Ethos Demographics",
    plot_data_function=lambda pd: pd.ethos_distribution,
    style=PlotStyle.stacked,
)
ETHOS_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-ethos-crime-graph',
    title="Crime by Ethos",
    plot_data_function=lambda pd: pd.crime_by_ethos,
    style=PlotStyle.line,
)
ETHOS_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-ethos-happiness-graph',
    title="Happiness by Ethos",
    plot_data_function=lambda pd: pd.happiness_by_ethos,
    style=PlotStyle.line,
)
STRATA_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-strata-distribution-graph',
    title="Stratum Demographics",
    plot_data_function=lambda pd: pd.stratum_distribution,
    style=PlotStyle.stacked,
)
STRATA_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-strata-crime-graph',
    title="Crime by Stratum",
    plot_data_function=lambda pd: pd.crime_by_stratum,
    style=PlotStyle.line,
)
STRATA_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-strata-happiness-graph',
    title="Happiness by Stratum",
    plot_data_function=lambda pd: pd.happiness_by_stratum,
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
JOB_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-job-distribution-graph',
    title="Job Demographics",
    plot_data_function=lambda pd: pd.job_distribution,
    style=PlotStyle.stacked,
)
JOB_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-job-crime-graph',
    title="Crime by Job",
    plot_data_function=lambda pd: pd.crime_by_job,
    style=PlotStyle.line,
)
JOB_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-job-happiness-graph',
    title="Happiness by Job",
    plot_data_function=lambda pd: pd.happiness_by_job,
    style=PlotStyle.line,
    yrange=(0, 1.0),
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
EMPIRE_CONSUMER_GOODS_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-consumer-goods-budget-graph',
    title="Consumer Goods Budget",
    plot_data_function=lambda pd: pd.empire_consumer_goods_budget,
    style=PlotStyle.budget,
)
EMPIRE_ALLOYS_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-alloys-budget-graph',
    title="Alloys Budget",
    plot_data_function=lambda pd: pd.empire_alloys_budget,
    style=PlotStyle.budget,
)
EMPIRE_FOOD_ECONOMY_GRAPH = PlotSpecification(
    plot_id='empire-food-budget-graph',
    title="Food",
    plot_data_function=lambda pd: pd.empire_food_budget,
    style=PlotStyle.budget,
)

VICTORY_RANK_GRAPH = PlotSpecification(
    plot_id='victory-rank-graph',
    title="Victory Rank (Lower is better!)",
    plot_data_function=lambda pd: pd.vic_rank,
    style=PlotStyle.line,
)
VICTORY_SCORE_GRAPH = PlotSpecification(
    plot_id='victory-score-graph',
    title="Victory Score",
    plot_data_function=lambda pd: pd.vic_score,
    style=PlotStyle.line,
)
VICTORY_ECONOMY_SCORE_GRAPH = PlotSpecification(
    plot_id='victory-economy-score-graph',
    title="Victory Economic Score",
    plot_data_function=lambda pd: pd.vic_economy_score,
    style=PlotStyle.line,
)

# This dictionary specifies how the plots should be laid out in tabs by the plotly frontend
# and how they should be split to different image files by matplotlib
THEMATICALLY_GROUPED_PLOTS = {
    "Budget": [
        EMPIRE_ENERGY_ECONOMY_GRAPH,
        EMPIRE_MINERAL_ECONOMY_GRAPH,
        EMPIRE_CONSUMER_GOODS_ECONOMY_GRAPH,
        EMPIRE_ALLOYS_ECONOMY_GRAPH,
        EMPIRE_FOOD_ECONOMY_GRAPH,
    ],
    "Economy": [
        PLANET_COUNT_GRAPH,
        SYSTEM_COUNT_GRAPH,
        NET_ENERGY_INCOME_GRAPH,
        NET_MINERAL_INCOME_GRAPH,
        NET_ALLOYS_INCOME_GRAPH,
        NET_CONSUMER_GOODS_INCOME_GRAPH,
        NET_FOOD_INCOME_GRAPH,
    ],
    "Pops": [
        SPECIES_DISTRIBUTION_GRAPH,
        SPECIES_CRIME_GRAPH,
        SPECIES_HAPPINESS_GRAPH,
        ETHOS_DISTRIBUTION_GRAPH,
        ETHOS_CRIME_GRAPH,
        ETHOS_HAPPINESS_GRAPH,
        STRATA_DISTRIBUTION_GRAPH,
        STRATA_CRIME_GRAPH,
        STRATA_HAPPINESS_GRAPH,
    ],
    "Jobs": [
        JOB_DISTRIBUTION_GRAPH,
        JOB_CRIME_GRAPH,
        JOB_HAPPINESS_GRAPH,
    ],
    "Factions": [
        FACTION_DISTRIBUTION_GRAPH,
        FACTION_SUPPORT_GRAPH,
        FACTION_APPROVAL_GRAPH,
        FACTION_CRIME_GRAPH,
        FACTION_HAPPINESS_GRAPH,
    ],
    "Planets": [
        PLANET_POP_DISTRIBUTION_GRAPH,
        PLANET_MIGRATION_GRAPH,
        PLANET_STABILITY_GRAPH,
        PLANET_HAPPINESS_GRAPH,
        PLANET_CRIME_GRAPH,
        PLANET_AMENITIES_GRAPH,
        PLANET_HOUSING_GRAPH,
    ],
    "Science": [
        TECHNOLOGY_PROGRESS_GRAPH,
        TOTAL_RESEARCH_OUTPUT_GRAPH,
        SURVEY_PROGRESS_GRAPH,
        RESEARCH_OUTPUT_GRAPH,
    ],
    "Military": [
        FLEET_SIZE_GRAPH,
        MILITARY_POWER_GRAPH,
    ],
    "Victory": [
        VICTORY_RANK_GRAPH,
        VICTORY_SCORE_GRAPH,
        VICTORY_ECONOMY_SCORE_GRAPH,
    ],
}

# The EmpireProgressionPlotData is cached here for each "active" game
# (one that was requested or had a save file parsed in the current execution).
_CURRENT_EXECUTION_PLOT_DATA: Dict[str, "EmpireProgressionPlotData"] = {}


def get_current_execution_plot_data(game_name: str) -> "EmpireProgressionPlotData":
    """
    Update and retrieve the EmpireProgressionPlotData object stored for the requested game.

    :param game_name: The exact name of a game for which a database is available
    :return:
    """
    global _CURRENT_EXECUTION_PLOT_DATA
    if game_name not in _CURRENT_EXECUTION_PLOT_DATA:
        with models.get_db_session(game_name) as session:
            game = session.query(models.Game).filter_by(game_name=game_name).first()
        if not game:
            logger.warning(f"Warning: Game {game_name} could not be found in database!")
        _CURRENT_EXECUTION_PLOT_DATA[game_name] = EmpireProgressionPlotData(game_name)
        _CURRENT_EXECUTION_PLOT_DATA[game_name].initialize()
    _CURRENT_EXECUTION_PLOT_DATA[game_name].update_with_new_gamestate()
    return _CURRENT_EXECUTION_PLOT_DATA[game_name]


def get_color_vals(key_str: str, range_min: float = 0.1, range_max: float = 1.0) -> Tuple[float, float, float]:
    """
    Generate RGB values for the given identifier. Some special values (tech categories)
    have hardcoded colors to roughly match the game's look and feel.

    For unknown identifiers, a random color is generated. Colors should be consistent, as the
    random instance is seeded with the identifier.

    Optionally, min- and max-values can be passed in to avoid colors that are hard to see against
    the background. This may be configurable in a future version.

    :param key_str: A (unique) identifier with which the color should be associated (e.g. legend entry)
    :param range_min: Minimum value of each color component
    :param range_max: Maximum value of each color component
    :return: RGB values
    """
    if key_str.lower() == "physics":
        r, g, b = COLOR_PHYSICS
    elif key_str.lower() == "society":
        r, g, b = COLOR_SOCIETY
    elif key_str.lower() == "engineering":
        r, g, b = COLOR_ENGINEERING
    elif key_str == GalaxyMapData.UNCLAIMED:  # for unclaimed system in the galaxy map
        r, g, b = 255, 255, 255
    else:
        random.seed(key_str)  # to keep empire colors consistent between plots
        r, g, b = [random.uniform(range_min, range_max) for _ in range(3)]
    return r, g, b


class EmpireProgressionPlotData:
    """
    Responsible for extracting the data for various plots from a single game's database and
    maintaining it in a format immediately suitable for visualization.

    All data is represented as follows:
       - A shared list of in-game dates in years
       - For each metric, a dictionary mapping plot legend ID's (e.g. country names, budget categories etc)
         to lists of float values. List entries for which no data should be shown to the player are
         filled with NaN or 0 values

    The data should be accessed by calling the plot_data_function of some PlotSpecification,
    passing the EmpireProgressionPlotData as an argument.
    """
    DEFAULT_VAL = float("nan")

    def __init__(self, game_name):
        self.game_name = game_name
        self.last_date = None
        self.last_date = -1
        self.dates = None
        self.player_country = None
        self.pop_count = None
        self.owned_planets = None
        self.controlled_systems = None
        self.net_mineral_income = None
        self.net_energy_income = None

        self.net_alloys_income = None
        self.net_consumer_goods_income = None
        self.net_food_income = None

        self.total_research_output = None
        self.tech_count = None
        self.survey_count = None
        self.military_power = None
        self.fleet_size = None

        self.species_distribution = None
        self.happiness_by_species = None
        self.crime_by_species = None

        self.job_distribution = None
        self.happiness_by_job = None
        self.crime_by_job = None

        self.stratum_distribution = None
        self.happiness_by_stratum = None
        self.crime_by_stratum = None

        self.ethos_distribution = None
        self.happiness_by_ethos = None
        self.crime_by_ethos = None

        self.faction_distribution = None
        self.happiness_by_faction = None
        self.crime_by_faction = None
        self.support_by_faction = None
        self.approval_by_faction = None

        self.planet_pop_distribution = None
        self.happiness_by_planet = None
        self.crime_by_planet = None
        self.migration_by_planet = None
        self.free_amenities_by_planet = None
        self.free_housing_by_planet = None
        self.stability_by_planet = None

        self.empire_energy_budget = None
        self.empire_mineral_budget = None
        self.empire_alloys_budget = None
        self.empire_consumer_goods_budget = None
        self.empire_food_budget = None

        self.empire_research_output = None

        self.vic_rank = None
        self.vic_score = None
        self.vic_economy_score = None

        self.data_dicts: List[Tuple[Dict[str, List[float]], float]] = None

        self.show_everything: bool = None
        self.only_show_default_empires: bool = None
        self.plot_time_resolution: int = None
        self.gs_count = -1

    def initialize(self):
        self.last_date = -1
        self.dates: List[float] = []
        self.player_country: str = None

        self.pop_count: Dict[str, List[int]] = {}
        self.owned_planets: Dict[str, List[int]] = {}
        self.controlled_systems: Dict[str, List[int]] = {}

        self.net_mineral_income: Dict[str, List[float]] = {}
        self.net_energy_income: Dict[str, List[float]] = {}
        self.net_alloys_income: Dict[str, List[float]] = {}
        self.net_consumer_goods_income: Dict[str, List[float]] = {}
        self.net_food_income: Dict[str, List[float]] = {}

        self.tech_count: Dict[str, List[int]] = {}
        self.total_research_output: Dict[str, List[int]] = {}
        self.survey_count: Dict[str, List[int]] = {}
        self.military_power: Dict[str, List[float]] = {}
        self.fleet_size: Dict[str, List[float]] = {}

        self.species_distribution: Dict[str, List[float]] = {}
        self.happiness_by_species: Dict[str, List[float]] = {}
        self.crime_by_species: Dict[str, List[float]] = {}

        self.job_distribution: Dict[str, List[float]] = {}
        self.happiness_by_job: Dict[str, List[float]] = {}
        self.crime_by_job: Dict[str, List[float]] = {}

        self.stratum_distribution: Dict[str, List[float]] = {}
        self.happiness_by_stratum: Dict[str, List[float]] = {}
        self.crime_by_stratum: Dict[str, List[float]] = {}

        self.ethos_distribution: Dict[str, List[float]] = {}
        self.happiness_by_ethos: Dict[str, List[float]] = {}
        self.crime_by_ethos: Dict[str, List[float]] = {}

        self.faction_distribution: Dict[str, List[float]] = {}
        self.happiness_by_faction: Dict[str, List[float]] = {}
        self.crime_by_faction: Dict[str, List[float]] = {}
        self.support_by_faction: Dict[str, List[float]] = {}
        self.approval_by_faction: Dict[str, List[float]] = {}

        self.planet_pop_distribution: Dict[str, List[float]] = {}
        self.happiness_by_planet: Dict[str, List[float]] = {}
        self.crime_by_planet: Dict[str, List[float]] = {}
        self.migration_by_planet: Dict[str, List[float]] = {}
        self.free_amenities_by_planet: Dict[str, List[float]] = {}
        self.free_housing_by_planet: Dict[str, List[float]] = {}
        self.stability_by_planet: Dict[str, List[float]] = {}

        self.empire_energy_budget: Dict[str, List[float]] = {}
        self.empire_alloys_budget: Dict[str, List[float]] = {}
        self.empire_consumer_goods_budget: Dict[str, List[float]] = {}
        self.empire_mineral_budget: Dict[str, List[float]] = {}
        self.empire_food_budget: Dict[str, List[float]] = {}

        self.empire_research_output = dict(physics=[], society=[], engineering=[])

        self.vic_rank: Dict[str, List[float]] = {}
        self.vic_score: Dict[str, List[float]] = {}
        self.vic_economy_score: Dict[str, List[float]] = {}

        self.data_dicts = [
            (self.pop_count, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.owned_planets, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.controlled_systems, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.net_mineral_income, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.net_energy_income, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.net_alloys_income, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.net_consumer_goods_income, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.net_food_income, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.tech_count, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.total_research_output, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.survey_count, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.military_power, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.fleet_size, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.species_distribution, 0.0),
            (self.happiness_by_species, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_species, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.job_distribution, 0.0),
            (self.happiness_by_job, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_job, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.stratum_distribution, 0.0),
            (self.happiness_by_stratum, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_stratum, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.ethos_distribution, 0.0),
            (self.happiness_by_ethos, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_ethos, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.faction_distribution, 0.0),
            (self.happiness_by_faction, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_faction, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.support_by_faction, 0.0),
            (self.approval_by_faction, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.empire_energy_budget, 0.0),
            (self.empire_alloys_budget, 0.0),
            (self.empire_consumer_goods_budget, 0.0),
            (self.empire_mineral_budget, 0.0),
            (self.empire_food_budget, 0.0),
            (self.empire_research_output, 0.0),
            (self.vic_rank, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.vic_score, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.vic_economy_score, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.planet_pop_distribution, 0.0),
            (self.happiness_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.crime_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.migration_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.free_amenities_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.free_housing_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),
            (self.stability_by_planet, EmpireProgressionPlotData.DEFAULT_VAL),

        ]

        self.show_everything = config.CONFIG.show_everything
        self.only_show_default_empires = config.CONFIG.only_show_default_empires

    def update_with_new_gamestate(self):
        if (self.show_everything != config.CONFIG.show_everything
                or self.only_show_default_empires != config.CONFIG.only_show_default_empires
                or self.plot_time_resolution != config.CONFIG.plot_time_resolution):
            # reset everything due to changed setting: This forces the program to redraw all plots with the appropriate data:
            logger.info("Detected changed visibility settings: Reassembling plot data")
            self.initialize()
            self.show_everything = config.CONFIG.show_everything
            self.only_show_default_empires = config.CONFIG.only_show_default_empires
            self.plot_time_resolution = config.CONFIG.plot_time_resolution
        num_new_gs = models.count_gamestates_since(self.game_name, self.last_date)
        if self.plot_time_resolution == 0 or num_new_gs < self.plot_time_resolution:
            use_every_nth_gamestate = 1
        else:
            use_every_nth_gamestate = (num_new_gs // self.plot_time_resolution) + 1
        tstart = time.time()
        print(num_new_gs, self.plot_time_resolution, use_every_nth_gamestate)
        for i, gs in enumerate(models.get_gamestates_since(self.game_name, self.last_date)):
            if gs.date <= self.last_date:
                print(f"Warning: Received non-chronological gamestate {gs}")
                continue
            if (self.plot_time_resolution == 0
                    or num_new_gs < self.plot_time_resolution
                    or i % use_every_nth_gamestate == 0
                    or (num_new_gs - i + len(self.dates)) <= self.plot_time_resolution):
                self.process_gamestate(gs)
            self.last_date = gs.date

        print(f"Finished reading {len(self.dates)} gamestates from DB in {time.time() - tstart:5.3f} s.")

    def process_gamestate(self, gs: models.GameState):
        self.dates.append(gs.date / 360.0)  # store date in years for visualization
        for country_data in gs.country_data:
            try:
                if config.CONFIG.only_show_default_empires and country_data.country.country_type != "default":
                    continue
                self._extract_geographic_information(country_data)
                self._extract_economy_information(country_data)
                self._extract_science_information(country_data)
                self._extract_military_information(country_data)

                self._extract_victory_condition_information(country_data)

                if self.player_country is None and country_data.country.is_player:
                    self.player_country = country_data.country.country_name
                if country_data.country.is_player:
                    self._process_pop_stats(gs, country_data)
                    self._extract_player_empire_budget(country_data)
                    self._extract_player_empire_research(country_data)
            except Exception as e:
                print(e)
                print(country_data.country.country_name)

        # Pad every dict with the default value if no real value was added, to keep them consistent with the dates list
        for data_dict, default_val in self.data_dicts:
            for key in data_dict:
                while len(data_dict[key]) < len(self.dates):
                    data_dict[key].append(default_val)

    def _extract_player_empire_budget(self, country_data: models.CountryData):
        # For some reason, some budget values have to be halved...
        for budget_item in country_data.budget:
            assert isinstance(budget_item, models.BudgetItem)
            self._add_new_value_to_data_dict(
                self.empire_energy_budget,
                budget_item.name,
                budget_item.net_energy,
                default_val=0.0,
            )
            self._add_new_value_to_data_dict(
                self.empire_mineral_budget,
                budget_item.name,
                budget_item.net_minerals,
                default_val=0.0,
            )
            self._add_new_value_to_data_dict(
                self.empire_alloys_budget,
                budget_item.name,
                budget_item.net_alloys,
                default_val=0.0,
            )
            self._add_new_value_to_data_dict(
                self.empire_consumer_goods_budget,
                budget_item.name,
                budget_item.net_consumer_goods,
                default_val=0.0,
            )
            self._add_new_value_to_data_dict(
                self.empire_food_budget,
                budget_item.name,
                budget_item.net_food,
                default_val=0.0,
            )

    def _process_pop_stats(self, game_state: models.GameState, country_data: models.CountryData):
        for stats in country_data.pop_stats_species:
            assert isinstance(stats, models.PopStatsBySpecies)
            self._add_new_value_to_data_dict(self.species_distribution,
                                             stats.species.species_name,
                                             stats.pop_count,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.happiness_by_species,
                                             stats.species.species_name,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_species,
                                             stats.species.species_name,
                                             stats.crime)
        for stats in country_data.pop_stats_job:
            assert isinstance(stats, models.PopStatsByJob)
            self._add_new_value_to_data_dict(self.job_distribution,
                                             stats.job_description,
                                             stats.pop_count,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.happiness_by_job,
                                             stats.job_description,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_job,
                                             stats.job_description,
                                             stats.crime)
        for stats in country_data.pop_stats_stratum:
            assert isinstance(stats, models.PopStatsByStratum)
            self._add_new_value_to_data_dict(self.stratum_distribution,
                                             stats.stratum,
                                             stats.pop_count,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.happiness_by_stratum,
                                             stats.stratum,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_stratum,
                                             stats.stratum,
                                             stats.crime)
        for stats in country_data.pop_stats_ethos:
            assert isinstance(stats, models.PopStatsByEthos)
            self._add_new_value_to_data_dict(self.ethos_distribution,
                                             stats.ethos,
                                             stats.pop_count,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.happiness_by_ethos,
                                             stats.ethos,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_ethos,
                                             stats.ethos,
                                             stats.crime)
        for stats in country_data.pop_stats_faction:
            assert isinstance(stats, models.PopStatsByFaction)
            faction_name = stats.faction.faction_name
            self._add_new_value_to_data_dict(self.faction_distribution,
                                             faction_name,
                                             stats.pop_count)
            self._add_new_value_to_data_dict(self.happiness_by_faction,
                                             faction_name,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_faction,
                                             faction_name,
                                             stats.crime)
            self._add_new_value_to_data_dict(self.support_by_faction,
                                             faction_name,
                                             stats.support,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.approval_by_faction,
                                             faction_name,
                                             stats.faction_approval)

        for stats in game_state.planet_stats:
            assert isinstance(stats, models.PlanetStats)

            planet_name = stats.planet.name
            self._add_new_value_to_data_dict(self.planet_pop_distribution,
                                             planet_name,
                                             stats.pop_count)
            self._add_new_value_to_data_dict(self.happiness_by_planet,
                                             planet_name,
                                             stats.happiness)
            self._add_new_value_to_data_dict(self.crime_by_planet,
                                             planet_name,
                                             stats.crime)
            self._add_new_value_to_data_dict(self.migration_by_planet,
                                             planet_name,
                                             stats.migration,
                                             default_val=0)
            self._add_new_value_to_data_dict(self.free_amenities_by_planet,
                                             planet_name,
                                             stats.free_amenities)
            self._add_new_value_to_data_dict(self.free_housing_by_planet,
                                             planet_name,
                                             stats.free_housing)
            self._add_new_value_to_data_dict(self.stability_by_planet,
                                             planet_name,
                                             stats.stability)

    def iterate_data(self, plot_spec: PlotSpecification) -> Iterable[Tuple[str, List[float], List[float]]]:
        data_dict = plot_spec.plot_data_function(self)
        for key, data in data_dict.items():
            # substitute some special values: (robots from the limbo event chain)
            if key == "ROBOT_POP_SPECIES_1":
                key = "Robot"
            elif key == "ROBOT_POP_SPECIES_2":
                key = "Droid"
            elif key == "ROBOT_POP_SPECIES_3":
                key = "Synth"
            if data:
                yield key, self.dates, data

    def data_sorted_by_last_value(self, plot_spec: PlotSpecification) -> List[Tuple[str, List[float], List[float]]]:
        unsorted_data = list(self.iterate_data(plot_spec))
        return sorted(unsorted_data, key=lambda key_x_y_tup: (key_x_y_tup[2][-1], key_x_y_tup[0]))

    def _extract_geographic_information(self, country_data: models.CountryData):
        if self.show_everything or country_data.show_geography_info():
            new_planet_count = country_data.owned_planets
            new_system_count = country_data.controlled_systems
        else:
            new_planet_count = EmpireProgressionPlotData.DEFAULT_VAL
            new_system_count = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.owned_planets, country_data.country.country_name, new_planet_count)
        self._add_new_value_to_data_dict(self.controlled_systems, country_data.country.country_name, new_system_count)

    def _extract_economy_information(self, country_data: models.CountryData):
        if self.show_everything or country_data.show_economic_info():
            new_energy_income = country_data.net_energy
            new_mineral_income = country_data.net_minerals
            new_alloys_income = country_data.net_alloys
            new_consumer_goods_income = country_data.net_consumer_goods
            new_food_income = country_data.net_food
        else:
            new_energy_income = EmpireProgressionPlotData.DEFAULT_VAL
            new_mineral_income = EmpireProgressionPlotData.DEFAULT_VAL
            new_alloys_income = EmpireProgressionPlotData.DEFAULT_VAL
            new_consumer_goods_income = EmpireProgressionPlotData.DEFAULT_VAL
            new_food_income = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.net_energy_income, country_data.country.country_name, new_energy_income)
        self._add_new_value_to_data_dict(self.net_mineral_income, country_data.country.country_name, new_mineral_income)
        self._add_new_value_to_data_dict(self.net_alloys_income, country_data.country.country_name, new_alloys_income)
        self._add_new_value_to_data_dict(self.net_consumer_goods_income, country_data.country.country_name, new_consumer_goods_income)
        self._add_new_value_to_data_dict(self.net_food_income, country_data.country.country_name, new_food_income)

    def _extract_science_information(self, country_data: models.CountryData):
        if self.show_everything or country_data.show_tech_info():
            new_tech_count = country_data.tech_count
            new_research_output = country_data.net_society_research + country_data.net_physics_research + country_data.net_engineering_research
            new_exploration_progress = country_data.exploration_progress
        else:
            new_tech_count = EmpireProgressionPlotData.DEFAULT_VAL
            new_research_output = EmpireProgressionPlotData.DEFAULT_VAL
            new_exploration_progress = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.tech_count, country_data.country.country_name, new_tech_count)
        self._add_new_value_to_data_dict(self.total_research_output, country_data.country.country_name, new_research_output)
        self._add_new_value_to_data_dict(self.survey_count, country_data.country.country_name, new_exploration_progress)

    def _extract_military_information(self, country_data: models.CountryData):
        if self.show_everything or country_data.show_military_info():
            new_military_strength = country_data.military_power
            new_fleet_size = country_data.fleet_size
        else:
            new_military_strength = EmpireProgressionPlotData.DEFAULT_VAL
            new_fleet_size = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.military_power, country_data.country.country_name, new_military_strength)
        self._add_new_value_to_data_dict(self.fleet_size, country_data.country.country_name, new_fleet_size)

    def _extract_victory_condition_information(self, country_data: models.CountryData):
        if self.show_everything or country_data.show_geography_info():
            vic_rank = country_data.victory_rank
            vic_score = country_data.victory_score
            vic_economy_score = country_data.economy_power
        else:
            vic_rank = EmpireProgressionPlotData.DEFAULT_VAL
            vic_score = EmpireProgressionPlotData.DEFAULT_VAL
            vic_economy_score = EmpireProgressionPlotData.DEFAULT_VAL
        self._add_new_value_to_data_dict(self.vic_rank, country_data.country.country_name, vic_rank)
        self._add_new_value_to_data_dict(self.vic_score, country_data.country.country_name, vic_score)
        self._add_new_value_to_data_dict(self.vic_economy_score, country_data.country.country_name, vic_economy_score)

    def _extract_player_empire_research(self, country_data: models.CountryData):
        self.empire_research_output["physics"].append(country_data.net_physics_research)
        self.empire_research_output["society"].append(country_data.net_society_research)
        self.empire_research_output["engineering"].append(country_data.net_engineering_research)

    def _add_new_value_to_data_dict(self, data_dict, key, new_val, default_val=DEFAULT_VAL):
        if key not in data_dict:
            if new_val == default_val:
                return
            data_dict[key] = [default_val for _ in range(len(self.dates) - 1)]
        if len(data_dict[key]) >= len(self.dates):
            logger.info(f"Ignoring duplicate value for {key}.")
            return
        data_dict[key].append(new_val)


_GALAXY_DATA: Dict[str, "GalaxyMapData"] = {}


def get_galaxy_data(game_name: str) -> "GalaxyMapData":
    """
    Similar to get_current_execution_plot_data, the GalaxyMapData for
    each game is cached in the _GALAXY_DATA dictionary.
    """
    if game_name not in _GALAXY_DATA:
        _GALAXY_DATA[game_name] = GalaxyMapData(game_name)
        _GALAXY_DATA[game_name].initialize_galaxy_graph()
    return _GALAXY_DATA[game_name]


@dataclasses.dataclass
class _SystemOwnership:
    """ Minimal representation of SystemOwnership that is not tied to a database session. """
    country: str
    system_id: int
    start: int
    end: int


class GalaxyMapData:
    """ Maintains the data for the historical galaxy map. """
    UNCLAIMED = "Unclaimed Systems"

    def __init__(self, game_id: str):
        self.game_id = game_id
        self.galaxy_graph: nx.Graph = None
        self._game_state_model = None
        self._cache_valid_date = -1
        self._owner_cache: Dict[int, List[_SystemOwnership]] = None

    def initialize_galaxy_graph(self):
        start_time = time.clock()
        self._owner_cache = {}
        self.galaxy_graph = nx.Graph()
        with models.get_db_session(self.game_id) as session:
            for system in session.query(models.System).all():
                assert isinstance(system, models.System)  # to remove pycharm warnings
                self.galaxy_graph.add_node(
                    system.system_id_in_game,
                    name=system.name,
                    country=GalaxyMapData.UNCLAIMED,
                    pos=[-system.coordinate_x, -system.coordinate_y],
                )
            for hl in session.query(models.HyperLane).all():
                sys_one, sys_two = hl.system_one.system_id_in_game, hl.system_two.system_id_in_game
                self.galaxy_graph.add_edge(sys_one, sys_two, country=self.UNCLAIMED)
        logger.debug(f"Initialized galaxy graph in {time.clock() - start_time} seconds.")

    def get_graph_for_date(self, time_days: int) -> nx.Graph:
        start_time = time.clock()
        if time_days > self._cache_valid_date:
            self._update_cache()
            logger.debug(f"Updated System Ownership Cache in {time.clock() - start_time} seconds.")
        systems_by_owner = self._get_system_ids_by_owner(time_days)
        owner_by_system = {}
        for country, nodes in systems_by_owner.items():
            for node in nodes:
                owner_by_system[node] = country
                self.galaxy_graph.nodes[node]["country"] = country

        for edge in self.galaxy_graph.edges:
            i, j = edge
            i_country = owner_by_system.get(i, self.UNCLAIMED)
            j_country = owner_by_system.get(j, self.UNCLAIMED)
            if i_country == j_country:
                self.galaxy_graph.edges[edge]["country"] = i_country
            else:
                self.galaxy_graph.edges[edge]["country"] = self.UNCLAIMED
        logger.info(f"Updated networkx graph in {time.clock() - start_time} seconds.")
        return self.galaxy_graph

    def _get_system_ids_by_owner(self, time_days):
        owned_systems = set()
        systems_by_owner = {GalaxyMapData.UNCLAIMED: set()}
        for system_id, ownership_list in self._owner_cache.items():
            for ownership in ownership_list:
                if not ownership.start <= time_days <= ownership.end:
                    continue
                owned_systems.add(system_id)
                if ownership.country not in systems_by_owner:
                    systems_by_owner[ownership.country] = set()
                systems_by_owner[ownership.country].add(system_id)
        systems_by_owner[GalaxyMapData.UNCLAIMED] |= set(self.galaxy_graph.nodes) - owned_systems
        self._game_state_model = None
        return systems_by_owner

    def _update_cache(self):
        logger.info("Updating Cache")
        # would be nicer to properly update the cache, but for now it is simpler to just rebuild it when we request a new date.
        self._owner_cache = {}
        self._cache_valid_date = -1
        with models.get_db_session(self.game_id) as session:
            db_ownerships = session.query(models.SystemOwnership).order_by(
                models.SystemOwnership.start_date_days
            ).all()
            for ownership in db_ownerships:
                self._cache_valid_date = max(self._cache_valid_date, ownership.end_date_days)
                system_id = ownership.system.system_id_in_game
                name = self._get_country_name_from_id(ownership, ownership.end_date_days)
                if system_id not in self._owner_cache:
                    self._owner_cache[system_id] = []
                    if ownership.start_date_days > 0:
                        self._owner_cache[system_id].append(_SystemOwnership(
                            country=self.UNCLAIMED,
                            system_id=system_id,
                            start=0,
                            end=ownership.start_date_days,
                        ))
                self._owner_cache[system_id].append(_SystemOwnership(
                    country=name,
                    system_id=system_id,
                    start=ownership.start_date_days,
                    end=ownership.end_date_days,
                ))

    def _get_country_name_from_id(self, db_ownership: models.SystemOwnership, time_days):
        country = db_ownership.country
        if country is None:
            logger.warning(f"{db_ownership} has no country!")
            return GalaxyMapData.UNCLAIMED
        if config.CONFIG.show_everything:
            return country.country_name
        if country.first_player_contact_date is None or country.first_player_contact_date > time_days:
            return GalaxyMapData.UNCLAIMED
        return country.country_name
