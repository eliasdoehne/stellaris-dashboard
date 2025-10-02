import abc
import colorsys
import dataclasses
import enum
import functools
import itertools
import logging
import pathlib
import random
import re
import time
from collections import defaultdict
from typing import List, Dict, Callable, Tuple, Iterable, Union, Set, Optional, Any

import networkx as nx
import numpy as np
from scipy.spatial import Voronoi

from stellarisdashboard import datamodel, config, game_info
from stellarisdashboard.parsing.save_parser import rust_parser

logger = logging.getLogger(__name__)

COLOR_PHYSICS = (0.12, 0.4, 0.66)
COLOR_SOCIETY = (0.23, 0.59, 0.35)
COLOR_ENGINEERING = (0.75, 0.59, 0.12)


@enum.unique
class PlotStyle(enum.Enum):
    """Defines the kind of visualization associated with a given PlotSpecification (defined below)"""

    line = 0
    stacked = 1
    budget = 2


@dataclasses.dataclass
class PlotSpecification:
    """This class is used to define all available visualizations."""

    plot_id: str
    title: str

    # This function specifies which data container class should be used for the plot.
    # The int argument is the country ID for which budgets and pop stats are shown.
    data_container_factory: Callable[[Optional[int], Any], "AbstractPlotDataContainer"]

    style: PlotStyle
    yrange: Tuple[float, float] = None

    x_axis_label: str = "Time (years after 2200.01.01)"
    y_axis_label: str = ""

    data_container_factory_kwargs: Dict = dataclasses.field(default_factory=dict)


def get_plot_specifications_for_tab_layout():
    return {
        tab: [PLOT_SPECIFICATIONS[plot] for plot in plots]
        for tab, plots in config.CONFIG.tab_layout.items()
    }


# The PlotDataManager is cached in memory for each "active" game
# (one that was requested or had a save file parsed in the current execution).
_CURRENT_EXECUTION_PLOT_DATA: Dict[str, "PlotDataManager"] = {}


def get_current_execution_plot_data(
    game_name: str, country_perspective: Optional[int] = None
) -> "PlotDataManager":
    """Update and retrieve the PlotDataManager object stored for the requested game.

    :param game_name: The exact name of a game for which a database is available
    :return:
    """
    global _CURRENT_EXECUTION_PLOT_DATA
    if game_name not in _CURRENT_EXECUTION_PLOT_DATA:
        with datamodel.get_db_session(game_name) as session:
            game = session.query(datamodel.Game).filter_by(game_name=game_name).first()
        if not game:
            logger.warning(f"Warning: Game {game_name} could not be found in database!")
        plot_specifications = [
            ps
            for pslist in get_plot_specifications_for_tab_layout().values()
            for ps in pslist
        ]
        plot_specifications += get_market_graphs(config.CONFIG.market_resources)
        _CURRENT_EXECUTION_PLOT_DATA[game_name] = PlotDataManager(
            game_name, plot_specifications
        )
        _CURRENT_EXECUTION_PLOT_DATA[game_name].initialize()
    _CURRENT_EXECUTION_PLOT_DATA[game_name].country_perspective = country_perspective
    _CURRENT_EXECUTION_PLOT_DATA[game_name].update_with_new_gamestate()
    return _CURRENT_EXECUTION_PLOT_DATA[game_name]


_GAME_COUNTRY_COLORS = {}


def clear_cached_country_colors():
    _GAME_COUNTRY_COLORS.clear()


def get_color_vals(
    game_id: str, key_str: str, range_min: float = 0.1, range_max: float = 1.0
) -> Tuple[float, float, float]:
    """Generate RGB values for the given identifier. Some special values (tech categories)
    have hardcoded colors to roughly match the game's look and feel.

    For unknown identifiers, a random color is generated, with the key_str being applied as a seed to
    the random number generator. This makes colors consistent across figures and executions.
    """
    if game_id not in _GAME_COUNTRY_COLORS:
        country_colors = CountryColors()
        country_colors.load(game_id)
        _GAME_COUNTRY_COLORS[game_id] = country_colors
    else:
        country_colors = _GAME_COUNTRY_COLORS[game_id]

    if key_str.lower() == "physics":
        r, g, b = COLOR_PHYSICS
    elif key_str.lower() == "society":
        r, g, b = COLOR_SOCIETY
    elif key_str.lower() == "engineering":
        r, g, b = COLOR_ENGINEERING
    elif key_str == GalaxyMapData.UNCLAIMED:  # for unclaimed system in the galaxy map
        r, g, b = 255, 255, 255
    elif key_str.endswith("galactic_market"):
        r, g, b = 255, 0, 0
    elif key_str.endswith("internal_market"):
        r, g, b = 0, 0, 255
    elif country_colors.has_color_for_name(key_str):
        r, g, b = country_colors.get_color_by_name(key_str)
    else:
        random.seed(key_str)
        h = random.uniform(0, 1)
        l = random.uniform(0.4, 0.6)
        s = random.uniform(0.5, 1)
        r, g, b = map(
            lambda x: 255 * (x if x > 0.01 else 0), colorsys.hls_to_rgb(h, l, s)
        )
    return r, g, b


class PlotDataManager:
    """Responsible for maintaining a single game's data for every available PlotSpecification.

    The data is organized as a dictionary mapping the plot_id of the PlotSpecification class
    to a DataContainer instance (defined below).
    """

    def __init__(
        self,
        game_name: str,
        plot_specifications: List[PlotSpecification],
        country_perspective: Optional[int] = None,
    ):
        self.game_name: str = game_name
        self.plot_specifications = plot_specifications

        self.last_date = None
        self._loaded_gamestates = None
        self.show_everything = None
        self.show_all_country_types = None
        self.plot_time_resolution = None

        self._country_perspective: int = country_perspective

        self.data_containers_by_plot_id: Dict[str, AbstractPlotDataContainer] = None

    def initialize(self):
        self.last_date = -float("inf")
        self._loaded_gamestates = 0
        self.show_everything = config.CONFIG.show_everything
        self.show_all_country_types = config.CONFIG.show_all_country_types
        self.plot_time_resolution = config.CONFIG.plot_time_resolution

        self.data_containers_by_plot_id = {}
        for plot_spec in self.plot_specifications:
            self.data_containers_by_plot_id[
                plot_spec.plot_id
            ] = plot_spec.data_container_factory(
                self.country_perspective, **plot_spec.data_container_factory_kwargs
            )

    @property
    def country_perspective(self) -> Optional[int]:
        return self._country_perspective

    @country_perspective.setter
    def country_perspective(self, value: Optional[int]):
        if value != self._country_perspective:
            logger.info(
                f"Switching perspective to Country {value if value is not None else 'Observer'}"
            )
            self._country_perspective = value
            self.initialize()

    def update_with_new_gamestate(self):
        if (
            self.show_everything != config.CONFIG.show_everything
            or self.show_all_country_types != config.CONFIG.show_all_country_types
            or self.plot_time_resolution != config.CONFIG.plot_time_resolution
        ):
            # reset everything due to changed setting: This forces the program to redraw all plots with the appropriate data:
            logger.info("Detected changed visibility settings: Reassembling plot data")
            self.initialize()
            self.show_everything = config.CONFIG.show_everything
            self.show_all_country_types = config.CONFIG.show_all_country_types

        num_new_gs = datamodel.count_gamestates_since(self.game_name, self.last_date)
        if self.plot_time_resolution == 0 or num_new_gs < self.plot_time_resolution:
            use_every_nth_gamestate = 1
        else:
            use_every_nth_gamestate = (num_new_gs // self.plot_time_resolution) + 1
        t_start = time.time()
        num_loaded_gs = 0
        for i, gs in enumerate(
            datamodel.get_gamestates_since(self.game_name, self.last_date)
        ):
            if gs.date <= self.last_date:
                logger.warning(
                    f"Received gamestate with date {datamodel.days_to_date(gs.date)}, last known date is {datamodel.days_to_date(self.last_date)}"
                )
                continue
            if (
                self.plot_time_resolution == 0
                or num_new_gs < self.plot_time_resolution
                or i % use_every_nth_gamestate == 0
                or (num_new_gs - i + self._loaded_gamestates)
                <= self.plot_time_resolution
            ):
                num_loaded_gs += 1
                self._loaded_gamestates += 1
                for data_container in self.data_containers_by_plot_id.values():
                    data_container.extract_data_from_gamestate(gs)
            self.last_date = gs.date
        logger.info(
            f"Loaded {num_loaded_gs} new gamestates from the database in {time.time() - t_start:5.3f} seconds. ({self._loaded_gamestates} gamestates in total)"
        )

    def get_data_for_plot(
        self, ps: PlotSpecification
    ) -> Iterable[Tuple[str, List[int], List[float]]]:
        """
        Used to access the raw data for the provided plot specification. Individual traces to be plotted are
        yielded one-by-one as tuples in the form (legend key_object, x values, y values).

        :param ps:
        :return:
        """
        container = self.data_containers_by_plot_id.get(ps.plot_id)
        if container is None:
            logger.info(f"No data available for plot {ps.title} ({ps.plot_id}).")
            return
        yield from container.iterate_traces()


class AbstractPlotDataContainer(abc.ABC):
    DEFAULT_VAL = float("nan")

    def __init__(self, country_perspective: Optional[int], **kwargs):
        self.dates: List[float] = []
        self.data_dict: Dict[str, List[float]] = {}
        self._country_perspective = country_perspective

    def iterate_traces(self) -> Iterable[Tuple[str, List[int], List[float]]]:
        for key, values in self.data_dict.items():
            yield key, self.dates, values

    def _add_new_value_to_data_dict(self, key, new_val, default_val=DEFAULT_VAL):
        if key not in self.data_dict:
            if new_val == default_val:
                return
            self.data_dict[key] = [default_val for _ in range(len(self.dates) - 1)]
        if len(self.data_dict[key]) >= len(self.dates):
            logger.info(
                f"{self.__class__.__qualname__} Ignoring duplicate value for {key}."
            )
            return
        self.data_dict[key].append(new_val)

    def _pad_data_dict(self, default_val=DEFAULT_VAL):
        # Pad every dict with the default value if no real value was added, to keep them consistent with the dates list
        for key in self.data_dict:
            while len(self.data_dict[key]) < len(self.dates):
                self.data_dict[key].append(default_val)

    @abc.abstractmethod
    def extract_data_from_gamestate(self, gs: datamodel.GameState):
        pass


class AbstractPerCountryDataContainer(AbstractPlotDataContainer, abc.ABC):
    def extract_data_from_gamestate(self, gs: datamodel.GameState):
        added_new_val = False
        self.dates.append(gs.date / 360.0)
        for cd in gs.country_data:
            country_name = cd.country.rendered_name
            try:
                if (
                    not config.CONFIG.show_all_country_types
                    and cd.country.country_type != "default"
                ):
                    continue
                new_val = self._get_value_from_countrydata(cd)
                if new_val is not None:
                    added_new_val = True
                    self._add_new_value_to_data_dict(
                        country_name, new_val, default_val=self.DEFAULT_VAL
                    )
            except Exception as e:
                logger.exception(country_name)
        if not added_new_val:
            self.dates.pop()  # if nothing was added, we don't need to remember the date.
        self._pad_data_dict(default_val=self.DEFAULT_VAL)

    @abc.abstractmethod
    def _get_value_from_countrydata(
        self, cd: datamodel.CountryData
    ) -> Union[None, float]:
        pass


def _override_visibility(cd: datamodel.CountryData):
    return not cd.country.is_hidden_country() and config.CONFIG.show_everything


class PlanetCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_geography_info():
            return cd.owned_planets


class SystemCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_geography_info():
            return cd.controlled_systems


class TotalEnergyIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_energy


class TotalMineralsIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_minerals


class TotalAlloysIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_alloys


class TotalConsumerGoodsIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_consumer_goods


class TotalTradeIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_trade


class TotalFoodIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_food


class TotalBiomassIncomeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.net_biomass


class EmpireSizeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_economic_info():
            return cd.empire_size


class TechCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_tech_info():
            return cd.tech_count


class ExploredSystemsCountDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_tech_info():
            return cd.exploration_progress


class TotalScienceOutputDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_tech_info():
            return (
                cd.net_physics_research
                + cd.net_society_research
                + cd.net_engineering_research
            )


class FleetSizeDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_military_info():
            return cd.fleet_size


class MilitaryPowerDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_military_info():
            return cd.military_power


class VictoryScoreDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_geography_info():
            return cd.victory_score


class EconomyScoreDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_geography_info():
            return cd.economy_power


class VictoryRankDataContainer(AbstractPerCountryDataContainer):
    def _get_value_from_countrydata(self, cd: datamodel.CountryData):
        if _override_visibility(cd) or cd.show_geography_info():
            return cd.victory_rank


class AbstractPlayerInfoDataContainer(AbstractPlotDataContainer, abc.ABC):
    def extract_data_from_gamestate(self, gs: datamodel.GameState):
        player_cd = self._get_player_countrydata(gs)

        if player_cd is None or not self._include(player_cd):
            return

        self.dates.append(gs.date / 360.0)
        try:
            for key, new_val in self._iterate_budgetitems(player_cd):
                if new_val is not None:
                    self._add_new_value_to_data_dict(
                        key, new_val, default_val=self.DEFAULT_VAL
                    )
        except Exception as e:
            logger.exception(player_cd.country.rendered_country_name)
        self._pad_data_dict(self.DEFAULT_VAL)

    def _get_player_countrydata(self, gs: datamodel.GameState) -> datamodel.CountryData:
        player_cd = None
        for cd in gs.country_data:
            if cd.country.is_hidden_country():
                continue
            if (
                self._country_perspective is None and cd.country.is_player
            ) or cd.country.country_id_in_game == self._country_perspective:
                player_cd = cd
                break
        return player_cd

    @abc.abstractmethod
    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        pass

    def _include(self, player_cd: datamodel.CountryData) -> bool:
        return True


class ScienceOutputByFieldDataContainer(AbstractPlayerInfoDataContainer):
    DEFAULT_VAL = 0.0

    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        yield "Physics", cd.net_physics_research
        yield "Society", cd.net_society_research
        yield "Engineering", cd.net_engineering_research


class FleetCompositionDataContainer(AbstractPlayerInfoDataContainer):
    DEFAULT_VAL = 0.0

    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        yield "corvettes", cd.ship_count_corvette
        yield "destroyers", cd.ship_count_destroyer * 2
        yield "cruisers", cd.ship_count_cruiser * 4
        yield "battleships", cd.ship_count_battleship * 8
        yield "titans", cd.ship_count_titan * 16
        yield "colossi", cd.ship_count_colossus * 32


class MarketPriceDataContainer(AbstractPlayerInfoDataContainer):
    DEFAULT_VAL = float("nan")
    galactic_market_indicator_key = "traded_on_galactic_market"
    internal_market_indicator_key = "traded_on_internal_market"

    def __init__(self, country_perspective, resource_name, base_price, resource_index):
        super().__init__(country_perspective=country_perspective)
        self.resource_name = resource_name
        self.base_price = base_price
        self.resource_index = resource_index

    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        gs = cd.game_state
        if cd.has_galactic_market_access:
            yield from self._iter_galactic_market_price(gs, cd)
        else:
            yield from self._iter_internal_market_price(gs, cd)

    def _iter_galactic_market_price(
        self, gs: datamodel.GameState, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        market_fee = self.get_market_fee(gs)
        market_resources: List[datamodel.GalacticMarketResource] = sorted(
            gs.galactic_market_resources, key=lambda r: r.resource_index
        )
        for res, res_data in zip(market_resources, config.CONFIG.market_resources):
            if res_data["name"] == self.resource_name and res.availability != 0:
                yield from self._get_resource_prices(
                    market_fee, res_data["base_price"], res.fluctuation
                )
                yield self.galactic_market_indicator_key, -0.001
                yield self.internal_market_indicator_key, self.DEFAULT_VAL
                break

    def _iter_internal_market_price(
        self, gs: datamodel.GameState, cd: datamodel.CountryData
    ):
        market_fee = self.get_market_fee(gs)
        res_data = None
        for r in config.CONFIG.market_resources:
            if r["name"] == self.resource_name:
                res_data = r
                break
        if res_data is None:
            logger.info(f"Could not find configuration for resource {self.resource_name}")
            return
        if res_data["base_price"] is None:
            return

        always_tradeable = ["energy", "minerals", "food", "consumer_goods", "alloys"]
        fluctuation = 0.0 if self.resource_name in always_tradeable else None
        for resource in cd.internal_market_resources:
            if resource.resource_name.text == self.resource_name:
                fluctuation = resource.fluctuation
                break
        if fluctuation is None:
            return

        yield from self._get_resource_prices(
            market_fee, res_data["base_price"], fluctuation
        )
        yield self.galactic_market_indicator_key, self.DEFAULT_VAL
        yield self.internal_market_indicator_key, -0.001

    def _get_resource_prices(
        self, market_fee: float, base_price: float, fluctuation: float
    ) -> Tuple[float, float, float]:
        no_fee_price = base_price * (1 + fluctuation / 100)
        buy_price = base_price * (1 + fluctuation / 100) * (1 + market_fee)
        sell_price = base_price * (1 + fluctuation / 100) * (1 - market_fee)

        yield f"{self.resource_name}_base_price", no_fee_price
        if buy_price != sell_price:
            yield f"{self.resource_name}_buy_price", buy_price
            yield f"{self.resource_name}_sell_price", sell_price

    def get_market_fee(self, gs):
        market_fees = config.CONFIG.market_fee
        current_fee = {"date": 0, "fee": 0.3}  # default
        for fee in sorted(market_fees, key=lambda f: f["date"]):
            if datamodel.date_to_days(fee["date"]) > gs.date:
                break
            current_fee = fee
        market_fee = current_fee["fee"]
        return market_fee


class AbstractPlayerBudgetDataContainer(AbstractPlayerInfoDataContainer, abc.ABC):
    DEFAULT_VAL = 0.0

    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        for budget_item in cd.budget:
            val = self._get_value_from_budgetitem(budget_item)
            if val == 0.0:
                val = None
            yield budget_item.name, val

    @abc.abstractmethod
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem) -> float:
        pass

    def _include(self, player_cd):
        return len(player_cd.budget) != 0


class EnergyBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_energy


class MineralsBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_minerals


class AlloysBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_alloys


class ConsumerGoodsBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_consumer_goods


class TradeBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_trade


class FoodBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_food


class VolatileMotesBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_volatile_motes


class ExoticGasesBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_exotic_gases


class RareCrystalsBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_rare_crystals


class LivingMetalBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_living_metal


class ZroBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_zro


class DarkMatterBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_dark_matter


class NanitesBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_nanites


class MinorArtifactsBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_minor_artifacts


class AstralThreadsBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_astral_threads


class BiomassBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_biomass


class UnityBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_unity


class InfluenceBudgetDataContainer(AbstractPlayerBudgetDataContainer):
    def _get_value_from_budgetitem(self, bi: datamodel.BudgetItem):
        return bi.net_influence


class BudgetSumDataContainer(AbstractPerCountryDataContainer, abc.ABC):
    DEFAULT_VAL = float("nan")

    def __init__(
        self,
        function_from_budgetitem: Callable[[datamodel.BudgetItem], float],
        country_perspective: Optional[int],
        **kwargs,
    ):
        super().__init__(country_perspective, **kwargs)
        self.function_from_budgetitem = function_from_budgetitem

    def _get_value_from_countrydata(
        self, cd: datamodel.CountryData
    ) -> Union[None, float]:
        if cd.show_economic_info():
            return sum(self.function_from_budgetitem(bi) for bi in cd.budget)


PopStatsType = Union[
    datamodel.PopStatsByFaction,
    datamodel.PopStatsByEthos,
    datamodel.PopStatsByStratum,
    datamodel.PopStatsBySpecies,
    datamodel.PlanetStats,
]


class AbstractPopStatsDataContainer(AbstractPlayerInfoDataContainer, abc.ABC):
    def _iterate_budgetitems(
        self, cd: datamodel.CountryData
    ) -> Iterable[Tuple[str, float]]:
        for pop_stats in self._iterate_popstats(cd):
            key = self._get_key_from_popstats(pop_stats)
            val = self._get_value_from_popstats(pop_stats)
            yield (key, val)

    @abc.abstractmethod
    def _iterate_popstats(self, cd: datamodel.CountryData) -> Iterable[PopStatsType]:
        pass

    @abc.abstractmethod
    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        pass

    @abc.abstractmethod
    def _get_value_from_popstats(self, ps: PopStatsType) -> float:
        pass

    def _include(self, player_cd):
        try:
            next(self._iterate_popstats(player_cd))
            return True
        except StopIteration:
            return False


class AbstractPopStatsBySpeciesDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PopStatsBySpecies]:
        return iter(cd.pop_stats_species)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PopStatsBySpecies)
        return f"{ps.species.rendered_name} ({ps.species.species_id_in_game})"


class SpeciesDistributionDataContainer(AbstractPopStatsBySpeciesDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsBySpecies):
        return ps.pop_count


class SpeciesHappinessDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsBySpecies):
        return ps.happiness


class SpeciesPowerDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsBySpecies):
        return ps.power


class SpeciesCrimeDataContainer(AbstractPopStatsBySpeciesDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsBySpecies):
        return ps.crime


class AbstractPopStatsByFactionDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PopStatsByFaction]:
        return iter(cd.pop_stats_faction)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PopStatsByFaction)
        return ps.faction.rendered_name


class FactionDistributionDataContainer(AbstractPopStatsByFactionDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.pop_count


class FactionSupportDataContainer(AbstractPopStatsByFactionDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.support


class FactionApprovalDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.faction_approval


class FactionHappinessDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.happiness


class FactionPowerDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.power


class FactionCrimeDataContainer(AbstractPopStatsByFactionDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByFaction):
        return ps.crime


class AbstractPopStatsByJobDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PopStatsByJob]:
        return iter(cd.pop_stats_job)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PopStatsByJob)
        return ps.job_description


class JobDistributionDataContainer(AbstractPopStatsByJobDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsByJob):
        return ps.pop_count


class JobHappinessDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByJob):
        return ps.happiness


class JobPowerDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByJob):
        return ps.power


class JobCrimeDataContainer(AbstractPopStatsByJobDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByJob):
        return ps.crime


class AbstractPopStatsByPlanetDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PlanetStats]:
        return iter(cd.pop_stats_planets)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PlanetStats)
        return f"{ps.planet.rendered_name} ({ps.planet.planet_id_in_game})"


class PlanetDistributionDataContainer(AbstractPopStatsByPlanetDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.pop_count


class PlanetHappinessDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.happiness


class PlanetPowerDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.power


class PlanetCrimeDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.crime


class PlanetMigrationDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.migration


class PlanetAmenitiesDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.free_amenities


class PlanetHousingDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.free_housing


class PlanetStabilityDataContainer(AbstractPopStatsByPlanetDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PlanetStats):
        return ps.stability


class AbstractPopStatsByEthosDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PopStatsByEthos]:
        return iter(cd.pop_stats_ethos)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PopStatsByEthos)
        return ps.ethos


class EthosDistributionDataContainer(AbstractPopStatsByEthosDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsByEthos):
        return ps.pop_count


class EthosHappinessDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByEthos):
        return ps.happiness


class EthosPowerDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByEthos):
        return ps.power


class EthosCrimeDataContainer(AbstractPopStatsByEthosDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByEthos):
        return ps.crime


class AbstractPopStatsByStratumDataContainer(AbstractPopStatsDataContainer, abc.ABC):
    def _iterate_popstats(
        self, cd: datamodel.CountryData
    ) -> Iterable[datamodel.PopStatsByStratum]:
        return iter(cd.pop_stats_stratum)

    def _get_key_from_popstats(self, ps: PopStatsType) -> str:
        assert isinstance(ps, datamodel.PopStatsByStratum)
        return ps.stratum


class StratumDistributionDataContainer(AbstractPopStatsByStratumDataContainer):
    DEFAULT_VAL = 0.0

    def _get_value_from_popstats(self, ps: datamodel.PopStatsByStratum):
        return ps.pop_count


class StratumHappinessDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByStratum):
        return ps.happiness


class StratumPowerDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByStratum):
        return ps.power


class StratumCrimeDataContainer(AbstractPopStatsByStratumDataContainer):
    def _get_value_from_popstats(self, ps: datamodel.PopStatsByStratum):
        return ps.crime


""" Define PlotSpecifications for all currently supported plots: """

PLANET_COUNT_GRAPH = PlotSpecification(
    plot_id="planet-count",
    title="Owned Planets",
    data_container_factory=PlanetCountDataContainer,
    style=PlotStyle.line,
)
SYSTEM_COUNT_GRAPH = PlotSpecification(
    plot_id="system-count",
    title="Controlled Systems",
    data_container_factory=SystemCountDataContainer,
    style=PlotStyle.line,
)
NET_MINERAL_INCOME_GRAPH = PlotSpecification(
    plot_id="net-mineral-income",
    title="Net Mineral Income",
    data_container_factory=TotalMineralsIncomeDataContainer,
    style=PlotStyle.line,
)
NET_ENERGY_INCOME_GRAPH = PlotSpecification(
    plot_id="net-energy-income",
    title="Net Energy Income",
    data_container_factory=TotalEnergyIncomeDataContainer,
    style=PlotStyle.line,
)
NET_ALLOYS_INCOME_GRAPH = PlotSpecification(
    plot_id="net-alloys-income",
    title="Net Alloys Income",
    data_container_factory=TotalAlloysIncomeDataContainer,
    style=PlotStyle.line,
)
NET_TRADE_INCOME_GRAPH = PlotSpecification(
    plot_id="net-trade-income",
    title="Net Trade Income",
    data_container_factory=TotalTradeIncomeDataContainer,
    style=PlotStyle.line,
)
NET_CONSUMER_GOODS_INCOME_GRAPH = PlotSpecification(
    plot_id="net-consumer-goods-income",
    title="Net Consumer Goods Income",
    data_container_factory=TotalConsumerGoodsIncomeDataContainer,
    style=PlotStyle.line,
)
NET_FOOD_INCOME_GRAPH = PlotSpecification(
    plot_id="net-food-income",
    title="Net Food Income",
    data_container_factory=TotalFoodIncomeDataContainer,
    style=PlotStyle.line,
)
NET_BIOMASS_INCOME_GRAPH = PlotSpecification(
    plot_id="net-biomass-income",
    title="Net Biomass Income",
    data_container_factory=TotalBiomassIncomeDataContainer,
    style=PlotStyle.line,
)
EMPIRE_SIZE_GRAPH = PlotSpecification(
    plot_id="empire-size",
    title="Empire Size",
    data_container_factory=EmpireSizeDataContainer,
    style=PlotStyle.line,
)
TECHNOLOGY_PROGRESS_GRAPH = PlotSpecification(
    plot_id="tech-count",
    title="Researched Technologies",
    data_container_factory=TechCountDataContainer,
    style=PlotStyle.line,
)
RESEARCH_OUTPUT_BY_CATEGORY_GRAPH = PlotSpecification(
    plot_id="empire-research-output",
    title="Research Output",
    data_container_factory=ScienceOutputByFieldDataContainer,
    style=PlotStyle.stacked,
)
RESEARCH_OUTPUT_GRAPH = PlotSpecification(
    plot_id="empire-research-output-comparison",
    title="Total Research Output",
    data_container_factory=TotalScienceOutputDataContainer,
    style=PlotStyle.line,
)
SURVEY_PROGRESS_GRAPH = PlotSpecification(
    plot_id="survey-count",
    title="Surveyed Planets",
    data_container_factory=ExploredSystemsCountDataContainer,
    style=PlotStyle.line,
)
MILITARY_POWER_GRAPH = PlotSpecification(
    plot_id="military-power",
    title="Military Strength",
    data_container_factory=MilitaryPowerDataContainer,
    style=PlotStyle.line,
)
FLEET_SIZE_GRAPH = PlotSpecification(
    plot_id="fleet-size",
    title="Fleet Size",
    data_container_factory=FleetSizeDataContainer,
    style=PlotStyle.line,
)
FLEET_COMPOSITION_GRAPH = PlotSpecification(
    plot_id="empire-fleet-composition",
    title="Fleet Composition",
    data_container_factory=FleetCompositionDataContainer,
    style=PlotStyle.stacked,
)
SPECIES_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-species-distribution",
    title="Species Demographics",
    data_container_factory=SpeciesDistributionDataContainer,
    style=PlotStyle.stacked,
)
SPECIES_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-species-happiness",
    title="Happiness by Species",
    data_container_factory=SpeciesHappinessDataContainer,
    style=PlotStyle.line,
)
SPECIES_POWER_GRAPH = PlotSpecification(
    plot_id="empire-species-power",
    title="Power by Species",
    data_container_factory=SpeciesPowerDataContainer,
    style=PlotStyle.line,
)
SPECIES_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-species-crime",
    title="Crime by Species",
    data_container_factory=SpeciesCrimeDataContainer,
    style=PlotStyle.line,
)
FACTION_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-faction-distribution",
    title="Faction Demographics",
    data_container_factory=FactionDistributionDataContainer,
    style=PlotStyle.stacked,
)
FACTION_SUPPORT_GRAPH = PlotSpecification(
    plot_id="empire-faction-support",
    title="Faction Support",
    data_container_factory=FactionSupportDataContainer,
    style=PlotStyle.stacked,
)
FACTION_APPROVAL_GRAPH = PlotSpecification(
    plot_id="empire-faction-approval",
    title="Faction Approval",
    data_container_factory=FactionApprovalDataContainer,
    style=PlotStyle.line,
)
FACTION_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-faction-crime",
    title="Crime by Faction",
    data_container_factory=FactionCrimeDataContainer,
    style=PlotStyle.line,
)
FACTION_POWER_GRAPH = PlotSpecification(
    plot_id="empire-faction-power",
    title="Power by Faction",
    data_container_factory=FactionPowerDataContainer,
    style=PlotStyle.line,
)
FACTION_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-faction-happiness",
    title="Happiness by Faction",
    data_container_factory=FactionHappinessDataContainer,
    style=PlotStyle.line,
)
PLANET_POP_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-planet-pop-distribution",
    title="Population by Planet",
    data_container_factory=PlanetDistributionDataContainer,
    style=PlotStyle.stacked,
)
PLANET_MIGRATION_GRAPH = PlotSpecification(
    plot_id="empire-planet-migration",
    title="Migration by Planet",
    data_container_factory=PlanetMigrationDataContainer,
    style=PlotStyle.line,
)
PLANET_AMENITIES_GRAPH = PlotSpecification(
    plot_id="empire-planet-amenities",
    title="Free Amenities by Planet",
    data_container_factory=PlanetAmenitiesDataContainer,
    style=PlotStyle.line,
)
PLANET_STABILITY_GRAPH = PlotSpecification(
    plot_id="empire-planet-stability",
    title="Stability by Planet",
    data_container_factory=PlanetStabilityDataContainer,
    style=PlotStyle.line,
)
PLANET_HOUSING_GRAPH = PlotSpecification(
    plot_id="empire-planet-housing",
    title="Free Housing by Planet",
    data_container_factory=PlanetHousingDataContainer,
    style=PlotStyle.line,
)
PLANET_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-planet-crime",
    title="Crime by Planet",
    data_container_factory=PlanetCrimeDataContainer,
    style=PlotStyle.line,
)
PLANET_POWER_GRAPH = PlotSpecification(
    plot_id="empire-planet-power",
    title="Power by Planet",
    data_container_factory=PlanetPowerDataContainer,
    style=PlotStyle.line,
)
PLANET_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-planet-happiness",
    title="Happiness by Planet",
    data_container_factory=PlanetHappinessDataContainer,
    style=PlotStyle.line,
)
ETHOS_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-ethos-distribution",
    title="Ethos Demographics",
    data_container_factory=EthosDistributionDataContainer,
    style=PlotStyle.stacked,
)

ETHOS_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-ethos-crime",
    title="Crime by Ethos",
    data_container_factory=EthosCrimeDataContainer,
    style=PlotStyle.line,
)

ETHOS_POWER_GRAPH = PlotSpecification(
    plot_id="empire-ethos-power",
    title="Power by Ethos",
    data_container_factory=EthosPowerDataContainer,
    style=PlotStyle.line,
)
ETHOS_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-ethos-happiness",
    title="Happiness by Ethos",
    data_container_factory=EthosHappinessDataContainer,
    style=PlotStyle.line,
)
STRATA_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-strata-distribution",
    title="Stratum Demographics",
    data_container_factory=StratumDistributionDataContainer,
    style=PlotStyle.stacked,
)
STRATA_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-strata-crime",
    title="Crime by Stratum",
    data_container_factory=StratumCrimeDataContainer,
    style=PlotStyle.line,
)
STRATA_POWER_GRAPH = PlotSpecification(
    plot_id="empire-strata-power",
    title="Power by Stratum",
    data_container_factory=StratumPowerDataContainer,
    style=PlotStyle.line,
)
STRATA_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-strata-happiness",
    title="Happiness by Stratum",
    data_container_factory=StratumHappinessDataContainer,
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
JOB_DISTRIBUTION_GRAPH = PlotSpecification(
    plot_id="empire-job-distribution",
    title="Job Demographics",
    data_container_factory=JobDistributionDataContainer,
    style=PlotStyle.stacked,
)
JOB_CRIME_GRAPH = PlotSpecification(
    plot_id="empire-job-crime",
    title="Crime by Job",
    data_container_factory=JobCrimeDataContainer,
    style=PlotStyle.line,
)
JOB_POWER_GRAPH = PlotSpecification(
    plot_id="empire-job-power",
    title="Power by Job",
    data_container_factory=JobPowerDataContainer,
    style=PlotStyle.line,
)
JOB_HAPPINESS_GRAPH = PlotSpecification(
    plot_id="empire-job-happiness",
    title="Happiness by Job",
    data_container_factory=JobHappinessDataContainer,
    style=PlotStyle.line,
    yrange=(0, 1.0),
)
ENERGY_BUDGET = PlotSpecification(
    plot_id="empire-energy-budget",
    title="Energy Budget",
    data_container_factory=EnergyBudgetDataContainer,
    style=PlotStyle.budget,
)
MINERAL_BUDGET = PlotSpecification(
    plot_id="empire-mineral-budget",
    title="Mineral Budget",
    data_container_factory=MineralsBudgetDataContainer,
    style=PlotStyle.budget,
)
CONSUMER_GOODS_BUDGET = PlotSpecification(
    plot_id="empire-consumer-goods-budget",
    title="Consumer Goods Budget",
    data_container_factory=ConsumerGoodsBudgetDataContainer,
    style=PlotStyle.budget,
)
TRADE_BUDGET = PlotSpecification(
    plot_id="empire-trade-budget",
    title="Trade Budget",
    data_container_factory=TradeBudgetDataContainer,
    style=PlotStyle.budget,
)
ALLOYS_BUDGET = PlotSpecification(
    plot_id="empire-alloys-budget",
    title="Alloys Budget",
    data_container_factory=AlloysBudgetDataContainer,
    style=PlotStyle.budget,
)
FOOD_BUDGET = PlotSpecification(
    plot_id="empire-food-budget",
    title="Food",
    data_container_factory=FoodBudgetDataContainer,
    style=PlotStyle.budget,
)
VOLATILE_MOTES_BUDGET = PlotSpecification(
    plot_id="empire-volatile-motes-budget",
    title="Volatile Motes",
    data_container_factory=VolatileMotesBudgetDataContainer,
    style=PlotStyle.budget,
)
EXOTIC_GASES_BUDGET = PlotSpecification(
    plot_id="empire-exotic-gas-budget",
    title="Exotic Gases",
    data_container_factory=ExoticGasesBudgetDataContainer,
    style=PlotStyle.budget,
)
RARE_CRYSTALS_BUDGET = PlotSpecification(
    plot_id="empire-rare-crystals-budget",
    title="Rare Crystals",
    data_container_factory=RareCrystalsBudgetDataContainer,
    style=PlotStyle.budget,
)
LIVING_METAL_BUDGET = PlotSpecification(
    plot_id="empire-living-metal-budget",
    title="Living Metal",
    data_container_factory=LivingMetalBudgetDataContainer,
    style=PlotStyle.budget,
)
ZRO_BUDGET = PlotSpecification(
    plot_id="empire-zro-budget",
    title="Zro",
    data_container_factory=ZroBudgetDataContainer,
    style=PlotStyle.budget,
)
DARK_MATTER_BUDGET = PlotSpecification(
    plot_id="empire-dark-matter-budget",
    title="Dark Matter",
    data_container_factory=DarkMatterBudgetDataContainer,
    style=PlotStyle.budget,
)
NANITES_BUDGET = PlotSpecification(
    plot_id="empire-nanites-budget",
    title="Nanites",
    data_container_factory=NanitesBudgetDataContainer,
    style=PlotStyle.budget,
)
MINOR_ARTIFACTS_BUDGET = PlotSpecification(
    plot_id="empire-minor-artifacts-budget",
    title="Minor Artifacts Budget",
    data_container_factory=MinorArtifactsBudgetDataContainer,
    style=PlotStyle.budget,
)
ASTRAL_THREADS_BUDGET = PlotSpecification(
    plot_id="empire-astral-threads-budget",
    title="Astral Threads Budget",
    data_container_factory=AstralThreadsBudgetDataContainer,
    style=PlotStyle.budget,
)
BIOMASS_BUDGET = PlotSpecification(
    plot_id="empire-biomass-budget",
    title="Biomass Budget",
    data_container_factory=BiomassBudgetDataContainer,
    style=PlotStyle.budget,
)
INFLUENCE_BUDGET = PlotSpecification(
    plot_id="empire-influence-budget",
    title="Influence",
    data_container_factory=InfluenceBudgetDataContainer,
    style=PlotStyle.budget,
)
UNITY_BUDGET = PlotSpecification(
    plot_id="empire-unity-budget",
    title="Unity",
    data_container_factory=UnityBudgetDataContainer,
    style=PlotStyle.budget,
)
VICTORY_RANK_GRAPH = PlotSpecification(
    plot_id="victory-rank",
    title="Victory Rank (Lower is better!)",
    data_container_factory=VictoryRankDataContainer,
    style=PlotStyle.line,
)
VICTORY_SCORE_GRAPH = PlotSpecification(
    plot_id="victory-score",
    title="Victory Score",
    data_container_factory=VictoryScoreDataContainer,
    style=PlotStyle.line,
)
VICTORY_ECONOMY_SCORE_GRAPH = PlotSpecification(
    plot_id="victory-economy-score",
    title="Victory Economic Score",
    data_container_factory=EconomyScoreDataContainer,
    style=PlotStyle.line,
)

# Below are not shown by default, but can be enabled in configuration
TOTAL_MINERAL_INCOME_GRAPH = PlotSpecification(
    plot_id="total-mineral-income",
    title="Total Mineral Income",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: max(0.0, bi.net_minerals)
    ),
    style=PlotStyle.line,
)
TOTAL_ENERGY_INCOME_GRAPH = PlotSpecification(
    plot_id="total-energy-income",
    title="Total Energy Income",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: max(0.0, bi.net_energy)
    ),
    style=PlotStyle.line,
)
TOTAL_ALLOYS_INCOME_GRAPH = PlotSpecification(
    plot_id="total-alloys-income",
    title="Total Alloys Income",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: max(0.0, bi.net_alloys)
    ),
    style=PlotStyle.line,
)
TOTAL_CONSUMER_GOODS_INCOME_GRAPH = PlotSpecification(
    plot_id="total-consumer-goods-income",
    title="Total Consumer Goods Income",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: max(0.0, bi.net_consumer_goods)
    ),
    style=PlotStyle.line,
)
TOTAL_FOOD_INCOME_GRAPH = PlotSpecification(
    plot_id="total-food-income",
    title="Total Food Income",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: max(0.0, bi.net_food)
    ),
    style=PlotStyle.line,
)
# Below are not shown by default, but can be enabled in configuration
TOTAL_MINERAL_EXPENSE_GRAPH = PlotSpecification(
    plot_id="total-mineral-expense",
    title="Total Mineral Expenses",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: min(0.0, bi.net_minerals)
    ),
    style=PlotStyle.line,
)
TOTAL_ENERGY_EXPENSE_GRAPH = PlotSpecification(
    plot_id="total-energy-expense",
    title="Total Energy Expenses",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: min(0.0, bi.net_energy)
    ),
    style=PlotStyle.line,
)
TOTAL_ALLOYS_EXPENSE_GRAPH = PlotSpecification(
    plot_id="total-alloys-expense",
    title="Total Alloys Expenses",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: min(0.0, bi.net_alloys)
    ),
    style=PlotStyle.line,
)
TOTAL_CONSUMER_GOODS_EXPENSE_GRAPH = PlotSpecification(
    plot_id="total-consumer-goods-expense",
    title="Total Consumer Goods Expenses",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: min(0.0, bi.net_consumer_goods)
    ),
    style=PlotStyle.line,
)
TOTAL_FOOD_EXPENSE_GRAPH = PlotSpecification(
    plot_id="total-food-expense",
    title="Total Food Expenses",
    data_container_factory=functools.partial(
        BudgetSumDataContainer, lambda bi: min(0.0, bi.net_food)
    ),
    style=PlotStyle.line,
)

PLOT_SPECIFICATIONS = {
    "planet_count_graph": PLANET_COUNT_GRAPH,
    "system_count_graph": SYSTEM_COUNT_GRAPH,
    "net_energy_income_graph": NET_ENERGY_INCOME_GRAPH,
    "net_mineral_income_graph": NET_MINERAL_INCOME_GRAPH,
    "net_alloys_income_graph": NET_ALLOYS_INCOME_GRAPH,
    "net_consumer_goods_income_graph": NET_CONSUMER_GOODS_INCOME_GRAPH,
    "net_trade_income_graph": NET_TRADE_INCOME_GRAPH,
    "net_food_income_graph": NET_FOOD_INCOME_GRAPH,
    "net_biomass_income_graph": NET_BIOMASS_INCOME_GRAPH,
    "total_energy_income_graph": TOTAL_ENERGY_INCOME_GRAPH,
    "total_mineral_income_graph": TOTAL_MINERAL_INCOME_GRAPH,
    "total_alloys_income_graph": TOTAL_ALLOYS_INCOME_GRAPH,
    "total_consumer_goods_income_graph": TOTAL_CONSUMER_GOODS_INCOME_GRAPH,
    "total_food_income_graph": TOTAL_FOOD_INCOME_GRAPH,
    "total_energy_expense_graph": TOTAL_ENERGY_EXPENSE_GRAPH,
    "total_mineral_expense_graph": TOTAL_MINERAL_EXPENSE_GRAPH,
    "total_alloys_expense_graph": TOTAL_ALLOYS_EXPENSE_GRAPH,
    "total_consumer_goods_expense_graph": TOTAL_CONSUMER_GOODS_EXPENSE_GRAPH,
    "total_food_expense_graph": TOTAL_FOOD_EXPENSE_GRAPH,
    "empire_size_graph": EMPIRE_SIZE_GRAPH,
    "energy_budget": ENERGY_BUDGET,
    "mineral_budget": MINERAL_BUDGET,
    "consumer_goods_budget": CONSUMER_GOODS_BUDGET,
    "alloys_budget": ALLOYS_BUDGET,
    "trade_budget": TRADE_BUDGET,
    "food_budget": FOOD_BUDGET,
    "influence_budget": INFLUENCE_BUDGET,
    "unity_budget": UNITY_BUDGET,
    "volatile_motes_budget": VOLATILE_MOTES_BUDGET,
    "exotic_gases_budget": EXOTIC_GASES_BUDGET,
    "rare_crystals_budget": RARE_CRYSTALS_BUDGET,
    "living_metal_budget": LIVING_METAL_BUDGET,
    "zro_budget": ZRO_BUDGET,
    "dark_matter_budget": DARK_MATTER_BUDGET,
    "nanites_budget": NANITES_BUDGET,
    "minor_artifacts_budget": MINOR_ARTIFACTS_BUDGET,
    "astral_threads_budget": ASTRAL_THREADS_BUDGET,
    "biomass_budget": BIOMASS_BUDGET,
    "species_distribution_graph": SPECIES_DISTRIBUTION_GRAPH,
    "species_happiness_graph": SPECIES_HAPPINESS_GRAPH,
    "species_crime_graph": SPECIES_CRIME_GRAPH,
    "species_power_graph": SPECIES_POWER_GRAPH,
    "ethos_distribution_graph": ETHOS_DISTRIBUTION_GRAPH,
    "ethos_happiness_graph": ETHOS_HAPPINESS_GRAPH,
    "ethos_crime_graph": ETHOS_CRIME_GRAPH,
    "ethos_power_graph": ETHOS_POWER_GRAPH,
    "strata_distribution_graph": STRATA_DISTRIBUTION_GRAPH,
    "strata_happiness_graph": STRATA_HAPPINESS_GRAPH,
    "strata_crime_graph": STRATA_CRIME_GRAPH,
    "strata_power_graph": STRATA_POWER_GRAPH,
    "job_distribution_graph": JOB_DISTRIBUTION_GRAPH,
    "job_happiness_graph": JOB_HAPPINESS_GRAPH,
    "job_crime_graph": JOB_CRIME_GRAPH,
    "job_power_graph": JOB_POWER_GRAPH,
    "faction_distribution_graph": FACTION_DISTRIBUTION_GRAPH,
    "faction_approval_graph": FACTION_APPROVAL_GRAPH,
    "faction_happiness_graph": FACTION_HAPPINESS_GRAPH,
    "faction_support_graph": FACTION_SUPPORT_GRAPH,
    "faction_crime_graph": FACTION_CRIME_GRAPH,
    "faction_power_graph": FACTION_POWER_GRAPH,
    "planet_pop_distribution_graph": PLANET_POP_DISTRIBUTION_GRAPH,
    "planet_migration_graph": PLANET_MIGRATION_GRAPH,
    "planet_stability_graph": PLANET_STABILITY_GRAPH,
    "planet_happiness_graph": PLANET_HAPPINESS_GRAPH,
    "planet_amenities_graph": PLANET_AMENITIES_GRAPH,
    "planet_housing_graph": PLANET_HOUSING_GRAPH,
    "planet_crime_graph": PLANET_CRIME_GRAPH,
    "planet_power_graph": PLANET_POWER_GRAPH,
    "technology_progress_graph": TECHNOLOGY_PROGRESS_GRAPH,
    "survey_progress_graph": SURVEY_PROGRESS_GRAPH,
    "research_output_graph": RESEARCH_OUTPUT_GRAPH,
    "research_output_by_category_graph": RESEARCH_OUTPUT_BY_CATEGORY_GRAPH,
    "fleet_size_graph": FLEET_SIZE_GRAPH,
    "military_power_graph": MILITARY_POWER_GRAPH,
    "fleet_composition_graph": FLEET_COMPOSITION_GRAPH,
    "victory_rank_graph": VICTORY_RANK_GRAPH,
    "victory_score_graph": VICTORY_SCORE_GRAPH,
    "victory_economy_score_graph": VICTORY_ECONOMY_SCORE_GRAPH,
}

_GALAXY_DATA: Dict[str, "GalaxyMapData"] = {}


def market_graph_id(resource_config) -> str:
    return f"trade-price-{resource_config['name']}"


def market_graph_title(resource_config) -> str:
    resource_name = game_info.convert_id_to_name(
        resource_config["name"], remove_prefix="sr"
    )
    return f"{resource_name} market price"


def get_market_graphs(resource_config) -> List[PlotSpecification]:
    res = []
    for idx, rc in enumerate(resource_config):
        if rc["base_price"] is not None:
            res.append(
                PlotSpecification(
                    plot_id=market_graph_id(rc),
                    title=market_graph_title(rc),
                    data_container_factory=MarketPriceDataContainer,
                    data_container_factory_kwargs=dict(
                        resource_name=rc["name"],
                        base_price=rc["base_price"],
                        resource_index=idx,
                    ),
                    style=PlotStyle.line,
                )
            )
    return res


def get_galaxy_data(game_name: str) -> "GalaxyMapData":
    """Similar to get_current_execution_plot_data, the GalaxyMapData for
    each game is cached in the _GALAXY_DATA dictionary.
    """
    if game_name not in _GALAXY_DATA:
        _GALAXY_DATA[game_name] = GalaxyMapData(game_name)
        _GALAXY_DATA[game_name].initialize_galaxy_graph()
    return _GALAXY_DATA[game_name]


GalaxyMapCoordinate = Tuple[float, float]


class GalaxyMapData:
    """Maintains the data for the historical galaxy map."""

    UNCLAIMED = "Unclaimed"
    ARTIFICIAL_NODE = "artificial-node"

    def __init__(self, game_id: str):
        self.game_id = game_id
        self.galaxy_graph: nx.Graph = None

    def initialize_galaxy_graph(self):
        start_time = time.process_time()
        self.galaxy_graph = nx.Graph()
        with datamodel.get_db_session(self.game_id) as session:
            for system in session.query(datamodel.System):
                assert isinstance(system, datamodel.System)
                self.galaxy_graph.add_node(
                    system.system_id_in_game,
                    name=system.rendered_name,
                    country=GalaxyMapData.UNCLAIMED,
                    system_id=system.system_id,
                    country_id=None,
                    pos=[-system.coordinate_x, -system.coordinate_y],
                )
            for hl in session.query(datamodel.HyperLane).all():
                sys_one, sys_two = (
                    hl.system_one.system_id_in_game,
                    hl.system_two.system_id_in_game,
                )
                self.galaxy_graph.add_edge(sys_one, sys_two, country=self.UNCLAIMED)

        self._prepare_system_shapes()

        logger.debug(
            f"Initialized galaxy graph in {time.process_time() - start_time} seconds."
        )

    def update_graph_for_date(self, time_days: int):
        """
        Update all time-variable properties of the galaxy in the networkx graph (primarily system ownership).
        """
        start_time = time.process_time()
        systems_by_owner = self._get_system_ids_by_owner(time_days)
        owner_by_system = {}
        owner_id_by_system = {}
        for country_tuple, nodes in systems_by_owner.items():
            country_id, country_name = country_tuple
            for node in nodes:
                owner_by_system[node] = country_name
                owner_id_by_system[node] = country_id
                self.galaxy_graph.nodes[node]["country"] = country_name
                self.galaxy_graph.nodes[node]["country_id"] = country_id

        for edge in self.galaxy_graph.edges:
            i, j = edge
            i_country = owner_by_system.get(i, self.UNCLAIMED)
            j_country = owner_by_system.get(j, self.UNCLAIMED)
            if i_country == j_country:
                self.galaxy_graph.edges[edge]["country"] = i_country
            else:
                self.galaxy_graph.edges[edge]["country"] = self.UNCLAIMED

        logger.debug(
            f"Updated galaxy graph in {time.process_time() - start_time:5.3f} seconds."
        )

    def get_country_border_ridges(
        self,
        country_ridges: Dict[str, Set[Tuple[GalaxyMapCoordinate, GalaxyMapCoordinate]]],
    ) -> Iterable[Tuple[List[float], List[float]]]:
        """
        :param country_ridges: Dict mapping country name to ridges (defined by pairs of vertices)
        :return: Iterate over those ridges which lie at the border of a country (to another country,
            to unclaimed space, or to the edge of the galaxy)
        """
        for c1, r1 in country_ridges.items():
            for c2, r2 in country_ridges.items():
                if c2 <= c1:
                    continue
                intersecting_ridges = r1 & r2
                for rv1, rv2 in intersecting_ridges:
                    yield [rv1[0], rv2[0]], [rv1[1], rv2[1]]

    def _get_system_ids_by_owner(self, time_days) -> Dict[Tuple[str, str], Set[int]]:
        owned_systems = set()
        systems_by_owner = {(None, GalaxyMapData.UNCLAIMED): set()}

        with datamodel.get_db_session(self.game_id) as session:
            for system in session.query(datamodel.System):
                country = system.get_owner_country_at(time_days)
                country_tuple = self._country_display_tuple(country)
                owned_systems.add(system.system_id_in_game)
                if country_tuple not in systems_by_owner:
                    systems_by_owner[country_tuple] = set()
                systems_by_owner[country_tuple].add(system.system_id_in_game)

        systems_by_owner[(None, GalaxyMapData.UNCLAIMED)] |= (
            set(self.galaxy_graph.nodes) - owned_systems
        )
        return systems_by_owner

    def _prepare_system_shapes(self):
        points = [
            self.galaxy_graph.nodes[node]["pos"]
            for node in sorted(self.galaxy_graph.nodes)
        ]

        min_radius = float("inf")
        max_radius = float("-inf")
        for x, y in points:
            radius = np.sqrt(x**2 + y**2)
            min_radius = min(min_radius, radius)
            max_radius = max(max_radius, radius)

        # add artificial points around the galaxy and the center to make a clean boundary
        angles = np.linspace(0, 2 * np.pi, 32)
        _sin = np.sin(angles)
        _cos = np.cos(angles)
        outer = 1.1 * max_radius
        points += [[outer * _c, outer * _s] for _c, _s in zip(_sin, _cos)]
        inner = 0.8 * min_radius
        points += [[inner * _c, inner * _s] for _c, _s in zip(_sin, _cos)]

        voronoi = Voronoi(np.array(points))
        for i, node in enumerate(sorted(self.galaxy_graph.nodes)):
            region = voronoi.regions[voronoi.point_region[i]]

            vertices = [voronoi.vertices[v] for v in region if v != -1]
            shape_x, shape_y = zip(
                *[
                    v
                    for v in vertices
                    if 0.5 * min_radius
                    <= np.sqrt(v[0] ** 2 + v[1] ** 2)
                    <= 1.5 * max_radius
                ]
            )
            self.galaxy_graph.nodes[node]["shape"] = shape_x, shape_y

        self._extract_voronoi_ridges(voronoi)

    def _extract_voronoi_ridges(self, voronoi: Voronoi):
        """
        Adjacent systems in the Voronoi diagram are separated by ridges, which can be described in two ways:
        - ridge points: Index to the pair of input points separated by the ridge
        - ridge vertices: Index to the pair of "output" points connected by the ridge

        For each node, we collect all ridges for which it is a ridge point, transform the ridge vertices
        into map coordinates, and store these in the graph metadata. Ridge vertices for artificial nodes are stored
        as well (so the borders can also be drawn around the edge of the galaxy).
        """
        self.galaxy_graph.graph["system_borders"] = defaultdict(set)

        for ridge_points, ridge_vertices in zip(
            voronoi.ridge_points, voronoi.ridge_vertices
        ):
            for rp in ridge_points:
                if rp not in self.galaxy_graph:
                    # substitute placeholder for artificial nodes
                    rp = GalaxyMapData.ARTIFICIAL_NODE

                rv1, rv2 = ridge_vertices
                rv_tuple = (tuple(voronoi.vertices[rv1]), tuple(voronoi.vertices[rv2]))

                self.galaxy_graph.graph["system_borders"][rp].add(rv_tuple)

    def _country_display_tuple(self, country: datamodel.Country) -> Tuple[str, str]:
        if country is None:
            return (None, GalaxyMapData.UNCLAIMED)
        if config.CONFIG.show_everything:
            return (country.country_id, country.rendered_name)
        if not country.has_met_player():
            return (None, GalaxyMapData.UNCLAIMED)
        return (country.country_id, country.rendered_name)


_MIN_V = 0.4
_MAX_V = 1.0
_V_SHIFTS = [
    0.2,
    0.4,
    0.6,
]
_GRAYSCALE_S = 0.2


class CountryColors:
    def __init__(self):
        self.map_colors: Dict[str, tuple] = {}
        self.country_name_to_primary_color: Dict[str, tuple] = {}
        self._used_hsv = set()

    def load(self, game_id: str):
        for game_data_dir in reversed(config.CONFIG.game_data_dirs):
            colors_path = game_data_dir / "flags/colors.txt"
            if colors_path.exists():
                self.map_colors = self._parse_map_colors(colors_path)
                break

        with datamodel.get_db_session(game_id) as session:
            for c in session.query(datamodel.Country).order_by("country_id_in_game"):
                name = c.rendered_name
                # avoid grayscale colors if possible; they can be hard to distinguish
                color = (
                    c.secondary_color
                    if self._is_grayscale_color(c.primary_color)
                    and not self._is_grayscale_color(c.secondary_color)
                    else c.primary_color
                )
                # try to avoid duplicate colors
                # don't bother for non-default-or-fallen-empire countries, so that the "real" countries are more likely to get their color
                rgb = self._get_rgb(color, avoid_used=c.is_real_country())
                self.country_name_to_primary_color[name] = rgb

    def _get_rgb(self, key: str, avoid_used: bool):
        r, g, b = self.map_colors.get(key, (0, 0, 0))
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        v = min(v, _MAX_V)
        v = max(v, _MIN_V)
        if avoid_used:
            for shift in itertools.chain([0.0], *((v, -v) for v in _V_SHIFTS)):
                v_shifted = s + shift
                if (
                    v_shifted >= _MIN_V
                    and v_shifted <= _MAX_V
                    and self._round_hsv(h, s, v_shifted) not in self._used_hsv
                ):
                    v = v_shifted
                    break
            self._used_hsv.add(self._round_hsv(h, s, v))
        return tuple(int(v * 255) for v in colorsys.hsv_to_rgb(h, s, v))
    
    @staticmethod
    def _round_hsv(h: float, s: float, v: float):
        return round(h, 1), round(s, 1), round(v, 1)

    def _is_grayscale_color(self, key: str):
        r, g, b = self.map_colors.get(key, (0, 0, 0))
        _, s, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        return s < _GRAYSCALE_S

    def has_color_for_name(self, country_name: str):
        return country_name in self.country_name_to_primary_color

    def get_color_by_name(self, country_name: str):
        return self.country_name_to_primary_color[country_name]

    @staticmethod
    def _parse_map_colors(path: pathlib.Path):
        with open(path, "r") as f:
            prepared_str = re.sub(r"#[^\n]*", "", f.read())  # strip out comments
            data = rust_parser.parse_save_from_string(prepared_str)

            colors = data.get("colors", {})
            map_colors = {}
            for key in colors:
                space, v1, v2, v3 = colors[key]["map"]
                if space == "rgb":
                    map_colors[key] = (int(v1), int(v2), int(v3))
                elif space == "hsv":
                    rgb = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(v1, v2, v3))
                    map_colors[key] = rgb
                else:
                    raise RuntimeError(f"Unexpected color space: {space}")
            return map_colors
