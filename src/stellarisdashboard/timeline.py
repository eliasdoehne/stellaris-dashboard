import itertools
import logging
import random
import time
from typing import Dict, Any, Tuple, Union

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

        self.random_instance = random.Random()

        self._new_models = []
        self._enclave_trade_modifiers = None
        self._initialize_enclave_trade_info()

    def process_gamestate(self, game_name: str, gamestate_dict: Dict[str, Any]):
        self.random_instance.seed(game_name)
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
                self._reset_state()

    def _extract_galaxy_data(self):
        logger.info(f"{self._logger_str} Extracting galaxy data...")
        for system_id_in_game in self._gamestate_dict.get("galactic_object", {}):
            self._add_single_system(system_id_in_game)

    def _add_single_system(self, system_id: int, country_model: models.Country = None) -> Union[models.System, None]:
        """ This is separated into a method since it is occasionally necessary to add individual systems after the initial scan of the galaxy. """
        system_data = self._gamestate_dict.get("galactic_object", {}).get(system_id)
        if system_data is None:
            logger.warn(f"{self._logger_str} Found no data for system with ID {system_id}!")
            return
        original_system_name = system_data.get("name")
        coordinate_x = system_data.get("coordinate", {}).get("x", 0)
        coordinate_y = system_data.get("coordinate", {}).get("y", 0)
        system_model = models.System(
            game=self.game,
            system_id_in_game=system_id,
            star_class=system_data.get("star_class"),
            original_name=original_system_name,
            coordinate_x=coordinate_x,
            coordinate_y=coordinate_y,
        )
        self._session.add(system_model)
        if country_model is not None:
            self._session.add(models.HistoricalEvent(
                event_type=models.HistoricalEventType.discovered_new_system,
                system=system_model,
                country=country_model,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                is_known_to_player=True,  # galactic map is always visible
            ))

        for hl_data in system_data.get("hyperlane", []):
            neighbor_id = hl_data.get("to")
            if neighbor_id == system_id:
                continue  # This can happen in Stellaris 2.1
            neighbor_model = self._session.query(models.System).filter_by(
                system_id_in_game=neighbor_id
            ).one_or_none()
            if neighbor_model is None:
                continue  # assume that the hyperlane will be created when adding the neighbor system to DB later

            self._session.add(models.HyperLane(
                system_one=system_model,
                system_two=neighbor_model,
            ))
        return system_model

    def _process_gamestate(self):
        self._extract_player_trade_agreements()
        self._count_starbases()
        player_economy = self._extract_player_economy(self._gamestate_dict["country"][self._player_country])
        self._current_gamestate = models.GameState(
            game=self.game, date=self._date_in_days,
            **player_economy
        )
        self._session.add(self._current_gamestate)

        for country_id, country_data_dict in self._gamestate_dict["country"].items():
            if not isinstance(country_data_dict, dict):
                continue

            country_model = self._session.query(models.Country).filter_by(
                game=self.game,
                country_id_in_game=country_id
            ).one_or_none()
            country_type = country_data_dict.get("type")
            if country_model is None:
                country_model = models.Country(
                    is_player=(country_id == self._player_country),
                    country_id_in_game=country_id,
                    game=self.game,
                    country_type=country_type,
                    country_name=country_data_dict.get("name", "no name")
                )
                if country_id == self._player_country:
                    country_model.first_player_contact_date = 0
                self._session.add(country_model)
            self._extract_country_leaders(country_model, country_data_dict)

            if country_type not in {"default", "fallen_empire", "awakened_fallen_empire"}:  # TODO consider adding more types,  "ruined_marauders", "dormant_marauders", "awakened_marauders"
                continue  # Enclaves, Leviathans, etc ....

            diplomacy_data = self._process_diplomacy(country_model, country_data_dict)
            if country_model.first_player_contact_date is None and diplomacy_data.get("has_communications_with_player"):
                country_model.first_player_contact_date = self._date_in_days
                self._session.add(country_model)

            self._extract_country_government(country_model, country_data_dict)

            country_data_model = self._extract_country_data(country_id, country_model, country_data_dict, diplomacy_data)
            if country_data_model.attitude_towards_player.is_known():
                debug_name = country_data_dict.get('name', 'Unnamed Country')
                logger.info(f"{self._logger_str} Extracting country info: {debug_name}")

            if config.CONFIG.only_read_player_history and not country_model.is_player:
                continue
            self._history_process_planet_and_sector_events(country_model, country_data_dict)
            self._extract_pop_info_from_planets(country_data_dict, country_data_model)
            self._extract_factions_and_faction_leaders(country_model, country_data_model)
            self._history_add_ruler_events(country_model, country_data_dict)
            self._history_add_tech_events(country_model, country_data_dict)

        self._extract_wars()
        self._settle_finished_wars()
        if config.CONFIG.extract_system_ownership:
            self._extract_system_ownership()

    def _extract_country_government(self, country_model: models.Country, country_dict):
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

        prev_gov = self._session.query(models.Government).filter(
            models.Government.start_date_days <= self._date_in_days,
            models.Government.end_date_days <= self._date_in_days,
        ).filter_by(
            country=country_model,
        ).order_by(
            models.Government.start_date_days.desc()
        ).first()

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
            personality=country_dict.get("personality", "unknown_personality"),
            **ethics,
            **civics,
        )
        self._session.add(gov)
        if gov_was_reformed:
            ruler = self._get_current_ruler(country_dict)
            self._session.add(models.HistoricalEvent(
                event_type=models.HistoricalEventType.government_reform,
                country=country_model,
                leader=ruler,  # might be none, but probably very unlikely (?)
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                is_known_to_player=country_model.is_known_to_player(),
            ))

    def _count_starbases(self):
        self._country_starbases_dict = {}
        for starbase_dict in self._gamestate_dict.get("starbases", {}).values():
            if not isinstance(starbase_dict, dict):
                continue
            system_id_in_game = starbase_dict.get("system")
            if system_id_in_game is None:
                continue
            owner_id = starbase_dict.get("owner")
            if owner_id is None:
                continue
            if owner_id not in self._country_starbases_dict:
                self._country_starbases_dict[owner_id] = set()
            self._country_starbases_dict[owner_id].add(system_id_in_game)

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
            date=self._date_in_days,
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

    def _process_diplomacy(self, country_model: models.Country, country_dict):
        relations_manager = country_dict.get("relations_manager", [])
        diplomacy_towards_player = dict(
            has_rivalry_with_player=False,
            has_defensive_pact_with_player=False,
            has_federation_with_player=False,
            has_non_aggression_pact_with_player=False,
            has_closed_borders_with_player=False,
            has_communications_with_player=False,
            has_migration_treaty_with_player=False,
        )
        if not isinstance(relations_manager, dict):
            return diplomacy_towards_player
        relation_list = relations_manager.get("relation", [])
        if not isinstance(relation_list, list):  # if there is only one
            relation_list = [relation_list]
        for relation in relation_list:
            if not isinstance(relation, dict):
                continue

            if not config.CONFIG.only_read_player_history or country_model.is_player:
                self._history_add_or_update_diplomatic_events(country_model, country_dict, relation)

            if relation.get("country") == self._player_country:
                diplomacy_towards_player.update(
                    has_rivalry_with_player=relation.get("is_rival") == "yes",
                    has_defensive_pact_with_player=relation.get("defensive_pact") == "yes",
                    has_federation_with_player=relation.get("alliance") == "yes",
                    has_non_aggression_pact_with_player=relation.get("non_aggression_pledge") == "yes",
                    has_closed_borders_with_player=relation.get("closed_borders") == "yes",
                    has_communications_with_player=relation.get("communications") == "yes",
                    has_migration_treaty_with_player=relation.get("migration_access") == "yes",
                )
            break
        return diplomacy_towards_player

    def _history_add_or_update_diplomatic_events(self, country_model: models.Country, country_dict, relation):
        relation_country = relation.get("country")
        target_country_model = self._session.query(models.Country).filter_by(
            country_id_in_game=relation_country,
        ).one_or_none()
        if target_country_model is None:
            return  # target country might not be in DB yet if this is the first save...
        ruler = self._get_current_ruler(country_dict)
        tc_ruler = self._get_current_ruler(self._gamestate_dict["country"].get(target_country_model.country_id_in_game))

        is_known_to_player = country_model.is_known_to_player() and target_country_model.is_known_to_player()
        for target_event_type, relation_status in [
            (models.HistoricalEventType.rivalry_declaration, relation.get("is_rival") == "yes"),
            (models.HistoricalEventType.closed_borders, relation.get("closed_borders") == "yes"),
            (models.HistoricalEventType.defensive_pact, relation.get("defensive_pact") == "yes"),
            (models.HistoricalEventType.formed_federation, relation.get("alliance") == "yes"),
            (models.HistoricalEventType.non_aggression_pact, relation.get("non_aggression_pledge") == "yes"),
            (models.HistoricalEventType.first_contact, relation.get("communications") == "yes"),
        ]:
            if relation_status:
                for (c, tc, r) in [(country_model, target_country_model, ruler),
                                   (target_country_model, country_model, tc_ruler)]:
                    matching_event = self._session.query(models.HistoricalEvent).filter_by(
                        event_type=target_event_type,
                        country=c,
                        target_country=tc,
                    ).order_by(models.HistoricalEvent.start_date_days.desc()).first()

                    if matching_event is None or matching_event.end_date_days < self._date_in_days - 5 * 360:
                        matching_event = models.HistoricalEvent(
                            event_type=target_event_type,
                            country=c,
                            target_country=tc,
                            leader=r,
                            start_date_days=self._date_in_days,
                            end_date_days=self._date_in_days,
                            is_known_to_player=is_known_to_player,
                        )
                    else:
                        matching_event.end_date_days = self._date_in_days
                        matching_event.is_known_to_player = is_known_to_player
                    self._session.add(matching_event)

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

    # TODO Extract research and sensor link agreements between all countries for HistoricalEvents
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

    def _extract_factions_and_faction_leaders(self, country_model: models.Country, country_data: models.CountryData):
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
            faction_model = self._add_faction_and_faction_support(
                faction_id_in_game=faction_id,
                faction_name=faction_name,
                country_data=country_data,
                members=members,
                support=faction_dict.get("support", 0),
                happiness=faction_dict.get("happiness", 0),
                ethics=models.PopEthics.from_str(faction_dict.get("type")),
            )
            self._history_add_or_update_faction_leader_event(country_model, faction_model, faction_dict)

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
        country_model = country_data.country
        faction = self._session.query(models.PoliticalFaction).filter_by(
            faction_id_in_game=faction_id_in_game,
            country=country_model,
        ).one_or_none()
        if faction is None:
            faction = models.PoliticalFaction(
                country=country_model,
                faction_name=faction_name,
                faction_id_in_game=faction_id_in_game,
                ethics=ethics,
            )
            self._session.add(faction)
            if faction_id_in_game not in TimelineExtractor.NO_FACTION_ID_MAP.values():
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.new_faction,
                    country=country_model,
                    faction=faction,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    is_known_to_player=(country_model.is_known_to_player()
                                        and country_data is not None
                                        and country_data.attitude_towards_player.reveals_demographic_info()),
                ))
        faction_support_model = models.FactionSupport(
            faction=faction,
            country_data=country_data,
            members=members,
            support=support,
            happiness=happiness,
        )
        self._session.add(faction_support_model)
        return faction

    def _extract_pop_info_from_planets(self, country_dict: Dict[str, Any], country_data: models.CountryData):
        self._factionless_pops = {
            TimelineExtractor.SLAVE_FACTION_NAME: 0,
            TimelineExtractor.PURGE_FACTION_NAME: 0,
            TimelineExtractor.NON_SENTIENT_ROBOT_NO_FACTION: 0,
        }
        species_demographics = {}
        pop_data = self._gamestate_dict["pop"]

        for planet_id in country_dict.get("owned_planets", []):
            for pop_id in self._gamestate_dict.get("planet", {}).get(planet_id, {}).get("pop", []):
                if pop_id is None:
                    continue
                elif pop_id not in pop_data:
                    logger.warning(f"{self._logger_str} Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                this_pop = pop_data[pop_id]
                if not isinstance(this_pop, dict):
                    continue  # might be "none"
                if this_pop["growth_state"] not in ["grown", 1]:
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
            pop_count = models.PopCount(
                country_data=country_data,
                species=self._get_or_add_species(species_id),
                pop_count=pop_count,
            )
            self._session.add(pop_count)

    def _get_or_add_species(self, species_id_in_game: int):
        species_data = self._gamestate_dict["species"][species_id_in_game]
        species_name = species_data["name"]
        species = self._session.query(models.Species).filter_by(
            game=self.game, species_id_in_game=species_id_in_game
        ).one_or_none()
        if species is None:
            species = models.Species(
                game=self.game,
                species_name=species_name,
                species_id_in_game=species_id_in_game,
                is_robotic=species_data["class"] == "ROBOT",
                parent_species_id_in_game=species_data.get("base", -1),
            )
            self._session.add(species)
        return species

    def _extract_wars(self):
        logger.info(f"{self._logger_str} Processing Wars")
        wars_dict = self._gamestate_dict.get("war", {})
        if not wars_dict:
            return

        for war_id, war_dict in wars_dict.items():
            if not isinstance(war_dict, dict):
                continue
            war_name = war_dict.get("name", "Unnamed war")
            war_model = self._session.query(models.War).order_by(models.War.end_date_days.desc()).filter_by(
                game=self.game, name=war_name
            ).first()
            if war_model is None or (war_model.outcome != models.WarOutcome.in_progress
                                     and war_model.end_date_days < self._date_in_days - 5 * 360):
                start_date_days = models.date_to_days(war_dict["start_date"])
                war_model = models.War(
                    war_id_in_game=war_id,
                    game=self.game,
                    start_date_days=start_date_days,
                    end_date_days=self._date_in_days,
                    name=war_name,
                    outcome=models.WarOutcome.in_progress,
                )
            elif war_dict.get("defender_force_peace") == "yes":
                war_model.outcome = models.WarOutcome.status_quo
                war_model.end_date_days = models.date_to_days(war_dict.get("defender_force_peace_date"))
            elif war_dict.get("attacker_force_peace") == "yes":
                war_model.outcome = models.WarOutcome.status_quo
                war_model.end_date_days = models.date_to_days(war_dict.get("attacker_force_peace_date"))
            elif war_model.outcome != models.WarOutcome.in_progress:
                continue
            else:
                war_model.end_date_days = self._date_in_days
            self._session.add(war_model)
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

                country_dict = self._gamestate_dict["country"][country_id]
                if db_country is None:
                    country_name = country_dict["name"]
                    logger.warning(f"Could not find country matching war participant {country_name}")
                    continue

                is_attacker = country_id in attackers

                war_participant = self._session.query(models.WarParticipant).filter_by(
                    war=war_model, country=db_country
                ).one_or_none()
                if war_participant is None:
                    war_goal = war_goal_attacker if is_attacker else war_goal_defender
                    war_participant = models.WarParticipant(
                        war=war_model,
                        war_goal=models.WarGoal.__members__.get(war_goal, models.WarGoal.wg_other),
                        country=db_country,
                        is_attacker=is_attacker,
                    )
                    self._session.add(models.HistoricalEvent(
                        event_type=models.HistoricalEventType.war,
                        country=war_participant.country,
                        leader=self._get_current_ruler(country_dict),
                        start_date_days=self._date_in_days,
                        end_date_days=self._date_in_days,
                        war=war_model,
                        is_known_to_player=war_participant.country.is_known_to_player(),
                    ))
                if war_participant.war_goal is None:
                    war_participant.war_goal = war_goal_defender
                self._session.add(war_participant)

            self._extract_combat_victories(war_dict, war_model)

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

            planet_model = self._session.query(models.Planet).filter_by(
                planet_id_in_game=b_dict.get("planet"),
            ).one_or_none()

            if planet_model is None:
                system_id_in_game = b_dict.get("system")
                system = self._session.query(models.System).filter_by(
                    system_id_in_game=system_id_in_game
                ).one_or_none()
                if system is None:
                    system = self._add_single_system(system_id_in_game)
            else:
                system = planet_model.system

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
                system=system if system is not None else planet_model.system,
                planet=planet_model,
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
                system=system,
                planet=planet_model,
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

    def _extract_country_leaders(self, country_model: models.Country, country_dict):
        owned_leaders = country_dict.get("owned_leaders", [])
        if not isinstance(owned_leaders, list):  # if there is only one
            owned_leaders = [owned_leaders]
        leaders = self._gamestate_dict["leaders"]
        active_leaders = set(owned_leaders)

        # first, check if the known leaders in the DB are still there
        for leader in self._session.query(models.Leader).filter_by(
                country=country_model,
                is_active=True,
        ).all():
            if leader.leader_id_in_game not in active_leaders:
                leader.is_active = False
            else:
                current_leader_name = self.get_leader_name(leaders.get(leader.leader_id_in_game))
                leader.is_active = (current_leader_name == leader.leader_name
                                    or leader.last_date >= self._date_in_days - 3 * 360)
            if not leader.is_active:
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.leader_died,
                    country=country_model,
                    leader=leader,
                    start_date_days=leader.last_date,
                    end_date_days=leader.last_date,
                    is_known_to_player=(country_data is not None
                                        and country_data.attitude_towards_player.reveals_economy_info()),
                ))
            self._session.add(leader)

        # then, check if
        for leader_id in owned_leaders:
            leader_dict = leaders.get(leader_id)
            if not isinstance(leader_dict, dict):
                continue
            leader = self._session.query(models.Leader).filter_by(game=self.game, leader_id_in_game=leader_id).one_or_none()
            if leader is None:
                leader = self._add_new_leader(country_model, leader_id, leader_dict)
            leader.is_active = True
            leader.last_date = self._date_in_days
            self._session.add(leader)

    def _add_new_leader(self, country_model: models.Country, leader_id: int, leader_dict) -> models.Leader:
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
        date_born = date_hired - 360 * leader_dict.get("age", 0.0) + self.random_instance.randint(-15, 15)
        species_id = leader_dict.get("species_index", -1)
        species = self._get_or_add_species(species_id)
        leader = models.Leader(
            country=country_model,
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
        country_data = country_model.get_most_recent_data()
        self._session.add(models.HistoricalEvent(
            event_type=models.HistoricalEventType.leader_recruited,
            country=country_model,
            leader=leader,
            start_date_days=date_hired,
            end_date_days=self._date_in_days,
            is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
        ))
        return leader

    def get_leader_name(self, leader_dict):
        first_name = leader_dict['name']['first_name']
        last_name = leader_dict['name'].get('second_name', "")
        leader_name = f"{first_name} {last_name}".strip()
        return leader_name

    def _history_add_tech_events(self, country_model: models.Country, country_dict):
        tech_status_dict = country_dict.get("tech_status")
        if not isinstance(tech_status_dict, dict):
            return
        last_researched_techs = country_dict.get("tech_status", {}).get("technology", [])
        if len(last_researched_techs) < 10:
            return
        last_researched_techs = last_researched_techs[-26:]
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
            matching_description = self._get_or_add_shared_description(text=tech_name)
            # check for existing event in database:
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                event_type=models.HistoricalEventType.researched_technology,
                country=country_model,
                description=matching_description,
            ).one_or_none()
            if matching_event is None:
                scientist_id = tech_status_dict.get("leaders", {}).get(tech_type)
                leader = self._session.query(models.Leader).filter_by(leader_id_in_game=scientist_id).one_or_none()
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.researched_technology,
                    country=country_model,
                    leader=leader,
                    start_date_days=self._date_in_days,  # Todo might be cool to know the start date of the tech
                    end_date_days=self._date_in_days,
                    description=matching_description,
                    is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_technology_info(),
                ))

    def _history_add_ruler_events(self, country_model: models.Country, country_dict):
        ruler = self._get_current_ruler(country_dict)
        if ruler is not None:
            capital_id = country_dict.get("capital")
            capital = None
            if capital_id is not None:
                capital = self._session.query(models.Planet).filter_by(
                    planet_id_in_game=capital_id
                ).one_or_none()

            self._history_add_or_update_ruler(ruler, country_model, capital)
            self._history_extract_tradition_events(ruler, country_model, country_dict)
            self._history_extract_ascension_events(ruler, country_model, country_dict)
            self._history_extract_edict_events(ruler, country_model, country_dict)

    def _get_current_ruler(self, country_dict) -> Union[models.Leader, None]:
        if not isinstance(country_dict, dict):
            return None
        ruler_id = country_dict.get("ruler", -1)
        if ruler_id < 0:
            logger.warning(f"Could not find leader id for ruler!")
            return None
        leader = self._session.query(models.Leader).filter_by(
            leader_id_in_game=ruler_id,
            is_active=True,
        ).order_by(models.Leader.is_active.desc()).first()
        if leader is None:
            logger.warning(f"Could not find leader matching leader id {ruler_id} for country {country_dict.get('name')}")
        return leader

    def _history_add_or_update_ruler(self, ruler: models.Leader, country_model: models.Country, capital: models.Planet):
        most_recent_ruler_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.ruled_empire,
            country=country_model,
            leader=ruler,
        ).order_by(
            models.HistoricalEvent.start_date_days.desc()
        ).first()
        capital_system = capital.system if capital is not None else None
        if most_recent_ruler_event is None:
            start_date = self._date_in_days
            if start_date < 100:
                start_date = 0
            most_recent_ruler_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.ruled_empire,
                country=country_model,
                leader=ruler,
                start_date_days=start_date,
                planet=capital,
                system=capital_system,
                end_date_days=self._date_in_days,
                is_known_to_player=country_model.is_known_to_player(),
            )
        else:
            most_recent_ruler_event.end_date_days = self._date_in_days - 1
            most_recent_ruler_event.is_known_to_player = country_model.is_known_to_player()
        if most_recent_ruler_event.planet is None:
            most_recent_ruler_event.planet = capital
            most_recent_ruler_event.system = capital.system
        elif most_recent_ruler_event.planet != capital:
            self._session.add(models.HistoricalEvent(
                event_type=models.HistoricalEventType.capital_relocation,
                country=country_model,
                leader=ruler,
                start_date_days=most_recent_ruler_event.end_date_days,
                planet=capital,
                system=capital_system,
                end_date_days=self._date_in_days,
                is_known_to_player=country_model.is_known_to_player(),
            ))
        self._session.add(most_recent_ruler_event)

    def _history_extract_tradition_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        for tradition in country_dict.get("traditions", []):
            matching_description = self._get_or_add_shared_description(text=tradition)
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                country=country_model,
                event_type=models.HistoricalEventType.tradition,
                description=matching_description,
            ).one_or_none()
            if matching_event is None:
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    leader=ruler,
                    country=country_model,
                    event_type=models.HistoricalEventType.tradition,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    description=matching_description,
                    is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
                ))

    def _history_extract_ascension_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        for ascension_perk in country_dict.get("ascension_perks", []):
            matching_description = self._get_or_add_shared_description(text=ascension_perk)
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                country=country_model,
                event_type=models.HistoricalEventType.ascension_perk,
                description=matching_description,
            ).one_or_none()
            if matching_event is None:
                self._session.add(models.HistoricalEvent(
                    leader=ruler,
                    country=country_model,
                    event_type=models.HistoricalEventType.ascension_perk,
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days,
                    description=matching_description,
                    is_known_to_player=country_model.is_known_to_player(),
                ))

    def _history_extract_edict_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        edict_list = country_dict.get("edicts", [])
        if not isinstance(edict_list, list):
            edict_list = [edict_list]
        for edict in edict_list:
            if not isinstance(edict, dict):
                continue
            expiry_date = models.date_to_days(edict.get("date"))
            description = self._get_or_add_shared_description(text=(edict.get("edict")))
            matching_event = self._session.query(
                models.HistoricalEvent
            ).filter_by(
                event_type=models.HistoricalEventType.edict,
                country=country_model,
                description=description,
                end_date_days=expiry_date,
            ).one_or_none()
            if matching_event is None:
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.edict,
                    country=country_model,
                    leader=ruler,
                    description=description,
                    start_date_days=self._date_in_days,
                    end_date_days=expiry_date,
                    is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
                ))

    def _settle_finished_wars(self):
        truces_dict = self._gamestate_dict.get("truce", {})
        if not isinstance(truces_dict, dict):
            return
        #  resolve wars based on truces...
        for truce_id, truce_info in truces_dict.items():
            if not isinstance(truce_info, dict):
                continue
            war_name = truce_info.get("name")
            truce_type = truce_info.get("truce_type", "other")
            if not war_name or truce_type != "war":
                continue  # truce is due to diplomatic agreements or similar
            matching_war = self._session.query(models.War).order_by(models.War.start_date_days.desc()).filter_by(name=war_name).first()
            if matching_war is None:
                continue
            end_date = truce_info.get("start_date")  # start of truce => end of war
            if isinstance(end_date, str) and end_date != None:
                matching_war.end_date_days = models.date_to_days(end_date)

            if matching_war.outcome == models.WarOutcome.in_progress:
                if matching_war.attacker_war_exhaustion < matching_war.defender_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.attacker_victory
                elif matching_war.defender_war_exhaustion < matching_war.attacker_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.defender_victory
                else:
                    matching_war.outcome = models.WarOutcome.status_quo
                self._history_add_peace_events(matching_war)
            self._session.add(matching_war)
        #  resolve wars that are no longer in the save files...
        for war in self._session.query(models.War).filter_by(outcome=models.WarOutcome.in_progress).all():
            if war.end_date_days < self._date_in_days - 5 * 360:
                war.outcome = models.WarOutcome.unknown
                self._session.add(war)
                self._history_add_peace_events(war)

    def _history_process_planet_and_sector_events(self, country_model, country_dict):
        sector_dict = country_dict.get("sectors", {})
        # processing all colonies by sector allows reading the responsible sector governor
        for sector_id, sector_info in sector_dict.items():
            sector_description = self._get_or_add_shared_description(text=(sector_info.get("name", "Unnamed")))
            sector_capital = self._session.query(models.Planet).filter_by(
                planet_id_in_game=sector_info.get("capital")
            ).one_or_none()
            self._history_add_sector_creation_event(country_model, country_dict, sector_capital, sector_description)
            governor_id = sector_info.get("leader")
            governor_model = None
            if governor_id is not None:
                governor_model = self._session.query(models.Leader).filter_by(
                    country=country_model,
                    leader_id_in_game=governor_id,
                ).one_or_none()

            self._history_add_planetary_events(country_model, sector_info, governor_model)
            if governor_model is not None:
                self._history_add_or_update_governor_sector_events(country_model, sector_capital, governor_model, sector_description)

    def _history_add_sector_creation_event(self, country_model, country_dict, sector_capital: models.Planet, sector_description: models.SharedDescription):
        # also add a sector creation event if this sector is new...
        sector_creation_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.sector_creation,
            country=country_model,
            description=sector_description,
        ).one_or_none()
        if sector_creation_event is None:
            country_data = country_model.get_most_recent_data()
            sector_creation_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.sector_creation,
                leader=self._get_current_ruler(country_dict),
                country=country_model,
                description=sector_description,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
            )
        if sector_creation_event.planet is None and sector_capital is not None:
            sector_creation_event.planet = sector_capital
            sector_creation_event.system = sector_capital.system
        self._session.add(sector_creation_event)

    def _history_add_planetary_events(self, country_model: models.Country, sector_dict, governor: models.Leader):
        for system_id in sector_dict.get("galactic_object", []):
            system_model = self._session.query(models.System).filter_by(
                system_id_in_game=system_id,
            ).one_or_none()
            if system_model is None:
                logger.info(f"Adding single system with in-game id {system_id}")
                system_model = self._add_single_system(system_id, country_model=country_model)
            system_dict = self._gamestate_dict.get("galactic_object").get(system_id, {})
            if system_model.original_name != system_dict.get("name"):
                system_model.original_name = system_dict.get("name")
                self._session.update(system_model)

            planets = system_dict.get("planet", [])
            if not isinstance(planets, list):
                planets = [planets]
            for planet_id in planets:
                planet_dict = self._gamestate_dict.get("planet", {}).get(planet_id)
                if not isinstance(planet_dict, dict):
                    continue
                planet_class = planet_dict.get("planet_class")
                is_colonizable = game_info.is_colonizable_planet(planet_class)
                is_destroyed = game_info.is_destroyed_planet(planet_class)
                is_terraformable = is_colonizable or any(m == "terraforming_candidate" for (m, _) in self._all_planetary_modifiers(planet_dict))

                if not (is_colonizable or is_destroyed or is_terraformable):
                    continue  # don't bother with uninteresting planets

                planet_model = self._get_and_update_planet_model(country_model, system_model, planet_id, planet_dict)
                if planet_model is None:
                    continue
                if is_colonizable:
                    self._history_add_or_update_colonization_events(country_model, system_model, planet_model, planet_dict, governor)
                    if game_info.is_colonizable_megastructure(planet_class):
                        self._history_add_or_update_habitable_megastructure_construction_event(country_model, system_model, planet_model, planet_dict, governor, system_id)
                if is_terraformable:
                    self._history_add_or_update_terraforming_events(country_model, system_model, planet_model, planet_dict, governor)

    def _history_add_or_update_terraforming_events(self, country_model: models.Country,
                                                   system_model: models.System,
                                                   planet_model: models.Planet,
                                                   planet_dict,
                                                   governor: models.Leader):
        terraform_dict = planet_dict.get("terraform_process")
        if not isinstance(terraform_dict, dict):
            return

        current_pc = planet_dict.get("planet_class")
        target_pc = terraform_dict.get("planet_class")
        text = f"{current_pc},{target_pc}"
        if not game_info.is_colonizable_planet(target_pc):
            logger.info(f"Unexpected target planet class for terraforming of {planet_model.planet_name}: From {planet_model.planet_class} to {target_pc}")
            return
        matching_description = self._get_or_add_shared_description(text)
        matching_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.terraforming,
            description=matching_description,
            system=system_model,
            planet=planet_model,
        ).order_by(models.HistoricalEvent.start_date_days.desc()).first()
        if matching_event is None or matching_event.end_date_days < self._date_in_days - 5 * 360:
            matching_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.terraforming,
                country=country_model,
                system=planet_model.system,
                planet=planet_model,
                leader=governor,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                description=matching_description,
                is_known_to_player=country_model.is_known_to_player(),
            )
        else:
            matching_event.end_date_days = self._date_in_days
        self._session.add(matching_event)

    def _get_and_update_planet_model(self, country_model: models.Country,
                                     system_model: models.System,
                                     planet_id: int,
                                     planet_dict) -> Union[models.Planet, None]:
        planet_class = planet_dict.get("planet_class")
        planet_name = planet_dict.get("name")
        planet_model = self._session.query(models.Planet).filter_by(
            planet_id_in_game=planet_id
        ).one_or_none()
        if planet_model is None:
            planet_model = models.Planet(
                planet_name=planet_name,
                planet_id_in_game=planet_id,
                system=system_model,
                planet_class=planet_class,
                colonized_date=None,
            )
        elif planet_model.planet_name != planet_name:
            planet_model.planet_name = planet_name
        if planet_model.planet_class != planet_class:
            if game_info.is_destroyed_planet(planet_class):
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.planet_destroyed,
                    country=country_model,
                    system=system_model,
                    planet=planet_model,
                    start_date_days=self._date_in_days,
                    is_of_global_relevance=True,
                    description=self._get_or_add_shared_description(planet_model.planet_class),
                    is_known_to_player=country_model.is_known_to_player(),
                ))
            planet_model.planet_class = planet_class
        self._session.add(planet_model)
        return planet_model

    def _all_planetary_modifiers(self, planet_dict):
        modifiers = planet_dict.get("timed_modifiers", [])
        if not isinstance(modifiers, list):
            modifiers = [modifiers]
        for m in modifiers:
            if not isinstance(m, dict):
                continue
            modifier = m.get("modifier", "no modifier")
            duration = m.get("days")
            if duration == "-1" or duration == -1 or duration is None or not isinstance(duration, int):
                duration = None
            yield modifier, duration

        planet_modifiers = planet_dict.get("planet_modifier", [])
        if not isinstance(planet_modifiers, list):
            planet_modifiers = [planet_modifiers]
        for pm in planet_modifiers:
            if pm is not None:
                yield pm, None

    def _history_add_or_update_colonization_events(self, country_model: models.Country,
                                                   system_model: models.System,
                                                   planet_model: models.Planet,
                                                   planet_dict,
                                                   governor: models.Leader):
        if "colonize_date" in planet_dict or planet_dict.get("pop"):
            # I think one of these occurs once the colonization is finished
            colonization_completed = True
        elif "colonizer_pop" in planet_dict:
            # while colonization still in progress
            colonization_completed = False
        else:
            # planet is not colonized at all
            return

        colonization_end_date = planet_dict.get("colonize_date")
        if not colonization_end_date:
            end_date_days = self._date_in_days
        else:
            end_date_days = models.date_to_days(colonization_end_date)

        if planet_model.colonized_date is not None:
            # abort early if the planet is already added and known to be fully colonized
            return
        elif colonization_completed:
            # set the planet's colonization flag and allow updating the event one last time
            planet_model.colonized_date = colonization_end_date
            self._session.add(planet_model)
        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.colonization,
            planet=planet_model
        ).one_or_none()
        if event is None:
            start_date = self._date_in_days
            if self._date_in_days < 100:
                end_date_days = min(end_date_days, 0)
                if country_model.is_player:
                    end_date_days = 0
                governor = None
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.colonization,
                leader=governor,
                country=country_model,
                start_date_days=min(start_date, end_date_days),
                end_date_days=end_date_days,
                planet=planet_model,
                system=system_model,
                is_known_to_player=country_model.is_known_to_player(),
            )
        else:
            event.end_date_days = end_date_days
        self._session.add(event)

    def _history_add_or_update_habitable_megastructure_construction_event(self, country_model: models.Country,
                                                                          system_model: models.System,
                                                                          planet_model: models.Planet,
                                                                          planet_dict,
                                                                          governor: models.Leader,
                                                                          system_id: int):
        planet_class = planet_dict.get("planet_class")
        if planet_class == "pc_ringworld_habitable":
            sys_name = self._gamestate_dict["galactic_object"].get(system_id).get("name", "Unknown system")
            p_name = f"{sys_name} Ringworld"
        elif planet_class == "pc_habitat":
            p_name = planet_dict.get("name")
        else:
            logging.info("Expected megastructre planet class")
            return

        description = self._get_or_add_shared_description(
            text=p_name,
        )
        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.habitat_ringworld_construction,
            system=planet_model.system,
            description=description,
        ).one_or_none()
        if event is None:
            start_date = end_date = self._date_in_days  # TODO: change this when tracking the construction sites in the future
            logger.info(f"{self._logger_str}: New Megastructure {models.days_to_date(self._date_in_days)}")
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.habitat_ringworld_construction,
                country=country_model,
                leader=governor,
                start_date_days=start_date,
                end_date_days=end_date,
                planet=planet_model,
                system=system_model,
                description=description,
                is_known_to_player=country_model.is_known_to_player(),
            )
        elif not event.is_known_to_player:
            event.is_known_to_player = country_model.is_known_to_player()
        self._session.add(event)

    def _history_add_or_update_governor_sector_events(self, country_model,
                                                      sector_capital: models.Planet,
                                                      governor: models.Leader,
                                                      sector_description: models.SharedDescription):
        # check if governor was ruling same sector before => update date and return
        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.governed_sector,
            description=sector_description,
        ).order_by(models.HistoricalEvent.end_date_days.desc()).first()
        if (event is not None
                and event.leader == governor
                and event.end_date_days > self._date_in_days - 5 * 360):  # if the governor ruled this sector less than 5 years ago, re-use the event...
            event.end_date_days = self._date_in_days
        else:
            country_data = country_model.get_most_recent_data()
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.governed_sector,
                leader=governor,
                country=country_model,
                description=sector_description,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
            )

        if event.planet is None and sector_capital is not None:
            event.planet = sector_capital
            event.system = sector_capital.system
        self._session.add(event)

    def _history_add_or_update_faction_leader_event(self,
                                                    country_model: models.Country,
                                                    faction_model: models.PoliticalFaction,
                                                    faction_dict):
        faction_leader_id = faction_dict.get("leader", -1)
        if faction_leader_id < 0:
            return
        leader = self._session.query(models.Leader).filter_by(
            country=country_model,
            leader_id_in_game=faction_leader_id,
        ).one_or_none()
        if leader is None:
            logger.warning(f"Could not find leader matching leader id {faction_leader_id} for {country_model.country_name}\n{faction_dict}")
            return
        matching_event = self._session.query(models.HistoricalEvent).filter_by(
            country=country_model,
            leader=leader,
            event_type=models.HistoricalEventType.faction_leader,
            faction=faction_model,
        ).one_or_none()
        country_data = country_model.get_most_recent_data()
        is_known = country_data is not None and country_data.attitude_towards_player.reveals_demographic_info()
        if matching_event is None:
            matching_event = models.HistoricalEvent(
                country=country_model,
                leader=leader,
                event_type=models.HistoricalEventType.faction_leader,
                faction=faction_model,
                start_date_days=self._date_in_days,
                end_date_days=self._date_in_days,
                is_known_to_player=is_known,
            )
        else:
            matching_event.is_known_to_player = is_known
            matching_event.end_date_days = self._date_in_days
        self._session.add(matching_event)

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
            country_model = self._session.query(models.Country).filter_by(country_id_in_game=country_id_in_game).one_or_none()
            if system is None:
                logger.info(f"{self._logger_str}Detected new system {system_id_in_game}!")
                system = self._add_single_system(system_id_in_game, country_model=country_model)
            if country_model is None:
                logger.warning(f"Cannot establish ownership for system {system_id_in_game} and country {country_id_in_game}")
                continue
            ownership = self._session.query(models.SystemOwnership).filter_by(
                system=system
            ).order_by(models.SystemOwnership.end_date_days.desc()).first()
            if ownership is not None:
                ownership.end_date_days = self._date_in_days
                self._session.add(ownership)
            if ownership is None or ownership.country != country_model:
                ownership = models.SystemOwnership(
                    start_date_days=self._date_in_days,
                    end_date_days=self._date_in_days + 1,
                    country=country_model,
                    system=system,
                )
                self._session.add(ownership)
        logger.info(f"{self._logger_str} Processed system ownership in {time.clock()-start}s")

    def _history_add_peace_events(self, war: models.War):
        for wp in war.participants:
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                event_type=models.HistoricalEventType.peace,
                country=wp.country,
                war=war,
            ).one_or_none()
            if matching_event is None:
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.peace,
                    war=war,
                    country=wp.country,
                    leader=self._get_current_ruler(self._gamestate_dict["country"].get(wp.country.country_id_in_game, {})),
                    start_date_days=war.end_date_days,
                    is_known_to_player=wp.country.is_known_to_player(),
                ))

    def _get_or_add_shared_description(self, text: str) -> models.SharedDescription:
        matching_description = self._session.query(models.SharedDescription).filter_by(
            text=text,
        ).one_or_none()
        if matching_description is None:
            matching_description = models.SharedDescription(text=text)
            self._session.add(matching_description)
        return matching_description

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
