import abc
import dataclasses
import enum
import logging
import random
import time
from typing import List, Dict, Callable, Any, Tuple, Iterable, Union

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
    data_container_factory: Callable[[], "AbstractPlotDataContainer"]
    style: PlotStyle
    yrange: Tuple[float, float] = None

    x_axis_label: str = "Time (years after 2200.01.01)"
    y_axis_label: str = ""


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
    Responsible for maintaining a single game's data for every available PlotSpecification.

    Data is organized as a dictionary mapping the plot_id of the PlotSpecification class
    to a DataContainer instance (defined below this class).
    """

    def __init__(self, game_name: str, plot_specifications: Dict[str, List[PlotSpecification]] = None):
        if plot_specifications is None:
            plot_specifications = THEMATICALLY_GROUPED_PLOTS
        self.game_name: str = game_name
        self.plot_specifications = plot_specifications

        self.last_date: float = None
        self._loaded_gamestates: int = None
        self.show_everything: bool = None
        self.only_show_default_empires: bool = None
        self.plot_time_resolution: bool = None

        self.data_containers_by_plot_id: Dict[str, AbstractPlotDataContainer] = None

    def initialize(self):
        self.last_date = -float("inf")
        self._loaded_gamestates = 0
        self.show_everything = config.CONFIG.show_everything
        self.only_show_default_empires = config.CONFIG.only_show_default_empires
        self.plot_time_resolution = config.CONFIG.plot_time_resolution

        self.data_containers_by_plot_id = {}
        for ps_list in self.plot_specifications.values():
            for plot_spec in ps_list:
                self.data_containers_by_plot_id[plot_spec.plot_id] = plot_spec.data_container_factory()

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
        t_start = time.time()
        num_loaded_gs = 0
        for i, gs in enumerate(models.get_gamestates_since(self.game_name, self.last_date)):
            if gs.date <= self.last_date:
                logger.warning(f"Received gamestate with date {models.days_to_date(gs.date)}, last known date is {models.days_to_date(self.last_date)}")
                continue
            if (self.plot_time_resolution == 0
                    or num_new_gs < self.plot_time_resolution
                    or i % use_every_nth_gamestate == 0
                    or (num_new_gs - i + self._loaded_gamestates) <= self.plot_time_resolution):
                num_loaded_gs += 1
                self._loaded_gamestates += 1
                for data_container in self.data_containers_by_plot_id.values():
                    data_container.extract_data_from_gamestate(gs)
            self.last_date = gs.date
        logger.info(f"Loaded {num_loaded_gs} new gamestates from the database in {time.time() - t_start:5.3f} seconds. ({self._loaded_gamestates} gamestates in total)")

    def get_data_for_plot(self, ps: PlotSpecification):
        container = self.data_containers_by_plot_id.get(ps.plot_id)
        if container is None:
            logger.info(f"No data available for plot {ps.title} ({ps.plot_id}).")
            return
        return container.iterate_traces()


class AbstractPlotDataContainer(abc.ABC):
    DEFAULT_VAL = float("nan")

    def __init__(self):
        self.dates: List[float] = []
        self.data_dict: Dict[str, List[float]] = {}

    def iterate_traces(self):
        for key, values in self.data_dict.items():
            yield key, self.dates, values

    def _add_new_value_to_data_dict(self, key, new_val, default_val=DEFAULT_VAL):
        if key not in self.data_dict:
            if new_val == default_val:
                return
            self.data_dict[key] = [default_val for _ in range(len(self.dates) - 1)]
        if len(self.data_dict[key]) >= len(self.dates):
            logger.info(f"Ignoring duplicate value for {key}.")
            return
        self.data_dict[key].append(new_val)

    def _pad_data_dict(self, default_val=DEFAULT_VAL):
        # Pad every dict with the default value if no real value was added, to keep them consistent with the dates list
        for key in self.data_dict:
            while len(self.data_dict[key]) < len(self.dates):
                self.data_dict[key].append(default_val)

    @abc.abstractmethod
    def extract_data_from_gamestate(self, gs: models.GameState):
        pass


class AbstractPerCountryDataContainer(AbstractPlotDataContainer, abc.ABC):
    def extract_data_from_gamestate(self, gs: models.GameState):
        added_new_val = False
        self.dates.append(gs.date / 360.0)
        for cd in gs.country_data:
            try:
                if config.CONFIG.only_show_default_empires and cd.country.country_type != "default":
                    continue
                new_val = self._get_value_from_countrydata(cd)
                if new_val is not None:
                    added_new_val = True
                    self._add_new_value_to_data_dict(cd.country.country_name, new_val, default_val=self.DEFAULT_VAL)
            except Exception as e:
                print(e)
                print(cd.country.country_name)
        if not added_new_val:
            self.dates.pop()  # if nothing was added, we don't need to remember the date.
        self._pad_data_dict(default_val=self.DEFAULT_VAL)

    @abc.abstractmethod
    def _get_value_from_countrydata(self, cd: models.CountryData) -> Union[None, float]:
        pass


class PlanetCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_geography_info():
            return cd.owned_planets


class SystemCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_geography_info():
            return cd.controlled_systems


class TotalEnergyIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_economic_info():
            return cd.net_energy


class TotalMineralsIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_economic_info():
            return cd.net_minerals


class TotalAlloysIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_economic_info():
            return cd.net_alloys


class TotalConsumerGoodsIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_economic_info():
            return cd.net_consumer_goods


class TotalFoodIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_economic_info():
            return cd.net_food


class TechCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_tech_info():
            return cd.tech_count


class ExploredSystemsCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_tech_info():
            return cd.exploration_progress


class TotalScienceOutputDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_tech_info():
            return cd.net_physics_research + cd.net_society_research + cd.net_engineering_research


class FleetSizeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_military_info():
            return cd.fleet_size


class MilitaryPowerDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_military_info():
            return cd.military_power


class VictoryScoreDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_geography_info():
            return cd.victory_score


class EconomyScoreDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_geography_info():
            return cd.economy_power


class VictoryRankDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: models.CountryData):
        if config.CONFIG.show_everything or cd.show_geography_info():
            return cd.victory_rank


class AbstractPlayerInfoDataContainer(AbstractPlotDataContainer, abc.ABC):
    def extract_data_from_gamestate(self, gs: models.GameState):
        player_cd = None
        for cd in gs.country_data:
            if cd.country.is_player:
                player_cd = cd
                break
        if player_cd is None:
            logger.error("Could not identify player country! Abort...")
            return

        self.dates.append(gs.date / 360.0)
        try:
            for key, new_val in self._iterate_budgetitems(player_cd):
                if new_val is not None:
                    self._add_new_value_to_data_dict(key, new_val, default_val=self.DEFAULT_VAL)
        except Exception as e:
            print(e)
            print(player_cd.country.country_name)
        self._pad_data_dict(self.DEFAULT_VAL)

    @abc.abstractmethod
    def _iterate_budgetitems(self, cd: models.CountryData) -> Iterable[Tuple[str, float]]:
        pass


class ScienceOutputByFieldDataContainer(AbstractPlayerInfoDataContainer):
    DEFAULT_VAL = 0.0

    def _iterate_budgetitems(self, cd: models.CountryData) -> Iterable[Tuple[str, float]]:
        yield "Physics", cd.net_physics_research
        yield "Society", cd.net_society_research
        yield "Engineering", cd.net_engineering_research


class AbstractEconomyBudgetDataContainer(AbstractPlayerInfoDataContainer, abc.ABC):
    DEFAULT_VAL = 0.0

    def _iterate_budgetitems(self, cd: models.CountryData) -> Iterable[Tuple[str, float]]:
        for budget_item in cd.budget:
            val = self._get_value_from_budgetitem(budget_item)
            if val == 0.0:
                val = None
            yield (budget_item.name, val)

    @abc.abstractmethod
    def _get_value_from_budgetitem(self, bi: models.BudgetItem) -> float:
        pass


class EnergyBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_energy


class MineralsBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_minerals


class AlloysBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_alloys


class ConsumerGoodsBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_consumer_goods


class FoodBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_food


class VolatileMotesBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_volatile_motes


class ExoticGasesBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_exotic_gases


class RareCrystalsBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_rare_crystals


class LivingMetalBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_living_metal


class ZroBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_zro


class DarkMatterBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_dark_matter


class NanitesBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_nanites


class UnityBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_unity


class InfluenceBudgetDataContainer(AbstractEconomyBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: models.BudgetItem):
        return bi.net_influence


PopStatsUnionType = Union[
    models.PopStatsByFaction,
    models.PopStatsByEthos,
    models.PopStatsByStratum,
    models.PopStatsBySpecies,
    models.PlanetStats,
]


class AbstractPopStatsDataContainer(AbstractPlayerInfoDataContainer, abc.ABC):
    def _iterate_budgetitems(self, cd: models.CountryData) -> Iterable[Tuple[str, float]]:
        for pop_stats in self._iterate_popstats(cd):
            key = self._get_key_from_popstats(pop_stats)
            val = self._get_value_from_popstats(pop_stats)
            yield (key, val)

    @abc.abstractmethod
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[PopStatsUnionType]:
        pass

    @abc.abstractmethod
    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        pass

    @abc.abstractmethod
    def _get_value_from_popstats(self, ps: PopStatsUnionType) -> float:
        pass


class AbstractPopStatsBySpeciesDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PopStatsBySpecies]:
        return iter(cd.pop_stats_species)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PopStatsBySpecies)
        return ps.species.species_name


class SpeciesDistributionDataContainer(AbstractPopStatsBySpeciesDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsBySpecies):
        return ps.pop_count


class SpeciesHappinessDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsBySpecies):
        return ps.happiness


class SpeciesPowerDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsBySpecies):
        return ps.power


class SpeciesCrimeDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsBySpecies):
        return ps.crime


class AbstractPopStatsByFactionDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PopStatsByFaction]:
        return iter(cd.pop_stats_faction)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PopStatsByFaction)
        return ps.faction.faction_name


class FactionDistributionDataContainer(AbstractPopStatsByFactionDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.pop_count


class FactionSupportDataContainer(AbstractPopStatsByFactionDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.support


class FactionApprovalDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.faction_approval


class FactionHappinessDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.happiness


class FactionPowerDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.power


class FactionCrimeDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByFaction):
        return ps.crime


class AbstractPopStatsByJobDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PopStatsByJob]:
        return iter(cd.pop_stats_job)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PopStatsByJob)
        return ps.job_description


class JobDistributionDataContainer(AbstractPopStatsByJobDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsByJob):
        return ps.pop_count


class JobHappinessDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByJob):
        return ps.happiness


class JobPowerDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByJob):
        return ps.power


class JobCrimeDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByJob):
        return ps.crime


class AbstractPopStatsByPlanetDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PlanetStats]:
        return iter(cd.game_state.planet_stats)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PlanetStats)
        return ps.planet.name


class PlanetDistributionDataContainer(AbstractPopStatsByPlanetDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.pop_count


class PlanetHappinessDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.happiness


class PlanetPowerDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.power


class PlanetCrimeDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.crime


class PlanetMigrationDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.migration


class PlanetAmenitiesDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.free_amenities


class PlanetHousingDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.free_housing


class PlanetStabilityDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: models.PlanetStats):
        return ps.stability


class AbstractPopStatsByEthosDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PopStatsByEthos]:
        return iter(cd.pop_stats_ethos)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PopStatsByEthos)
        return ps.ethos


class EthosDistributionDataContainer(AbstractPopStatsByEthosDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsByEthos):
        return ps.pop_count


class EthosHappinessDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByEthos):
        return ps.happiness


class EthosPowerDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByEthos):
        return ps.power


class EthosCrimeDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByEthos):
        return ps.crime


class AbstractPopStatsByStratumDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(self, cd: models.CountryData) -> Iterable[models.PopStatsByStratum]:
        return iter(cd.pop_stats_stratum)

    def _get_key_from_popstats(self, ps: PopStatsUnionType) -> str:
        assert isinstance(ps, models.PopStatsByStratum)
        return ps.stratum


class StratumDistributionDataContainer(AbstractPopStatsByStratumDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: models.PopStatsByStratum):
        return ps.pop_count


class StratumHappinessDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByStratum):
        return ps.happiness


class StratumPowerDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByStratum):
        return ps.power


class StratumCrimeDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: models.PopStatsByStratum):
        return ps.crime


### Define PlotSpecifications for all currently supported plots
PLANET_COUNT_GRAPH = PlotSpecification(
    plot_id='planet-count',
    title="Owned Planets",
    data_container_factory=lambda: PlanetCountDataContainer(),
    style=PlotStyle.line,
)
SYSTEM_COUNT_GRAPH = PlotSpecification(
    plot_id='system-count',
    title="Controlled Systems",
    data_container_factory=lambda: SystemCountDataContainer(),
    style=PlotStyle.line,
)
NET_MINERAL_INCOME_GRAPH = PlotSpecification(
    plot_id='net-mineral-income',
    title="Net Mineral Income",
    data_container_factory=lambda: TotalMineralsIncomeDataContainer(),
    style=PlotStyle.line,
)
NET_ENERGY_INCOME_GRAPH = PlotSpecification(
    plot_id='net-energy-income',
    title="Net Energy Income",
    data_container_factory=lambda: TotalEnergyIncomeDataContainer(),
    style=PlotStyle.line,
)
NET_ALLOYS_INCOME_GRAPH = PlotSpecification(
    plot_id='net-alloys-income',
    title="Net Alloys Income",
    data_container_factory=lambda: TotalAlloysIncomeDataContainer(),
    style=PlotStyle.line,
)
NET_CONSUMER_GOODS_INCOME_GRAPH = PlotSpecification(
    plot_id='net-consumer-goods-income',
    title="Net Consumer Goods Income",
    data_container_factory=lambda: TotalConsumerGoodsIncomeDataContainer(),
    style=PlotStyle.line,
)
NET_FOOD_INCOME_GRAPH = PlotSpecification(
    plot_id='net-food-income',
    title="Net Food Income",
    data_container_factory=lambda: TotalFoodIncomeDataContainer(),
    style=PlotStyle.line,
)
TECHNOLOGY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='tech-count',
    title="Researched Technologies",
    data_container_factory=lambda: TechCountDataContainer(),
    style=PlotStyle.line,
)
RESEARCH_OUTPUT_BY_CATEGORY_GRAPH = PlotSpecification(
    plot_id='empire-research-output',
    title="Research Output",
    data_container_factory=lambda: ScienceOutputByFieldDataContainer(),
    style=PlotStyle.stacked,
)
RESEARCH_OUTPUT_GRAPH = PlotSpecification(
    plot_id='empire-research-output-comparison',
    title="Total Research Output",
    data_container_factory=lambda: TotalScienceOutputDataContainer(),
    style=PlotStyle.line,
)
SURVEY_PROGRESS_GRAPH = PlotSpecification(
    plot_id='survey-count',
    title="Exploration",
    data_container_factory=lambda: ExploredSystemsCountDataContainer(),
    style=PlotStyle.line,
)
MILITARY_POWER_GRAPH = PlotSpecification(
    plot_id='military-power',
    title="Military Strength",
    data_container_factory=lambda: MilitaryPowerDataContainer(),
    style=PlotStyle.line,
)
FLEET_SIZE_GRAPH = PlotSpecification(
    plot_id='fleet-size',
    title="Fleet Size",
    data_container_factory=lambda: FleetSizeDataContainer(),
    style=PlotStyle.line,
)
SPECIES_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-species-distribution',
    title="Species Demographics",
    data_container_factory=lambda: SpeciesDistributionDataContainer(),
    style=PlotStyle.stacked,
)
SPECIES_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-species-happiness',
    title="Happiness by Species",
    data_container_factory=lambda: SpeciesHappinessDataContainer(),
    style=PlotStyle.line,
)
SPECIES_POWER_GRAPH = PlotSpecification(
    plot_id='empire-species-power',
    title="Power by Species",
    data_container_factory=lambda: SpeciesPowerDataContainer(),
    style=PlotStyle.line,
)
SPECIES_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-species-crime',
    title="Crime by Species",
    data_container_factory=lambda: SpeciesCrimeDataContainer(),
    style=PlotStyle.line,
)
FACTION_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-faction-distribution',
    title="Faction Demographics",
    data_container_factory=lambda: FactionDistributionDataContainer(),
    style=PlotStyle.stacked,
)
FACTION_SUPPORT_GRAPH = PlotSpecification(
    plot_id='empire-faction-support',
    title="Faction Support",
    data_container_factory=lambda: FactionSupportDataContainer(),
    style=PlotStyle.stacked,
)
FACTION_APPROVAL_GRAPH = PlotSpecification(
    plot_id='empire-faction-approval',
    title="Faction Approval",
    data_container_factory=lambda: FactionApprovalDataContainer(),
    style=PlotStyle.line,
)
FACTION_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-faction-crime',
    title="Crime by Faction",
    data_container_factory=lambda: FactionCrimeDataContainer(),
    style=PlotStyle.line,
)
FACTION_POWER_GRAPH = PlotSpecification(
    plot_id='empire-faction-power',
    title="Power by Faction",
    data_container_factory=lambda: FactionPowerDataContainer(),
    style=PlotStyle.line,
)
FACTION_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-faction-happiness',
    title="Happiness by Faction",
    data_container_factory=lambda: FactionHappinessDataContainer(),
    style=PlotStyle.line,
)
PLANET_POP_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-planet-pop-distribution',
    title="Population by Planet",
    data_container_factory=lambda: PlanetDistributionDataContainer(),
    style=PlotStyle.stacked,
)
PLANET_MIGRATION_GRAPH = PlotSpecification(
    plot_id='empire-planet-migration',
    title="Migration by Planet",
    data_container_factory=lambda: PlanetMigrationDataContainer(),
    style=PlotStyle.line,
)
PLANET_AMENITIES_GRAPH = PlotSpecification(
    plot_id='empire-planet-amenities',
    title="Free Amenities by Planet",
    data_container_factory=lambda: PlanetAmenitiesDataContainer(),
    style=PlotStyle.line,
)
PLANET_STABILITY_GRAPH = PlotSpecification(
    plot_id='empire-planet-stability',
    title="Stability by Planet",
    data_container_factory=lambda: PlanetStabilityDataContainer(),
    style=PlotStyle.line,
)
PLANET_HOUSING_GRAPH = PlotSpecification(
    plot_id='empire-planet-housing',
    title="Free Housing by Planet",
    data_container_factory=lambda: PlanetHousingDataContainer(),
    style=PlotStyle.line,
)
PLANET_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-planet-crime',
    title="Crime by Planet",
    data_container_factory=lambda: PlanetCrimeDataContainer(),
    style=PlotStyle.line,
)
PLANET_POWER_GRAPH = PlotSpecification(
    plot_id='empire-planet-power',
    title="Power by Planet",
    data_container_factory=lambda: PlanetPowerDataContainer(),
    style=PlotStyle.line,
)
PLANET_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-planet-happiness',
    title="Happiness by Planet",
    data_container_factory=lambda: PlanetHappinessDataContainer(),
    style=PlotStyle.line,
)
ETHOS_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-ethos-distribution',
    title="Ethos Demographics",
    data_container_factory=lambda: EthosDistributionDataContainer(),
    style=PlotStyle.stacked,
)

ETHOS_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-ethos-crime',
    title="Crime by Ethos",
    data_container_factory=lambda: EthosCrimeDataContainer(),
    style=PlotStyle.line,
)

ETHOS_POWER_GRAPH = PlotSpecification(
    plot_id='empire-ethos-power',
    title="Power by Ethos",
    data_container_factory=lambda: EthosPowerDataContainer(),
    style=PlotStyle.line,
)
ETHOS_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-ethos-happiness',
    title="Happiness by Ethos",
    data_container_factory=lambda: EthosHappinessDataContainer(),
    style=PlotStyle.line,
)
STRATA_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-strata-distribution',
    title="Stratum Demographics",
    data_container_factory=lambda: StratumDistributionDataContainer(),
    style=PlotStyle.stacked,
)
STRATA_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-strata-crime',
    title="Crime by Stratum",
    data_container_factory=lambda: StratumCrimeDataContainer(),
    style=PlotStyle.line,
)
STRATA_POWER_GRAPH = PlotSpecification(
    plot_id='empire-strata-power',
    title="Power by Stratum",
    data_container_factory=lambda: StratumPowerDataContainer(),
    style=PlotStyle.line,
)
STRATA_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-strata-happiness',
    title="Happiness by Stratum",
    data_container_factory=lambda: StratumHappinessDataContainer(),
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
JOB_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id='empire-job-distribution',
    title="Job Demographics",
    data_container_factory=lambda: JobDistributionDataContainer(),
    style=PlotStyle.stacked,
)
JOB_CRIME_GRAPH = PlotSpecification(
    plot_id='empire-job-crime',
    title="Crime by Job",
    data_container_factory=lambda: JobCrimeDataContainer(),
    style=PlotStyle.line,
)
JOB_POWER_GRAPH = PlotSpecification(
    plot_id='empire-job-power',
    title="Power by Job",
    data_container_factory=lambda: JobPowerDataContainer(),
    style=PlotStyle.line,
)
JOB_HAPPINESS_GRAPH = PlotSpecification(
    plot_id='empire-job-happiness',
    title="Happiness by Job",
    data_container_factory=lambda: JobHappinessDataContainer(),
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
ENERGY_BUDGET = PlotSpecification(
    plot_id='empire-energy-budget',
    title="Energy Budget",
    data_container_factory=lambda: EnergyBudgetDataContainer(),
    style=PlotStyle.budget,
)
MINERAL_BUDGET = PlotSpecification(
    plot_id='empire-mineral-budget',
    title="Mineral Budget",
    data_container_factory=lambda: MineralsBudgetDataContainer(),
    style=PlotStyle.budget,
)
CONSUMER_GOODS_BUDGET = PlotSpecification(
    plot_id='empire-consumer-goods-budget',
    title="Consumer Goods Budget",
    data_container_factory=lambda: ConsumerGoodsBudgetDataContainer(),
    style=PlotStyle.budget,
)
ALLOYS_BUDGET = PlotSpecification(
    plot_id='empire-alloys-budget',
    title="Alloys Budget",
    data_container_factory=lambda: AlloysBudgetDataContainer(),
    style=PlotStyle.budget,
)
FOOD_BUDGET = PlotSpecification(
    plot_id='empire-food-budget',
    title="Food",
    data_container_factory=lambda: FoodBudgetDataContainer(),
    style=PlotStyle.budget,
)
VOLATILE_MOTES_BUDGET = PlotSpecification(
    plot_id='empire-volatile-motes-budget',
    title="Volatile Motes",
    data_container_factory=lambda: VolatileMotesBudgetDataContainer(),
    style=PlotStyle.budget,
)
EXOTIC_GASES_BUDGET = PlotSpecification(
    plot_id='empire-exotic-gas-budget',
    title="Exotic Gases",
    data_container_factory=lambda: ExoticGasesBudgetDataContainer(),
    style=PlotStyle.budget,
)
RARE_CRYSTALS_BUDGET = PlotSpecification(
    plot_id='empire-rare-crystals-budget',
    title="Rare Crystals",
    data_container_factory=lambda: RareCrystalsBudgetDataContainer(),
    style=PlotStyle.budget,
)
LIVING_METAL_BUDGET = PlotSpecification(
    plot_id='empire-living-metal-budget',
    title="Living Metal",
    data_container_factory=lambda: LivingMetalBudgetDataContainer(),
    style=PlotStyle.budget,
)
ZRO_BUDGET = PlotSpecification(
    plot_id='empire-zro-budget',
    title="Zro",
    data_container_factory=lambda: ZroBudgetDataContainer(),
    style=PlotStyle.budget,
)
DARK_MATTER_BUDGET = PlotSpecification(
    plot_id='empire-dark-matter-budget',
    title="Dark Matter",
    data_container_factory=lambda: DarkMatterBudgetDataContainer(),
    style=PlotStyle.budget,
)
NANITES_BUDGET = PlotSpecification(
    plot_id='empire-nanites-budget',
    title="Nanites",
    data_container_factory=lambda: NanitesBudgetDataContainer(),
    style=PlotStyle.budget,
)
INFLUENCE_BUDGET = PlotSpecification(
    plot_id='empire-influence-budget',
    title="Influence",
    data_container_factory=lambda: InfluenceBudgetDataContainer(),
    style=PlotStyle.budget,
)
UNITY_BUDGET = PlotSpecification(
    plot_id='empire-unity-budget',
    title="Unity",
    data_container_factory=lambda: UnityBudgetDataContainer(),
    style=PlotStyle.stacked,
)
VICTORY_RANK_GRAPH = PlotSpecification(
    plot_id='victory-rank',
    title="Victory Rank (Lower is better!)",
    data_container_factory=lambda: VictoryRankDataContainer(),
    style=PlotStyle.line,
)
VICTORY_SCORE_GRAPH = PlotSpecification(
    plot_id='victory-score',
    title="Victory Score",
    data_container_factory=lambda: VictoryScoreDataContainer(),
    style=PlotStyle.line,
)
VICTORY_ECONOMY_SCORE_GRAPH = PlotSpecification(
    plot_id='victory-economy-score',
    title="Victory Economic Score",
    data_container_factory=lambda: EconomyScoreDataContainer(),
    style=PlotStyle.line,
)

ALL_PLOT_SPECIFICATIONS = [
    PLANET_COUNT_GRAPH,
    SYSTEM_COUNT_GRAPH,
    NET_MINERAL_INCOME_GRAPH,
    NET_ENERGY_INCOME_GRAPH,
    NET_ALLOYS_INCOME_GRAPH,
    NET_CONSUMER_GOODS_INCOME_GRAPH,
    NET_FOOD_INCOME_GRAPH,
    TECHNOLOGY_PROGRESS_GRAPH,
    RESEARCH_OUTPUT_BY_CATEGORY_GRAPH,
    RESEARCH_OUTPUT_GRAPH,
    SURVEY_PROGRESS_GRAPH,
    MILITARY_POWER_GRAPH,
    FLEET_SIZE_GRAPH,
    SPECIES_DISTRIBUTION_GRAPH,
    SPECIES_HAPPINESS_GRAPH,
    SPECIES_POWER_GRAPH,
    SPECIES_CRIME_GRAPH,
    FACTION_DISTRIBUTION_GRAPH,
    FACTION_SUPPORT_GRAPH,
    FACTION_APPROVAL_GRAPH,
    FACTION_CRIME_GRAPH,
    FACTION_POWER_GRAPH,
    FACTION_HAPPINESS_GRAPH,
    PLANET_POP_DISTRIBUTION_GRAPH,
    PLANET_MIGRATION_GRAPH,
    PLANET_AMENITIES_GRAPH,
    PLANET_STABILITY_GRAPH,
    PLANET_HOUSING_GRAPH,
    PLANET_CRIME_GRAPH,
    PLANET_POWER_GRAPH,
    PLANET_HAPPINESS_GRAPH,
    ETHOS_DISTRIBUTION_GRAPH,
    ETHOS_CRIME_GRAPH,
    ETHOS_POWER_GRAPH,
    ETHOS_HAPPINESS_GRAPH,
    STRATA_DISTRIBUTION_GRAPH,
    STRATA_CRIME_GRAPH,
    STRATA_POWER_GRAPH,
    STRATA_HAPPINESS_GRAPH,
    JOB_DISTRIBUTION_GRAPH,
    JOB_CRIME_GRAPH,
    JOB_POWER_GRAPH,
    JOB_HAPPINESS_GRAPH,
    ENERGY_BUDGET,
    MINERAL_BUDGET,
    CONSUMER_GOODS_BUDGET,
    ALLOYS_BUDGET,
    FOOD_BUDGET,
    VOLATILE_MOTES_BUDGET,
    EXOTIC_GASES_BUDGET,
    RARE_CRYSTALS_BUDGET,
    LIVING_METAL_BUDGET,
    ZRO_BUDGET,
    DARK_MATTER_BUDGET,
    NANITES_BUDGET,
    INFLUENCE_BUDGET,
    UNITY_BUDGET,
    VICTORY_RANK_GRAPH,
    VICTORY_SCORE_GRAPH,
    VICTORY_ECONOMY_SCORE_GRAPH,
]

# This dictionary specifies how the plots should be laid out in tabs by the plotly frontend
# and how they should be split to different image files by matplotlib
THEMATICALLY_GROUPED_PLOTS = {
    "Economy": [
        PLANET_COUNT_GRAPH,
        SYSTEM_COUNT_GRAPH,
        NET_ENERGY_INCOME_GRAPH,
        NET_MINERAL_INCOME_GRAPH,
        NET_ALLOYS_INCOME_GRAPH,
        NET_CONSUMER_GOODS_INCOME_GRAPH,
        NET_FOOD_INCOME_GRAPH,
    ],
    "Budget": [
        ENERGY_BUDGET,
        MINERAL_BUDGET,
        CONSUMER_GOODS_BUDGET,
        ALLOYS_BUDGET,
        FOOD_BUDGET,
        INFLUENCE_BUDGET,
        UNITY_BUDGET,
        # VOLATILE_MOTES_BUDGET,
        # EXOTIC_GASES_BUDGET,
        # RARE_CRYSTALS_BUDGET,
        # LIVING_METAL_BUDGET,
        # ZRO_BUDGET,
        # DARK_MATTER_BUDGET,
        # NANITES_BUDGET,
    ],
    "Pops": [
        SPECIES_DISTRIBUTION_GRAPH,
        SPECIES_HAPPINESS_GRAPH,
        # SPECIES_CRIME_GRAPH,
        # SPECIES_POWER_GRAPH,
        ETHOS_DISTRIBUTION_GRAPH,
        ETHOS_HAPPINESS_GRAPH,
        # ETHOS_CRIME_GRAPH,
        # ETHOS_POWER_GRAPH,
        STRATA_DISTRIBUTION_GRAPH,
        STRATA_HAPPINESS_GRAPH,
        # STRATA_CRIME_GRAPH,
        # STRATA_POWER_GRAPH,
    ],
    "Jobs": [
        JOB_DISTRIBUTION_GRAPH,
        JOB_HAPPINESS_GRAPH,
        JOB_CRIME_GRAPH,
        JOB_POWER_GRAPH,
    ],
    "Factions": [
        FACTION_DISTRIBUTION_GRAPH,
        FACTION_APPROVAL_GRAPH,
        FACTION_HAPPINESS_GRAPH,
        FACTION_SUPPORT_GRAPH,
        FACTION_CRIME_GRAPH,
        FACTION_POWER_GRAPH,
    ],
    "Planets": [
        PLANET_POP_DISTRIBUTION_GRAPH,
        PLANET_MIGRATION_GRAPH,
        PLANET_STABILITY_GRAPH,
        PLANET_HAPPINESS_GRAPH,
        PLANET_AMENITIES_GRAPH,
        PLANET_HOUSING_GRAPH,
        PLANET_CRIME_GRAPH,
        PLANET_POWER_GRAPH,
    ],
    "Science": [
        TECHNOLOGY_PROGRESS_GRAPH,
        SURVEY_PROGRESS_GRAPH,
        RESEARCH_OUTPUT_GRAPH,
        RESEARCH_OUTPUT_BY_CATEGORY_GRAPH,
    ],
    "Military": [
        FLEET_SIZE_GRAPH,
        MILITARY_POWER_GRAPH,
    ],
    "Victory": [
        VICTORY_RANK_GRAPH,
        VICTORY_SCORE_GRAPH,
        # VICTORY_ECONOMY_SCORE_GRAPH,
    ],
}

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
        logger.info(f"Updated networkx graph in {time.clock() - start_time:5.3f} seconds.")
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
