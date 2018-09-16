import itertools
import logging
import random
from typing import Dict, Any, List

import time

from stellarisdashboard import models, game_info, config

logger = logging.getLogger(__name__)


# noinspection PyArgumentList
class TimelineExtractor:
    """ Process data from parsed save file dictionaries and add it to the database. """

    # Some constants to represent custom "factions"
    NO_FACTION = "No faction"
    SLAVE_FACTION_NAME = "No faction (enslaved)"
    PURGE_FACTION_NAME = "No faction (purge)"
    NON_SENTIENT_ROBOT_NO_FACTION = "No faction (non-sentient robot)"
    NO_FACTION_ID = -1
    SLAVE_FACTION_ID = -2
    PURGE_FACTION_ID = -3
    NON_SENTIENT_ROBOT_FACTION_ID = -4

    NO_FACTION_POP_ETHICS = {
        NO_FACTION: models.PopEthics.no_ethics,
        SLAVE_FACTION_NAME: models.PopEthics.enslaved,
        PURGE_FACTION_NAME: models.PopEthics.purge,
        NON_SENTIENT_ROBOT_NO_FACTION: models.PopEthics.no_ethics,
    }

    NO_FACTION_ID_MAP = {
        NO_FACTION_ID: NO_FACTION_ID,
        SLAVE_FACTION_NAME: SLAVE_FACTION_ID,
        PURGE_FACTION_NAME: PURGE_FACTION_ID,
        NON_SENTIENT_ROBOT_NO_FACTION: NON_SENTIENT_ROBOT_FACTION_ID,
    }

    def __init__(self):
        self._gamestate_dict = None
        self.game = None
        self._session = None
        self._player_country: int = None
        self._current_gamestate = None
        self._country_starbases_dict = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None
        self._factionless_pops = None
        self._date_in_days = None
        self._logger_str = None

        self._new_models = []
        self._enclave_trade_modifiers = None
        self._initialize_enclave_trade_info()

    def process_gamestate(self, game_name: str, gamestate_dict: Dict[str, Any]):
        date_str = gamestate_dict["date"]
        self._logger_str = f"{game_name} {date_str}:"
        logger.info(f"Processing {game_name}, {date_str}")
        self._gamestate_dict = gamestate_dict
        if len({player["country"] for player in self._gamestate_dict["player"]}) != 1:
            logger.warning(f"{self._logger_str} Player country is ambiguous!")
            return None
        self._player_country = self._gamestate_dict["player"][0]["country"]
        player_country_name = self._gamestate_dict["country"][self._player_country]["name"]
        with models.get_db_session(game_name) as self._session:
            try:
                self.game = self._session.query(models.Game).filter_by(game_name=game_name).first()
                if self.game is None:
                    logger.info(f"Adding new game {game_name} to database.")
                    self.game = models.Game(game_name=game_name, player_country_name=player_country_name)
                    self._session.add(self.game)
                    self._extract_galaxy_data()
                self._date_in_days = models.date_to_days(self._gamestate_dict["date"])
                game_states_by_date = {gs.date: gs for gs in self.game.game_states}
                if self._date_in_days in game_states_by_date:
                    logger.info(f"Gamestate for {self.game.game_name}, date {date_str} exists! Replacing...")
                    self._session.delete(game_states_by_date[self._date_in_days])
                self._process_gamestate()
                self._session.commit()
                logger.info(f"{self._logger_str} Committed changes to database.")
            except Exception as e:
                self._session.rollback()
                logger.error(e)
                raise e
            finally:
                self._session = None
                self._reset_state()

    def _extract_galaxy_data(self):
        logger.info(f"{self._logger_str} Extracting galaxy data...")
        hyperlanes = set()
        for system_id_in_game, system_data in self._gamestate_dict.get("galactic_object", {}).items():
            original_system_name = system_data.get("name")
            coordinate_x = system_data.get("coordinate", {}).get("x", 0)
            coordinate_y = system_data.get("coordinate", {}).get("y", 0)
            self._session.add(models.System(
                game=self.game,
                system_id_in_game=system_id_in_game,
                original_name=original_system_name,
                coordinate_x=coordinate_x,
                coordinate_y=coordinate_y,
            ))
            for hl_data in system_data.get("hyperlane", []):
                neighbor = hl_data.get("to")
                # use frozenset to avoid antiparallel duplicates
                if neighbor == system_id_in_game:
                    continue  # This can happen in Stellaris 2.1
                hyperlanes.add(frozenset([system_id_in_game, neighbor]))
        for hl in hyperlanes:
            a, b = hl
            one = self._session.query(models.System).filter_by(system_id_in_game=a, ).one_or_none()
            two = self._session.query(models.System).filter_by(system_id_in_game=b, ).one_or_none()
            if one is None or two is None:
                logger.warning(f"Could not find systems with IDs {a}, {b}")
            self._session.add(models.HyperLane(
                system_one=one,
                system_two=two,
            ))

    def _process_gamestate(self):
        self._extract_player_trade_agreements()
        self._count_starbases()
        player_economy = self._extract_player_economy(self._gamestate_dict["country"][self._player_country])
        self._current_gamestate = models.GameState(
            game=self.game, date=self._date_in_days,
            **player_economy
        )
        self._session.add(self._current_gamestate)

        player_country = None
        player_country_data = None
        player_country_dict = {}
        for country_id, country_data_dict in self._gamestate_dict["country"].items():
            if not isinstance(country_data_dict, dict):
                continue

            country = self._session.query(models.Country).filter_by(
                game=self.game,
                country_id_in_game=country_id
            ).one_or_none()
            country_type = country_data_dict.get("type")
            if country is None:
                country = models.Country(
                    is_player=(country_id == self._player_country),
                    country_id_in_game=country_id,
                    game=self.game,
                    country_type=country_type,
                    country_name=country_data_dict.get("name", "no name")
                )
                if country_id == self._player_country:
                    country.first_player_contact_date = 0
                self._session.add(country)

            diplomacy_data = self._extract_diplomacy_toward_player(country_data_dict)
            if country.first_player_contact_date is None and diplomacy_data.get("has_communications_with_player"):
                country.first_player_contact_date = self._date_in_days
                self._session.add(country)

            if country_type not in {"default", "fallen_empire", "awakened_fallen_empire"}:
                continue  # Enclaves, Leviathans, etc ....
            self._extract_country_government(country, country_data_dict)

            country_data = self._extract_country_data(country_id, country, country_data_dict, diplomacy_data)
            if country_data.attitude_towards_player.is_known():
                debug_name = country_data_dict.get('name', 'Unnamed Country')
                logger.info(f"{self._logger_str} Extracting country info: {debug_name}")

            self._extract_pop_info_from_planets(country_data_dict, country_data)
            if country_data.country.is_player:
                player_country = country
                player_country_data = country_data
                player_country_dict = country_data_dict

        self._extract_wars()
        self._extract_player_leaders(player_country, player_country_dict)
        self._extract_factions_and_faction_leaders(player_country_data)
        self._add_ruler_leader_achievements(player_country_dict)
        self._add_scientist_tech_achievements(player_country_dict)
        self._add_governor_achievements(player_country_dict)
        if config.CONFIG.extract_system_ownership:
            self._extract_system_ownership()

    def _extract_country_government(self, country_model: models.Country, country_dict):
        prev_gov = self._session.query(models.Government).filter_by(
            country=country_model,
        ).order_by(
            models.Government.end_date_days.desc()
        ).first()

        gov_name = country_dict.get("name", "Unnamed Country")

        ethics_list = country_dict.get("ethos", {}).get("ethic", [])
        if not isinstance(ethics_list, list):
            ethics_list = [ethics_list]
        ethics = set(ethics_list)

        civics_list = country_dict.get("government", {}).get("civics", [])
        if not isinstance(civics_list, list):
            civics_list = [civics_list]
        civics = set(civics_list)
        authority = models.GovernmentAuthority.from_str(
            country_dict.get("government", {}).get("authority", "other")
        )
        gov_type = country_dict.get("government", {}).get("type", "other")
        gov_was_reformed = False
        if prev_gov is not None:
            prev_gov.end_date_days = self._date_in_days - 1
            self._session.add(prev_gov)
            previous_ethics = [prev_gov.ethics_1, prev_gov.ethics_2, prev_gov.ethics_3, prev_gov.ethics_4, prev_gov.ethics_5]
            previous_ethics = set(previous_ethics) - {None}
            previous_civics = [prev_gov.civic_1, prev_gov.civic_2, prev_gov.civic_3, prev_gov.civic_4, prev_gov.civic_5]
            previous_civics = set(previous_civics) - {None}
            gov_was_reformed = ((ethics != previous_ethics)
                                or (civics != previous_civics)
                                or (gov_name != prev_gov.gov_name)
                                or (gov_type != prev_gov.gov_type))
            # nothing has changed...
            if not gov_was_reformed:
                return

        ethics = dict(zip([f"ethics_{i}" for i in range(1, 6)], ethics))
        civics = dict(zip([f"civic_{i}" for i in range(1, 6)], civics))

        gov = models.Government(
            country=country_model,
            start_date_days=self._date_in_days - 1,
            end_date_days=self._date_in_days + 1,
            gov_name=gov_name,
            gov_type=gov_type,
            authority=authority,
            **ethics,
            **civics,
        )
        self._session.add(gov)
        if gov_was_reformed and country_model.country_id_in_game == self._player_country:
            ruler = self._get_current_ruler(country_dict)
            if ruler is not None:
                self._session.add(models.LeaderAchievement(
                    leader=ruler,
                    achievement_type=models.LeaderAchievementType.reformed_government,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    achievement_description="",
                ))

    def _count_starbases(self):
        self._country_starbases_dict = {}
        for starbase_dict in self._gamestate_dict.get("starbases", {}).values():
            if not isinstance(starbase_dict, dict):
                continue
            owner_id = starbase_dict.get("owner", -1)
            if owner_id not in self._country_starbases_dict:
                self._country_starbases_dict[owner_id] = set()
            self._country_starbases_dict[owner_id].add(starbase_dict.get("system", -1))

    def _extract_country_data(self, country_id, country: models.Country, country_dict, diplomacy_data) -> models.CountryData:
        is_player = (country_id == self._player_country)
        has_research_agreement_with_player = is_player or (country_id in self._player_research_agreements)

        has_sensor_link_with_player = is_player or (country_id in self._player_sensor_links)
        if is_player:
            attitude_towards_player = models.Attitude.friendly
        else:
            attitude_towards_player = self._extract_ai_attitude_towards_player(country_dict)

        economy_data = self._extract_economy_data(country_dict)
        country_data = models.CountryData(
            country=country,
            game_state=self._current_gamestate,
            military_power=country_dict.get("military_power", 0),
            fleet_size=country_dict.get("fleet_size", 0),
            tech_progress=len(country_dict.get("tech_status", {}).get("technology", [])),
            exploration_progress=len(country_dict.get("surveyed", 0)),
            owned_planets=len(country_dict.get("owned_planets", [])),
            controlled_systems=len(self._country_starbases_dict.get(country_id, [])),
            has_research_agreement_with_player=has_research_agreement_with_player,
            has_sensor_link_with_player=has_sensor_link_with_player,
            attitude_towards_player=attitude_towards_player,
            **economy_data,
            **diplomacy_data,
        )
        self._session.add(country_data)
        return country_data

    def _extract_colonized_planets(self, country_dict):
        for planet_id in country_dict.get("owned_planets", []):
            pass

    def _extract_diplomacy_toward_player(self, country_dict):
        relations_manager = country_dict.get("relations_manager", [])
        diplomacy_info = dict(
            has_rivalry_with_player=False,
            has_defensive_pact_with_player=False,
            has_federation_with_player=False,
            has_non_aggression_pact_with_player=False,
            has_closed_borders_with_player=False,
            has_communications_with_player=False,
            has_migration_treaty_with_player=False,
        )
        if not isinstance(relations_manager, dict):
            return diplomacy_info
        relation_list = relations_manager.get("relation", [])
        if not isinstance(relation_list, list):  # if there is only one
            relation_list = [relation_list]
        for relation in relation_list:
            if not isinstance(relation, dict):
                continue
            if relation.get("country") != self._player_country:
                continue
            diplomacy_info.update(
                has_rivalry_with_player=relation.get("is_rival") == "yes",
                has_defensive_pact_with_player=relation.get("defensive_pact") == "yes",
                has_federation_with_player=relation.get("alliance") == "yes",
                has_non_aggression_pact_with_player=relation.get("non_aggression_pledge") == "yes",
                has_closed_borders_with_player=relation.get("closed_borders") == "yes",
                has_communications_with_player=relation.get("communications") == "yes",
                has_migration_treaty_with_player=relation.get("migration_access") == "yes",
            )
            break
        return diplomacy_info

    def _extract_player_economy(self, country_dict):
        base_economy_data = self._extract_economy_data(country_dict)
        last_month = self._get_last_month_budget(country_dict)
        economy_dict = {}
        energy_budget = last_month.get("values", {})

        enclave_deals = self._extract_enclave_resource_deals(country_dict)
        sector_budget_info = self._extract_sector_budget_info(country_dict)
        economy_dict.update(
            energy_income_base=energy_budget.get("base_income", 0),
            energy_income_production=energy_budget.get("produced_energy", 0),
            energy_income_trade=self._player_monthly_trade_info["energy_trade_income"],
            energy_income_sectors=sector_budget_info["energy_income_sectors"],
            energy_income_mission=energy_budget.get("mission_expense", 0),
            energy_spending_army=energy_budget.get("army_maintenance", 0),
            energy_spending_colonization=energy_budget.get("colonization", 0),
            energy_spending_starbases=energy_budget.get("starbase_maintenance", 0),
            energy_spending_trade=self._player_monthly_trade_info["energy_trade_spending"],
            energy_spending_building=energy_budget.get("buildings", 0),
            energy_spending_pop=energy_budget.get("pops", 0),
            energy_spending_ship=energy_budget.get("ship_maintenance", 0),
            energy_spending_station=energy_budget.get("station_maintenance", 0),
            mineral_income_production=base_economy_data.get("mineral_income", 0),  # note: includes sector production!
            mineral_income_trade=self._player_monthly_trade_info["mineral_trade_income"],
            mineral_income_sectors=sector_budget_info["mineral_income_sectors"],
            mineral_spending_pop=last_month.get("pop_mineral_maintenance", 0),
            mineral_spending_ship=last_month.get("ship_mineral_maintenances", 0),
            mineral_spending_trade=self._player_monthly_trade_info["mineral_trade_spending"],
            food_income_production=base_economy_data.get("food_income", 0),
            food_income_trade=self._player_monthly_trade_info["food_trade_income"],
            food_income_sectors=sector_budget_info["food_income_sectors"],
            food_spending=base_economy_data.get("food_spending", 0),
            food_spending_trade=self._player_monthly_trade_info["food_trade_spending"],
            food_spending_sectors=sector_budget_info["food_spending_sectors"],
            mineral_income_enclaves=enclave_deals["mineral_income_enclaves"],
            mineral_spending_enclaves=enclave_deals["mineral_spending_enclaves"],
            energy_income_enclaves=enclave_deals["energy_income_enclaves"],
            energy_spending_enclaves=enclave_deals["energy_spending_enclaves"],
            food_income_enclaves=enclave_deals["food_income_enclaves"],
            food_spending_enclaves=enclave_deals["food_spending_enclaves"],
        )
        return economy_dict

    def _extract_enclave_resource_deals(self, country_dict):
        enclave_deals = dict(
            mineral_income_enclaves=0,
            mineral_spending_enclaves=0,
            energy_income_enclaves=0,
            energy_spending_enclaves=0,
            food_income_enclaves=0,
            food_spending_enclaves=0,
        )

        timed_modifier_list = country_dict.get("timed_modifier", [])
        if not isinstance(timed_modifier_list, list):
            # if for some reason there is only a single timed_modifier, timed_modifier_list will not be a list but a dictionary => Put it in a list!
            timed_modifier_list = [timed_modifier_list]
        for modifier_dict in timed_modifier_list:
            if not isinstance(modifier_dict, dict):
                continue
            modifier_id = modifier_dict.get("modifier", "")
            enclave_trade_budget_dict = self._enclave_trade_modifiers.get(modifier_id, {})
            for budget_item, amount in enclave_trade_budget_dict.items():
                enclave_deals[budget_item] += amount
        # Make spending numbers negative:
        enclave_deals["mineral_spending_enclaves"] *= -1
        enclave_deals["energy_spending_enclaves"] *= -1
        enclave_deals["food_spending_enclaves"] *= -1
        return enclave_deals

    @staticmethod
    def _extract_sector_budget_info(country_dict):
        sector_economy_data = dict(
            energy_income_sectors=0,
            mineral_income_sectors=0,
            food_income_sectors=0,
            food_spending_sectors=0,
        )
        share_amount_dict = {
            "none": 0,
            "quarter": 0.25,
            "half": 0.5,
            "three_quarters": 0.75,
        }
        economy_module = TimelineExtractor._get_economy_module(country_dict)
        sectors_dict = country_dict.get("sectors", {})
        if not isinstance(sectors_dict, dict):
            return sector_economy_data

        for sector_economy in economy_module.get("sectors", []):
            if "sector" not in sector_economy:
                logger.warning(f"Weird: Sector economy module has no sector id:\n{sector_economy}")
                continue

            sector_id = sector_economy["sector"]
            sector_info_dict = sectors_dict.get(sector_id, {})
            sector_resources = sector_economy.get("resources", {})

            energy_budget_list = sector_resources.get("energy")
            sector_energy_share_factor = share_amount_dict[sector_info_dict.get("energy_share", "half")]
            if isinstance(energy_budget_list, list):
                energy_income = energy_budget_list[1] - energy_budget_list[2]
                # the data we are reading gives the balances *available to* the sector. need to do some math to get the balances *returned by* the sector
                sector_economy_data["energy_income_sectors"] += max(0, sector_energy_share_factor * energy_income / (1 - sector_energy_share_factor))

            mineral_budget_list = sector_resources.get("minerals")
            sector_mineral_share_factor = share_amount_dict[sector_info_dict.get("minerals_share", "half")]
            if isinstance(mineral_budget_list, list):
                mineral_income = mineral_budget_list[1] - mineral_budget_list[2]
                sector_economy_data["mineral_income_sectors"] += max(0, sector_mineral_share_factor * mineral_income / (1 - sector_mineral_share_factor))

            # Excess Food is passed to the core sector directly.
            food_budget_list = sector_resources.get("food")
            if isinstance(food_budget_list, list):
                sector_economy_data["food_income_sectors"] += max(0, food_budget_list[1])
                sector_economy_data["food_spending_sectors"] += max(0, food_budget_list[2])

        return sector_economy_data

    @staticmethod
    def _get_last_month_budget(country_dict):
        if "budget" in country_dict and country_dict["budget"] != "none" and country_dict["budget"]:
            return country_dict["budget"].get("last_month", {})
        return {}

    @staticmethod
    def _get_economy_module(country_dict):
        if "modules" not in country_dict:
            return {}
        if "standard_economy_module" not in country_dict["modules"]:
            return {}
        return country_dict["modules"]["standard_economy_module"]

    @staticmethod
    def _extract_economy_data(country_dict):
        economy = {}
        economy_module = TimelineExtractor._get_economy_module(country_dict)
        if "last_month" in economy_module:
            last_month_economy = economy_module["last_month"]
            default = [0, 0, 0]
            for key, dict_key, val_index in [
                ("mineral_income", "minerals", 1),
                ("mineral_spending", "minerals", 2),
                ("energy_income", "energy", 1),
                ("energy_spending", "energy", 2),
                ("food_income", "food", 1),
                ("food_spending", "food", 2),
                ("unity_income", "unity", 1),
                ("unity_spending", "unity", 2),
                ("influence_income", "influence", 1),
                ("influence_spending", "influence", 2),
                ("society_research", "society_research", 1),
                ("physics_research", "physics_research", 1),
                ("engineering_research", "engineering_research", 1),
            ]:
                val_list = last_month_economy.get(dict_key, default)
                if isinstance(val_list, list):
                    economy[key] = val_list[val_index]
        return economy

    def _extract_ai_attitude_towards_player(self, country_data):
        attitude_towards_player = "unknown"
        ai = country_data.get("ai", {})
        if isinstance(ai, dict):
            attitudes = ai.get("attitude", [])
            for attitude in attitudes:
                if not isinstance(attitude, dict):
                    continue
                if attitude["country"] == self._player_country:
                    attitude_towards_player = attitude["attitude"]
                    break
            attitude_towards_player = models.Attitude.__members__.get(attitude_towards_player, models.Attitude.unknown)
        return attitude_towards_player

    def _extract_player_trade_agreements(self):
        self._player_research_agreements = set()
        self._player_sensor_links = set()
        self._player_monthly_trade_info = dict(
            mineral_trade_income=0,
            mineral_trade_spending=0,
            energy_trade_income=0,
            energy_trade_spending=0,
            food_trade_income=0,
            food_trade_spending=0,
        )
        trades = self._gamestate_dict.get("trade_deal", {})
        if not trades:
            return
        for trade_id, trade_deal in trades.items():
            if not isinstance(trade_deal, dict):
                continue  # could be "none"
            first = trade_deal.get("first", {})
            second = trade_deal.get("second", {})
            if first.get("country", -1) != self._player_country:
                first, second = second, first  # make it so player is always first party
            if first.get("country", -1) != self._player_country:
                continue  # trade doesn't involve player
            if second.get("research_agreement") == "yes":
                self._player_research_agreements.add(second["country"])
            if second.get("sensor_link") == "yes":
                self._player_sensor_links.add(second["country"])

            player_resources = first.get("monthly_resources", {})
            self._player_monthly_trade_info["mineral_trade_spending"] -= player_resources.get("minerals", 0)
            self._player_monthly_trade_info["energy_trade_spending"] -= player_resources.get("energy", 0)
            self._player_monthly_trade_info["food_trade_spending"] -= player_resources.get("food", 0)
            other_resources = second.get("monthly_resources", {})
            self._player_monthly_trade_info["mineral_trade_income"] += other_resources.get("minerals", 0)
            self._player_monthly_trade_info["energy_trade_income"] += other_resources.get("energy", 0)
            self._player_monthly_trade_info["food_trade_income"] += other_resources.get("food", 0)

    def _extract_factions_and_faction_leaders(self, country_data: models.CountryData):
        faction_pop_sum = 0
        for faction_id, faction_dict in self._gamestate_dict.get("pop_factions", {}).items():
            if not faction_dict or faction_dict == "none":
                continue
            faction_name = faction_dict["name"]
            country_id = faction_dict["country"]
            faction_country_name = self._gamestate_dict["country"][country_id]["name"]
            if faction_country_name != country_data.country.country_name:
                continue
            # If the faction is in the database, get it, otherwise add a new faction
            members = len(faction_dict.get("members", []))
            faction_pop_sum += members
            self._add_faction_and_faction_support(
                faction_id_in_game=faction_id,
                faction_name=faction_name,
                country_data=country_data,
                members=members,
                support=faction_dict.get("support", 0),
                happiness=faction_dict.get("happiness", 0),
                ethics=models.PopEthics.from_str(faction_dict.get("type")),
            )
            self._add_faction_leader_achievement(faction_dict)

        for faction_name, num in self._factionless_pops.items():
            if not num:
                continue
            faction_pop_sum += num
            self._add_faction_and_faction_support(
                faction_id_in_game=self.NO_FACTION_ID_MAP[faction_name],
                faction_name=faction_name,
                country_data=country_data,
                members=num,
                support=0,
                happiness=0,
                ethics=TimelineExtractor.NO_FACTION_POP_ETHICS[faction_name],
            )

        no_faction_pops = sum(pc.pop_count for pc in country_data.pop_counts) - faction_pop_sum
        if no_faction_pops:
            self._add_faction_and_faction_support(
                faction_id_in_game=self.NO_FACTION_ID,
                faction_name=TimelineExtractor.NO_FACTION,
                country_data=country_data,
                members=no_faction_pops,
                support=0,
                happiness=0,
                ethics=models.PopEthics.no_ethics
            )
            self._factionless_pops = None

    def _add_faction_and_faction_support(self,
                                         faction_id_in_game: int,
                                         faction_name: str,
                                         country_data: models.CountryData,
                                         members: int,
                                         support: float,
                                         happiness: float,
                                         ethics: models.PopEthics,
                                         ):
        faction = self._session.query(models.PoliticalFaction).filter_by(
            faction_id_in_game=faction_id_in_game,
            country=country_data.country,
        ).one_or_none()
        if faction is None:
            faction = models.PoliticalFaction(
                country=country_data.country,
                faction_name=faction_name,
                faction_id_in_game=faction_id_in_game,
                ethics=ethics,
            )
            self._session.add(faction)
        self._session.add(models.FactionSupport(
            faction=faction,
            country_data=country_data,
            members=members,
            support=support,
            happiness=happiness)
        )

    def _extract_pop_info_from_planets(self, country_dict: Dict[str, Any], country_data: models.CountryData):
        self._factionless_pops = {
            TimelineExtractor.SLAVE_FACTION_NAME: 0,
            TimelineExtractor.PURGE_FACTION_NAME: 0,
            TimelineExtractor.NON_SENTIENT_ROBOT_NO_FACTION: 0,
        }
        species_demographics = {}
        pop_data = self._gamestate_dict["pop"]

        for planet_id in country_dict.get("owned_planets", []):
            tiles = self._gamestate_dict["planet"][planet_id].get("tiles", [])
            for tile_data in tiles.values():
                pop_id = tile_data.get("pop")
                if pop_id is None:
                    continue
                elif pop_id not in pop_data:
                    logger.warning(f"{self._logger_str} Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                this_pop = pop_data[pop_id]
                if not isinstance(this_pop, dict):
                    continue  # might be "none"
                if this_pop["growth_state"] != "grown":
                    continue
                species_id = this_pop["species_index"]
                species_dict = self._gamestate_dict["species"][species_id]
                if species_id not in species_demographics:
                    species_demographics[species_id] = 0
                species_demographics[species_id] += 1
                if this_pop.get("enslaved") == "yes":
                    self._factionless_pops[TimelineExtractor.SLAVE_FACTION_NAME] += 1
                elif this_pop.get("purging") == "yes":
                    self._factionless_pops[TimelineExtractor.PURGE_FACTION_NAME] += 1
                elif species_dict.get("class") == "ROBOT" and species_dict.get("pops_can_join_factions") == "no":
                    self._factionless_pops[TimelineExtractor.NON_SENTIENT_ROBOT_NO_FACTION] += 1

        for species_id, pop_count in species_demographics.items():
            species_data = self._gamestate_dict["species"][species_id]
            species_name = species_data["name"]
            species = self._session.query(models.Species).filter_by(
                game=self.game, species_id_in_game=species_id
            ).one_or_none()
            if species is None:
                species = models.Species(
                    game=self.game,
                    species_name=species_name,
                    species_id_in_game=species_id,
                    is_robotic=species_data["class"] == "ROBOT",
                    parent_species_id_in_game=species_data.get("base", -1),
                )
                self._session.add(species)
            pop_count = models.PopCount(
                country_data=country_data,
                species=species,
                pop_count=pop_count,
            )
            self._session.add(pop_count)

    def _extract_wars(self):
        logger.info(f"{self._logger_str} Processing Wars")
        wars_dict = self._gamestate_dict.get("war", {})
        if not wars_dict:
            return

        for war_id, war_dict in wars_dict.items():
            if not isinstance(war_dict, dict):
                continue
            war_name = war_dict.get("name", "Unnamed war")
            war = self._session.query(models.War).order_by(models.War.end_date_days.desc()).filter_by(
                game=self.game, name=war_name
            ).first()
            if war is None:
                start_date_days = models.date_to_days(war_dict["start_date"])
                war = models.War(
                    war_id_in_game=war_id,
                    game=self.game,
                    start_date_days=start_date_days,
                    end_date_days=self._date_in_days,
                    name=war_name,
                    outcome=models.WarOutcome.in_progress,
                )
                self._session.add(war)
            elif war_dict.get("defender_force_peace") == "yes":
                war.outcome = models.WarOutcome.status_quo
                war.end_date_days = models.date_to_days(war_dict.get("defender_force_peace_date"))
            elif war_dict.get("attacker_force_peace") == "yes":
                war.outcome = models.WarOutcome.status_quo
                war.end_date_days = models.date_to_days(war_dict.get("attacker_force_peace_date"))
            elif war.outcome != models.WarOutcome.in_progress:
                continue
            else:
                war.end_date_days = self._date_in_days
            war_goal_attacker = war_dict.get("attacker_war_goal", {}).get("type")
            war_goal_defender = war_dict.get("defender_war_goal", {})
            if war_goal_defender:
                war_goal_defender = war_goal_defender.get("type")
            else:
                war_goal_defender = None
            attackers = {p["country"] for p in war_dict["attackers"]}
            for war_party_info in itertools.chain(war_dict["attackers"], war_dict["defenders"]):
                if not isinstance(war_party_info, dict):
                    continue  # just in case
                country_id = war_party_info.get("country")
                db_country = self._session.query(models.Country).filter_by(game=self.game, country_id_in_game=country_id).one_or_none()

                if db_country is None:
                    country_name = self._gamestate_dict["country"][country_id]["name"]
                    logger.warning(f"Could not find country matching war participant {country_name}")
                    continue

                is_attacker = country_id in attackers

                war_participant = self._session.query(models.WarParticipant).filter_by(
                    war=war, country=db_country
                ).one_or_none()
                if war_participant is None:
                    war_goal = war_goal_attacker if is_attacker else war_goal_defender
                    war_participant = models.WarParticipant(
                        war=war,
                        war_goal=models.WarGoal.__members__.get(war_goal, models.WarGoal.wg_other),
                        country=db_country,
                        is_attacker=is_attacker,
                    )
                    self._session.add(war_participant)
                if war_participant.war_goal is None:
                    war_participant.war_goal = war_goal_defender
            self._extract_combat_victories(war_dict, war)

    def _extract_combat_victories(self, war_dict, war: models.War):
        battles = war_dict.get("battles", [])
        if not isinstance(battles, list):
            battles = [battles]
        for b_dict in battles:
            if not isinstance(b_dict, dict):
                continue
            battle_attackers = b_dict.get("attackers")
            battle_defenders = b_dict.get("defenders")
            if not battle_attackers or not battle_defenders:
                continue
            if b_dict.get("attacker_victory") not in {"yes", "no"}:
                continue
            attacker_victory = b_dict.get("attacker_victory") == "yes"

            system_id_in_game = b_dict.get("system")
            system = self._session.query(models.System).filter_by(
                system_id_in_game=system_id_in_game
            ).one_or_none()

            planet_name = ""
            planet = b_dict.get("planet")
            if planet in self._gamestate_dict["planet"]:
                planet_name = self._gamestate_dict["planet"][planet].get("name", "")

            combat_type = models.CombatType.__members__.get(b_dict.get("type"), models.CombatType.other)

            date_str = b_dict.get("date")
            date_in_days = models.date_to_days(date_str)
            if date_in_days < 0:
                date_in_days = self._date_in_days

            attacker_exhaustion = b_dict.get("attacker_war_exhaustion", 0.0)
            defender_exhaustion = b_dict.get("defender_war_exhaustion", 0.0)
            if defender_exhaustion + attacker_exhaustion == 0:
                continue
            combat = self._session.query(models.Combat).filter_by(
                war=war,
                system=system,
                planet=planet_name,
                combat_type=combat_type,
                attacker_victory=attacker_victory,
                attacker_war_exhaustion=attacker_exhaustion,
                defender_war_exhaustion=defender_exhaustion,
            ).order_by(models.Combat.date.desc()).first()

            if combat is not None:
                continue

            combat = models.Combat(
                war=war,
                date=date_in_days,
                attacker_war_exhaustion=attacker_exhaustion,
                defender_war_exhaustion=defender_exhaustion,
                system=system, planet=planet_name,
                combat_type=combat_type,
                attacker_victory=attacker_victory,
            )
            self._session.add(combat)

            for country_id in itertools.chain(battle_attackers, battle_defenders):
                db_country = self._session.query(models.Country).filter_by(country_id_in_game=country_id).one_or_none()
                if db_country is None:
                    logger.warning(f"Could not find country with ID {country_id} when processing battle {b_dict}")
                    continue
                war_participant = self._session.query(models.WarParticipant).filter_by(
                    war=war,
                    country=db_country,
                ).one_or_none()
                if war_participant is None:
                    logger.info(f"Could not find War participant matching country {db_country.country_name} and war {war.name}.")
                    continue
                self._session.add(models.CombatParticipant(
                    combat=combat, war_participant=war_participant, is_attacker=country_id in battle_attackers,
                ))

    def _extract_player_leaders(self, country: models.Country, country_dict):
        player_owned_leaders = country_dict.get("owned_leaders", [])
        if not isinstance(player_owned_leaders, list):  # if there is only one
            player_owned_leaders = [player_owned_leaders]
        logger.info(f"{self._logger_str} Processing Leaders")
        leaders = self._gamestate_dict["leaders"]
        active_leaders = set(player_owned_leaders)
        for leader in self._session.query(models.Leader).filter_by(is_active=True).all():
            if leader.leader_id_in_game not in active_leaders:
                is_inactive = True
            else:
                current_leader_name = self.get_leader_name(leaders.get(leader.leader_id_in_game))
                is_inactive = current_leader_name != leader.leader_name

            if is_inactive:
                leader.is_active = False
                self._session.add(leader)
        for leader_id in player_owned_leaders:
            leader_dict = leaders.get(leader_id)
            if not isinstance(leader_dict, dict):
                continue
            leader = self._session.query(models.Leader).filter_by(game=self.game, leader_id_in_game=leader_id).one_or_none()
            if leader is None:
                leader = self._add_new_leader(country, leader_id, leader_dict)
            leader.last_date = self._date_in_days

    def _add_new_leader(self, country, leader_id, leader_dict):
        if "pre_ruler_class" in leader_dict:
            leader_class = models.LeaderClass.__members__.get(leader_dict.get("pre_ruler_class"), models.LeaderClass.unknown)
        else:
            leader_class = models.LeaderClass.__members__.get(leader_dict.get("class"), models.LeaderClass.unknown)
        leader_gender = models.LeaderGender.__members__.get(leader_dict.get("gender"), models.LeaderGender.other)
        leader_agenda = models.AGENDA_STR_TO_ENUM.get(leader_dict.get("agenda"), models.LeaderAgenda.other)
        leader_name = self.get_leader_name(leader_dict)

        date_hired = min(
            self._date_in_days,
            models.date_to_days(leader_dict.get("date", "10000.01.01")),
            models.date_to_days(leader_dict.get("start", "10000.01.01")),
            models.date_to_days(leader_dict.get("date_added", "10000.01.01")),
        )
        date_born = date_hired - 360 * leader_dict.get("age", 0.0) + random.randint(-15, 15)
        species_id = leader_dict.get("species_index", -1)
        species = self._session.query(models.Species).filter_by(species_id_in_game=species_id).one_or_none()
        leader = models.Leader(
            country=country,
            leader_id_in_game=leader_id,
            leader_class=leader_class,
            leader_name=leader_name,
            leader_agenda=leader_agenda,
            species=species,
            game=self.game,
            gender=leader_gender,
            date_hired=date_hired,
            date_born=date_born,
            is_active=True,
        )
        self._session.add(leader)
        return leader

    def get_leader_name(self, leader_dict):
        first_name = leader_dict['name']['first_name']
        last_name = leader_dict['name'].get('second_name', "")
        leader_name = f"{first_name} {last_name}".strip()
        return leader_name

    def _add_scientist_tech_achievements(self, player_country_dict):
        tech_status_dict = player_country_dict.get("tech_status")
        if not isinstance(tech_status_dict, dict):
            return
        last_researched_techs = player_country_dict.get("tech_status", {}).get("technology", [])
        if len(last_researched_techs) < 10:
            return
        last_researched_techs = last_researched_techs[-10:]
        for tech_name in last_researched_techs:
            if not isinstance(tech_name, str):
                continue
            if tech_name in game_info.PHYSICS_TECHS:
                tech_type = "physics"
            elif tech_name in game_info.SOCIETY_TECHS:
                tech_type = "society"
            elif tech_name in game_info.ENGINEERING_TECHS:
                tech_type = "engineering"
            else:  # Some uncategorized DLC techs, repeatables etc.
                continue
            scientist_id = tech_status_dict.get("leaders", {}).get(tech_type)
            if scientist_id is None:
                continue
            leader = self._session.query(models.Leader).filter_by(leader_id_in_game=scientist_id).one_or_none()
            if leader is None:
                logger.warning(f"Could not find scientist matching leader id {scientist_id}")
                continue
            # check for existing achievement in database:
            matching_achievement = self._session.query(models.LeaderAchievement).filter_by(
                achievement_type=models.LeaderAchievementType.researched_technology,
                achievement_description=tech_name,
            ).one_or_none()
            if matching_achievement is None:
                self._session.add(models.LeaderAchievement(
                    leader=leader,
                    achievement_type=models.LeaderAchievementType.researched_technology,
                    start_date_days=self._date_in_days,  # Todo might be cool to know the start date of the tech
                    end_date_days=self._date_in_days,
                    achievement_description=tech_name,
                ))

    def _add_ruler_leader_achievements(self, player_country_dict):
        ruler = self._get_current_ruler(player_country_dict)
        if ruler is not None:
            self._extract_ruler_was_ruler_achievement(ruler, player_country_dict)
            self._extract_ruler_tradition_achievements(ruler, player_country_dict)
            self._extract_ruler_ascension_achievements(ruler, player_country_dict)
            self._extract_ruler_edict_achievements(ruler, player_country_dict)
            self._extract_ruler_negotiated_peace_achievements_and_settle_matching_wars(ruler, player_country_dict)

    def _get_current_ruler(self, country_dict):
        ruler_id = country_dict.get("ruler", -1)
        if ruler_id < 0:
            logger.warning(f"Could not find leader id for ruler!")
            return None
        leader = self._session.query(models.Leader).filter_by(
            leader_id_in_game=ruler_id, is_active=True
        ).one_or_none()
        if leader is None:
            logger.warning(f"Could not find leader matching leader id {ruler_id}")
        return leader

    def _extract_ruler_was_ruler_achievement(self, leader, player_country_dict):
        most_recent_ruler_achievement = self._session.query(models.LeaderAchievement).order_by(
            models.LeaderAchievement.end_date_days.desc()
        ).filter_by(
            achievement_type=models.LeaderAchievementType.was_ruler,
        ).first()
        if most_recent_ruler_achievement is not None:
            most_recent_ruler_achievement.end_date_days = self._date_in_days - 1
            self._session.add(most_recent_ruler_achievement)
        add_new_achievement = most_recent_ruler_achievement is None or most_recent_ruler_achievement.leader != leader
        if add_new_achievement:  # add the new LeaderAchievement
            start_date = self._date_in_days
            if start_date < 100:
                start_date = 0
            self._session.add(models.LeaderAchievement(
                leader=leader,
                achievement_type=models.LeaderAchievementType.was_ruler,
                start_date_days=start_date,
                end_date_days=self._date_in_days,
                achievement_description=player_country_dict.get("name", ""),
            ))

    def _extract_ruler_tradition_achievements(self, leader, player_country_dict):
        for tradition in player_country_dict.get("traditions", []):
            matching_achievement = self._session.query(models.LeaderAchievement).filter_by(
                achievement_type=models.LeaderAchievementType.embraced_tradition,
                achievement_description=tradition,
            ).one_or_none()
            if matching_achievement is None:
                self._session.add(models.LeaderAchievement(
                    leader=leader,
                    achievement_type=models.LeaderAchievementType.embraced_tradition,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    achievement_description=tradition,
                ))

    def _extract_ruler_ascension_achievements(self, leader, player_country_dict):
        for perk in player_country_dict.get("ascension_perks", []):
            matching_achievement = self._session.query(models.LeaderAchievement).filter_by(
                achievement_type=models.LeaderAchievementType.achieved_ascension,
                achievement_description=perk,
            ).one_or_none()
            if matching_achievement is None:
                self._session.add(models.LeaderAchievement(
                    leader=leader,
                    achievement_type=models.LeaderAchievementType.achieved_ascension,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    achievement_description=perk,
                ))

    def _extract_ruler_edict_achievements(self, ruler, country_dict):
        edict_list = country_dict.get("edicts", [])
        if not isinstance(edict_list, list):
            edict_list = [edict_list]
        for edict in edict_list:
            if not isinstance(edict, dict):
                continue
            edict_name = edict.get("edict")
            expiry_date = models.date_to_days(edict.get("date"))
            matching_achievement = self._session.query(
                models.LeaderAchievement
            ).filter_by(
                achievement_type=models.LeaderAchievementType.passed_edict,
                achievement_description=edict_name,
                end_date_days=expiry_date,
            ).one_or_none()
            if matching_achievement is None:
                self._session.add(models.LeaderAchievement(
                    leader=ruler,
                    achievement_type=models.LeaderAchievementType.passed_edict,
                    achievement_description=edict_name,
                    start_date_days=self._date_in_days,
                    end_date_days=expiry_date,
                ))

    def _extract_ruler_negotiated_peace_achievements_and_settle_matching_wars(self, ruler, player_country_dict):
        logger.info(f"{self._logger_str} Processing Truces")
        truces_dict = player_country_dict.get("truce", {})
        if not truces_dict:
            return
        for truce_id, truce_info in truces_dict.items():
            if not isinstance(truce_info, dict):
                continue
            war_name = truce_info.get("name")
            truce_type = truce_info.get("truce_type", "other")
            if not war_name or truce_type != "war":
                continue  # truce is due to diplomatic agreements or similar
            matching_war = self._session.query(models.War).order_by(models.War.start_date_days.desc()).filter_by(name=war_name).first()
            if matching_war is None:
                logger.warning(f"Could not find war matching truce for {war_name}")
                continue
            end_date = truce_info.get("start_date")
            if end_date:
                end_date_days = models.date_to_days(end_date)
                matching_war.end_date_days = end_date_days
                self._session.add(models.LeaderAchievement(
                    achievement_type=models.LeaderAchievementType.negotiated_peace_treaty,
                    start_date_days=end_date_days,
                    end_date_days=end_date_days,
                    achievement_description=war_name,
                    leader=ruler,
                ))
            if matching_war.outcome == models.WarOutcome.in_progress:
                if matching_war.attacker_war_exhaustion < matching_war.defender_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.attacker_victory
                elif matching_war.defender_war_exhaustion < matching_war.attacker_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.defender_victory
                else:
                    matching_war.outcome = models.WarOutcome.status_quo

    def _add_governor_achievements(self, player_country_dict):
        sector_dict = player_country_dict.get("sectors", [])
        for sector_id, sector_info in sector_dict.items():
            governor_id = sector_info.get("leader")
            if governor_id is None:
                continue  # sector has no governor
            governor = self._session.query(models.Leader).filter_by(
                leader_id_in_game=governor_id,
            ).one_or_none()
            if governor is None:
                logger.warning(f"Could not find governor matching (in-game) ID {governor_id}")
                continue
            self._add_governor_ruled_sector_achievements(sector_info, governor)
            self._add_governor_planetary_achievements(sector_info, governor)

    def _add_governor_ruled_sector_achievements(self, sector_info, governor):
        sector_name = sector_info.get("name", "Unnamed Sector")
        achievement = self._session.query(models.LeaderAchievement).filter_by(
            achievement_type=models.LeaderAchievementType.governed_sector,
            achievement_description=sector_name,
        ).order_by(models.LeaderAchievement.end_date_days.desc()).first()
        add_new_achievement = False
        if achievement is None:
            add_new_achievement = True
        else:
            achievement.end_date_days = self._date_in_days - 1
            self._session.add(achievement)
            if achievement.leader != governor:
                add_new_achievement = True
        if add_new_achievement:
            self._session.add(models.LeaderAchievement(
                leader=governor,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days + 1,
                achievement_type=models.LeaderAchievementType.governed_sector,
                achievement_description=sector_name,
            ))

    def _add_governor_planetary_achievements(self, sector_info, governor):
        for system_id in sector_info.get("galactic_object", []):
            planets = self._gamestate_dict.get("galactic_object").get(system_id).get("planet", [])
            if not isinstance(planets, list):
                planets = [planets]
            for planet_id in planets:
                planet_dict = self._gamestate_dict.get("planet", {}).get(planet_id)
                self._add_historical_event_colonization(system_id, planet_id, planet_dict, governor)
                self._add_governor_megastructure_achievement(system_id, planet_dict, governor)
                self._add_governor_colonization_achievement(system_id, planet_dict, governor)

    # TODO Currently, this function only handles planet-like megastructures. Sentry array, science nexus etc might be handled as stations?
    def _add_governor_megastructure_achievement(self, system_id, planet_dict, governor):
        pc = planet_dict.get("planet_class")
        p_name = planet_dict.get("name")
        if pc not in {"pc_habitat", "pc_ringworld_habitable"}:
            return
        if pc == "pc_ringworld_habitable":
            sys_name = self._gamestate_dict["galactic_object"].get(system_id).get("name", "Unknown system")
            p_name = f"{sys_name} Ringworld"
        a = self._session.query(models.LeaderAchievement).filter_by(
            leader=governor,
            achievement_type=models.LeaderAchievementType.built_megastructure,
            achievement_description=p_name,
        ).one_or_none()
        if a is None:
            self._session.add(models.LeaderAchievement(
                leader=governor,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                achievement_type=models.LeaderAchievementType.built_megastructure,
                achievement_description=p_name,
            ))

    def _add_historical_event_colonization(self, system_id, planet_id, planet_dict, governor):
        p_name = planet_dict.get("name")

        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.planet_colonization,
            achievement_description=p_name,
        ).one_or_none()
        if event is None:
            system_model = self._session.query(models.System).filter_by(
                system_id_in_game=system_id
            ).one_or_none()
            planet_model = self._session.query(models.ColonizedPlanet).filter_by(
                system=system_model,
                planet_id_in_game=planet_id
            ).one_or_none()
            if system_model is None or planet_model is None:
                raise ValueError("Make sure to add all systems & colonized planets before historical events are processed!")
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.planet_colonization,
                leader=governor,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                planet=planet_model,
            )
            self._session.add(event)
        elif "colonizer_pop" in planet_dict:
            event.end_date_days = self._date_in_days
        if "colonize_date" in planet_dict:
            colonize_date = models.date_to_days(planet_dict["colonize_date"])
            event.end_date_days = colonize_date
        self._session.add(event)

    def _add_governor_colonization_achievement(self, system_id, planet_dict, governor):
        p_name = planet_dict.get("name")
        if "colonizer_pop" in planet_dict:
            a = self._session.query(models.LeaderAchievement).filter_by(
                achievement_type=models.LeaderAchievementType.colonized_planet,
                achievement_description=p_name,
            ).one_or_none()
            if a is None:
                self._session.add(models.LeaderAchievement(
                    leader=governor,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    achievement_type=models.LeaderAchievementType.colonized_planet,
                    achievement_description=p_name,
                ))
            else:
                a.end_date_days = self._date_in_days
                self._session.add(a)
        elif "colonize_date" in planet_dict:
            colonize_date = models.date_to_days(planet_dict["colonize_date"])
            a = self._session.query(models.LeaderAchievement).filter_by(
                achievement_type=models.LeaderAchievementType.colonized_planet,
                achievement_description=p_name,
            ).one_or_none()
            if a is None:
                a = models.LeaderAchievement(
                    leader=governor,
                    start_date_days=colonize_date,
                    end_date_days=colonize_date,
                    achievement_type=models.LeaderAchievementType.colonized_planet,
                    achievement_description=p_name,
                )
            else:
                a.end_date_days = colonize_date
            self._session.add(a)

    def _add_faction_leader_achievement(self, faction_dict):
        faction_leader_id = faction_dict.get("leader", -1)
        if faction_leader_id < 0:
            return
        leader = self._session.query(models.Leader).filter_by(leader_id_in_game=faction_leader_id).one_or_none()
        if leader is None:
            logger.warning(f"Could not find leader matching leader id {faction_leader_id}")
            return
        matching_achievement = self._session.query(models.LeaderAchievement).filter_by(
            leader=leader, achievement_type=models.LeaderAchievementType.was_faction_leader,
        ).one_or_none()
        if matching_achievement is not None:
            matching_achievement.end_date_days = self._date_in_days
        else:  # add the new LeaderAchievement
            matching_achievement = models.LeaderAchievement(
                leader=leader,
                achievement_type=models.LeaderAchievementType.was_faction_leader,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                achievement_description=faction_dict.get("name")
            )
            self._session.add(matching_achievement)

    def _extract_system_ownership(self):
        logger.info(f"{self._logger_str} Processing system ownership")
        start = time.clock()
        starbases = self._gamestate_dict.get("starbases", {})
        if not isinstance(starbases, dict):
            return
        for starbase_dict in starbases.values():
            if not isinstance(starbase_dict, dict):
                continue
            country_id_in_game = starbase_dict.get("owner")
            system_id_in_game = starbase_dict.get("system")
            if system_id_in_game is None or country_id_in_game is None:
                continue
            system = self._session.query(models.System).filter_by(system_id_in_game=system_id_in_game).one_or_none()
            country = self._session.query(models.Country).filter_by(country_id_in_game=country_id_in_game).one_or_none()
            if system is None or country is None:
                logger.warning(f"Cannot establish ownership for system {system_id_in_game} and country {country_id_in_game}")
                continue
            ownership = self._session.query(models.SystemOwnership).filter_by(
                system=system
            ).order_by(models.SystemOwnership.end_date_days.desc()).first()
            if ownership is not None:
                ownership.end_date_days = self._date_in_days
                self._session.add(ownership)
            if ownership is None or ownership.country != country:
                ownership = models.SystemOwnership(
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days + 1,
                    country=country,
                    system=system,
                )
                self._session.add(ownership)
        logger.info(f"{self._logger_str} Processed system ownership in {time.clock()-start}s")

    def _initialize_enclave_trade_info(self):
        trade_level_1 = [10, 20]
        trade_level_2 = [25, 50]
        trade_level_3 = [50, 100]

        trade_energy_for_minerals = ["mineral_income_enclaves", "energy_spending_enclaves"]
        trade_food_for_minerals = ["mineral_income_enclaves", "food_spending_enclaves"]
        trade_minerals_for_energy = ["energy_income_enclaves", "mineral_spending_enclaves"]
        trade_food_for_energy = ["energy_income_enclaves", "food_spending_enclaves"]
        trade_minerals_for_food = ["food_income_enclaves", "mineral_spending_enclaves"]
        trade_energy_for_food = ["food_income_enclaves", "energy_spending_enclaves"]

        self._enclave_trade_modifiers = {
            "enclave_mineral_trade_1_mut": dict(zip(trade_energy_for_minerals, trade_level_1)),
            "enclave_mineral_trade_1_rig": dict(zip(trade_energy_for_minerals, trade_level_1)),
            "enclave_mineral_trade_1_xur": dict(zip(trade_energy_for_minerals, trade_level_1)),
            "enclave_mineral_trade_2_mut": dict(zip(trade_energy_for_minerals, trade_level_2)),
            "enclave_mineral_trade_2_rig": dict(zip(trade_energy_for_minerals, trade_level_2)),
            "enclave_mineral_trade_2_xur": dict(zip(trade_energy_for_minerals, trade_level_2)),
            "enclave_mineral_trade_3_mut": dict(zip(trade_energy_for_minerals, trade_level_3)),
            "enclave_mineral_trade_3_rig": dict(zip(trade_energy_for_minerals, trade_level_3)),
            "enclave_mineral_trade_3_xur": dict(zip(trade_energy_for_minerals, trade_level_3)),
            "enclave_mineral_food_trade_1_mut": dict(zip(trade_food_for_minerals, trade_level_1)),
            "enclave_mineral_food_trade_1_rig": dict(zip(trade_food_for_minerals, trade_level_1)),
            "enclave_mineral_food_trade_1_xur": dict(zip(trade_food_for_minerals, trade_level_1)),
            "enclave_mineral_food_trade_2_mut": dict(zip(trade_food_for_minerals, trade_level_2)),
            "enclave_mineral_food_trade_2_rig": dict(zip(trade_food_for_minerals, trade_level_2)),
            "enclave_mineral_food_trade_2_xur": dict(zip(trade_food_for_minerals, trade_level_2)),
            "enclave_mineral_food_trade_3_mut": dict(zip(trade_food_for_minerals, trade_level_3)),
            "enclave_mineral_food_trade_3_rig": dict(zip(trade_food_for_minerals, trade_level_3)),
            "enclave_mineral_food_trade_3_xur": dict(zip(trade_food_for_minerals, trade_level_3)),
            "enclave_energy_trade_1_mut": dict(zip(trade_minerals_for_energy, trade_level_1)),
            "enclave_energy_trade_1_rig": dict(zip(trade_minerals_for_energy, trade_level_1)),
            "enclave_energy_trade_1_xur": dict(zip(trade_minerals_for_energy, trade_level_1)),
            "enclave_energy_trade_2_mut": dict(zip(trade_minerals_for_energy, trade_level_2)),
            "enclave_energy_trade_2_rig": dict(zip(trade_minerals_for_energy, trade_level_2)),
            "enclave_energy_trade_2_xur": dict(zip(trade_minerals_for_energy, trade_level_2)),
            "enclave_energy_trade_3_mut": dict(zip(trade_minerals_for_energy, trade_level_3)),
            "enclave_energy_trade_3_rig": dict(zip(trade_minerals_for_energy, trade_level_3)),
            "enclave_energy_trade_3_xur": dict(zip(trade_minerals_for_energy, trade_level_3)),
            "enclave_energy_food_trade_1_mut": dict(zip(trade_food_for_energy, trade_level_1)),
            "enclave_energy_food_trade_1_rig": dict(zip(trade_food_for_energy, trade_level_1)),
            "enclave_energy_food_trade_1_xur": dict(zip(trade_food_for_energy, trade_level_1)),
            "enclave_energy_food_trade_2_mut": dict(zip(trade_food_for_energy, trade_level_2)),
            "enclave_energy_food_trade_2_rig": dict(zip(trade_food_for_energy, trade_level_2)),
            "enclave_energy_food_trade_2_xur": dict(zip(trade_food_for_energy, trade_level_2)),
            "enclave_energy_food_trade_3_mut": dict(zip(trade_food_for_energy, trade_level_3)),
            "enclave_energy_food_trade_3_rig": dict(zip(trade_food_for_energy, trade_level_3)),
            "enclave_energy_food_trade_3_xur": dict(zip(trade_food_for_energy, trade_level_3)),
            "enclave_food_minerals_trade_1_mut": dict(zip(trade_minerals_for_food, trade_level_1)),
            "enclave_food_minerals_trade_1_rig": dict(zip(trade_minerals_for_food, trade_level_1)),
            "enclave_food_minerals_trade_1_xur": dict(zip(trade_minerals_for_food, trade_level_1)),
            "enclave_food_minerals_trade_2_mut": dict(zip(trade_minerals_for_food, trade_level_2)),
            "enclave_food_minerals_trade_2_rig": dict(zip(trade_minerals_for_food, trade_level_2)),
            "enclave_food_minerals_trade_2_xur": dict(zip(trade_minerals_for_food, trade_level_2)),
            "enclave_food_minerals_trade_3_mut": dict(zip(trade_minerals_for_food, trade_level_3)),
            "enclave_food_minerals_trade_3_rig": dict(zip(trade_minerals_for_food, trade_level_3)),
            "enclave_food_minerals_trade_3_xur": dict(zip(trade_minerals_for_food, trade_level_3)),
            "enclave_food_energy_trade_1_mut": dict(zip(trade_energy_for_food, trade_level_1)),
            "enclave_food_energy_trade_1_rig": dict(zip(trade_energy_for_food, trade_level_1)),
            "enclave_food_energy_trade_1_xur": dict(zip(trade_energy_for_food, trade_level_1)),
            "enclave_food_energy_trade_2_mut": dict(zip(trade_energy_for_food, trade_level_2)),
            "enclave_food_energy_trade_2_rig": dict(zip(trade_energy_for_food, trade_level_2)),
            "enclave_food_energy_trade_2_xur": dict(zip(trade_energy_for_food, trade_level_2)),
            "enclave_food_energy_trade_3_mut": dict(zip(trade_energy_for_food, trade_level_3)),
            "enclave_food_energy_trade_3_rig": dict(zip(trade_energy_for_food, trade_level_3)),
            "enclave_food_energy_trade_3_xur": dict(zip(trade_energy_for_food, trade_level_3)),
        }

    def _reset_state(self):
        logger.info(f"{self._logger_str} Resetting timeline state")
        self._country_starbases_dict = None
        self._current_gamestate = None
        self._gamestate_dict = None
        self._player_country = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None
        self._session = None
        self._logger_str = None
        self._date_in_days = None
