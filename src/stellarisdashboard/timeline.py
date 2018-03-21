import itertools
import logging
from typing import Dict, Any, List

from stellarisdashboard import models

logger = logging.getLogger(__name__)


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
        self._player_country = None
        self._current_gamestate = None
        self._country_starbasecount_dict = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None
        self._factionless_pops = None
        self._date_in_days = None

        self._enclave_trade_modifiers = None
        self._initialize_enclave_trade_info()

    def process_gamestate(self, game_name: str, gamestate_dict: Dict[str, Any]):
        date_str = gamestate_dict["date"]
        logger.debug(f"Processing {game_name}, {date_str}")
        self._gamestate_dict = gamestate_dict
        if len({player["country"] for player in self._gamestate_dict["player"]}) != 1:
            logger.warning("Player country is ambiguous!")
            return None
        self._player_country = self._gamestate_dict["player"][0]["country"]
        player_country_name = self._gamestate_dict["country"][self._player_country]["name"]
        with models.get_db_session(game_name) as session:
            self._session = session
            try:
                self.game = self._session.query(models.Game).filter_by(game_name=game_name).first()
                if self.game is None:
                    logger.info(f"Adding new game {game_name} to database.")
                    self.game = models.Game(game_name=game_name, player_country_name=player_country_name)
                    self._session.add(self.game)
                self._date_in_days = models.date_to_days(self._gamestate_dict["date"])
                game_states_by_date = {gs.date: gs for gs in self.game.game_states}
                if self._date_in_days in game_states_by_date:
                    logger.info(f"Gamestate for {self.game.game_name}, date {date_str} exists! Replacing...")
                    self._session.delete(game_states_by_date[self._date_in_days])
                self._process_gamestate()
                self._session.commit()
            except Exception as e:
                self._session.rollback()
                logger.error(e)
                raise e
            finally:
                self._session = None
                self._reset_state()

    def _process_gamestate(self):
        self._extract_player_trade_agreements()
        self._extract_country_starbase_count()
        player_economy = self._extract_player_economy(self._gamestate_dict["country"][self._player_country])
        self._current_gamestate = models.GameState(
            game=self.game, date=self._date_in_days,
            **player_economy
        )
        self._session.add(self._current_gamestate)

        for country_id, country_data_dict in self._gamestate_dict["country"].items():
            if not isinstance(country_data_dict, dict):
                continue  # can be "none", apparently
            if country_data_dict["type"] != "default":
                continue  # Enclaves, Leviathans, etc ....
            country_data = self._extract_country_data(country_id, country_data_dict)

            debug_name = country_data_dict.get('name', 'Unnamed Country') if country_data.attitude_towards_player.is_known() else 'Unknown'
            logger.debug(f"Extracting country data: {country_id}, {debug_name}")
            self._extract_pop_info_from_planets(country_data_dict, country_data)
            if country_data.country.is_player:
                self._extract_factions(country_data)
                self._extract_player_leaders(country_data_dict.get("owned_leaders", []))

        self._extract_wars()

    def _extract_country_starbase_count(self):
        self._country_starbasecount_dict = {}
        for starbase_dict in self._gamestate_dict.get("starbases", {}).values():
            if not isinstance(starbase_dict, dict):
                continue
            owner_id = starbase_dict.get("owner", -1)
            if owner_id not in self._country_starbasecount_dict:
                self._country_starbasecount_dict[owner_id] = 0
            self._country_starbasecount_dict[owner_id] += 1

    def _extract_country_data(self, country_id, country_dict) -> models.CountryData:
        is_player = (country_id == self._player_country)
        country = self._session.query(models.Country).filter_by(
            game=self.game,
            country_id_in_game=country_id
        ).one_or_none()
        if country is None:
            country = models.Country(is_player=is_player, country_id_in_game=country_id, game=self.game, country_name=country_dict["name"])
            self._session.add(country)
        has_research_agreement_with_player = is_player or (country_id in self._player_research_agreements)

        has_sensor_link_with_player = is_player or (country_id in self._player_sensor_links)
        if is_player:
            attitude_towards_player = models.Attitude.friendly
        else:
            attitude_towards_player = self._extract_ai_attitude_towards_player(country_dict)

        economy_data = self._extract_economy_data(country_dict)
        diplomacy_data = self._extract_diplomacy_toward_player(country_dict)
        country_data = models.CountryData(
            country=country,
            game_state=self._current_gamestate,
            military_power=country_dict.get("military_power", 0),
            fleet_size=country_dict.get("fleet_size", 0),
            tech_progress=len(country_dict.get("tech_status", {}).get("technology", [])),
            exploration_progress=len(country_dict.get("surveyed", 0)),
            owned_planets=len(country_dict.get("owned_planets", [])),
            controlled_systems=self._country_starbasecount_dict.get(country_id, 0),
            has_research_agreement_with_player=has_research_agreement_with_player,
            has_sensor_link_with_player=has_sensor_link_with_player,
            attitude_towards_player=attitude_towards_player,
            **economy_data,
            **diplomacy_data,
        )
        self._session.add(country_data)
        return country_data

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
        if relations_manager == "none":
            return diplomacy_info
        for relation in relations_manager:
            if not relation == "none":
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
        )
        return economy_dict

    def _extract_enclave_resource_deals(self, country_dict):
        enclave_deals = dict(
            mineral_income_enclaves=0,
            mineral_spending_enclaves=0,
            energy_income_enclaves=0,
            energy_spending_enclaves=0,
        )

        for modifier_dict in country_dict.get("timed_modifier", []):
            if not isinstance(modifier_dict, dict):
                continue
            modifier_id = modifier_dict.get("modifier", "")
            enclave_trade_budget_dict = self._enclave_trade_modifiers.get(modifier_id, {})
            for budget_item, amount in enclave_trade_budget_dict.items():
                enclave_deals[budget_item] += amount
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

    def _extract_factions(self, country_data: models.CountryData):
        faction_pop_sum = 0
        for faction_id, faction_data in self._gamestate_dict.get("pop_factions", {}).items():
            if not faction_data or faction_data == "none":
                continue
            faction_name = faction_data["name"]
            country_id = faction_data["country"]
            faction_country_name = self._gamestate_dict["country"][country_id]["name"]
            if faction_country_name != country_data.country.country_name:
                continue
            # If the faction is in the database, get it, otherwise add a new faction
            members = len(faction_data.get("members", []))
            faction_pop_sum += members
            self._add_faction_and_faction_support(
                faction_id=faction_id,
                faction_name=faction_name,
                country_data=country_data,
                members=members,
                support=faction_data.get("support", 0),
                happiness=faction_data.get("happiness", 0),
                ethics=models.PopEthics.from_str(faction_data["type"]),
            )

        for faction_name, num in self._factionless_pops.items():
            if not num:
                continue
            faction_pop_sum += num
            self._add_faction_and_faction_support(
                faction_id=self.NO_FACTION_ID_MAP[faction_name],
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
                faction_id=self.NO_FACTION_ID,
                faction_name=TimelineExtractor.NO_FACTION,
                country_data=country_data,
                members=no_faction_pops,
                support=0,
                happiness=0,
                ethics=models.PopEthics.no_ethics
            )
            self._factionless_pops = None

    def _add_faction_and_faction_support(self,
                                         faction_id: int,
                                         faction_name: str,
                                         country_data: models.CountryData,
                                         members: int,
                                         support: float,
                                         happiness: float,
                                         ethics: models.PopEthics,
                                         ):
        faction = self._session.query(models.PoliticalFaction).filter_by(
            faction_id_in_game=faction_id,
            country=country_data.country,
        ).one_or_none()
        if faction is None:
            faction = models.PoliticalFaction(
                country=country_data.country,
                faction_name=faction_name,
                faction_id_in_game=faction_id,
                ethics=ethics,
            )
            self._session.add(faction)
        fs = models.FactionSupport(faction=faction, country_data=country_data, members=members, support=support, happiness=happiness)
        self._session.add(fs)

    def _extract_pop_info_from_planets(self, country_dict: Dict[str, Any], country_data: models.CountryData):
        self._factionless_pops = {
            TimelineExtractor.SLAVE_FACTION_NAME: 0,
            TimelineExtractor.PURGE_FACTION_NAME: 0,
            TimelineExtractor.NON_SENTIENT_ROBOT_NO_FACTION: 0,
        }
        species_demographics = {}
        pop_data = self._gamestate_dict["pop"]

        for planet_id in country_dict.get("owned_planets", []):
            planet_data = self._gamestate_dict["planet"][planet_id]
            for pop_id in planet_data.get("pop", []):
                if pop_id not in pop_data:
                    logger.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                this_pop = pop_data[pop_id]
                if this_pop["growth_state"] != 1:
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
        wars_dict = self._gamestate_dict.get("war", {})
        if not wars_dict:
            return
        for game_war_id, war_dict in wars_dict.items():
            if war_dict == "none":
                continue
            db_war_id = game_war_id
            war = self._session.query(models.War).filter_by(game=self.game, war_id=db_war_id).one_or_none()
            if war is None:
                start_date_days = models.date_to_days(war_dict["start_date"])
                war = models.War(
                    war_id=db_war_id,
                    game=self.game,
                    start_date_days=start_date_days,
                    name=war_dict.get("name", "Unnamed war")
                )
                self._session.add(war)

            attackers = {p["country"] for p in war_dict["attackers"]}
            for war_party_info in itertools.chain(war_dict["attackers"], war_dict["defenders"]):
                country_id = war_party_info.get("country")
                country_name = self._gamestate_dict["country"][country_id]["name"]
                db_country = self._session.query(models.Country).filter_by(game=self.game, country_name=country_name).one()

                is_attacker = country_id in attackers

                war_participant = self._session.query(models.WarParticipant).filter_by(war=war, country=db_country).one_or_none()
                if war_participant is None:
                    war_participant = models.WarParticipant(
                        war=war,
                        country=db_country,
                        is_attacker=is_attacker,
                    )
                    self._session.add(war_participant)

                exhaustion = war_dict.get("attacker_war_exhaustion") if is_attacker else war_dict.get("defender_war_exhaustion")
                war_status = models.WarEvent(
                    war_participant=war_participant,
                    date=self._date_in_days,
                    war_exhaustion=exhaustion
                )
                self._session.add(war_status)

    def _extract_player_leaders(self, player_owned_leaders: List[int]):
        for leader_id in player_owned_leaders:
            leader_dict = self._gamestate_dict["leaders"].get(leader_id)
            if leader_dict is None or leader_dict == "none":
                continue
            leader = self._session.query(models.Leader).filter_by(game=self.game, leader_id_in_game=leader_id).one_or_none()
            if leader is None:
                leader = self._add_new_leader(leader_id, leader_dict)
            # TODO Do something with this info?

    def _add_new_leader(self, leader_id, leader_dict):
        leader_class = models.LeaderClass.__members__.get(leader_dict.get("class"), models.LeaderClass.unknown)
        leader_gender = models.LeaderGender.__members__.get(leader_dict.get("gender"), models.LeaderGender.other)
        leader_agenda = models.AGENDA_STR_TO_ENUM.get(leader_dict.get("agenda"), models.LeaderAgenda.other)
        first_name = leader_dict['name']['first_name']
        last_name = leader_dict['name'].get('second_name', "")
        leader_name = f"{first_name} {last_name}".strip()

        date_hired = min(
            self._date_in_days,
            models.date_to_days(leader_dict.get("date", "10000.01.01")),
            models.date_to_days(leader_dict.get("start", "10000.01.01")),
            models.date_to_days(leader_dict.get("date_added", "10000.01.01")),
        )
        date_born = date_hired - 360 * leader_dict.get("age", 0.0)
        leader = models.Leader(
            leader_id_in_game=leader_id,
            leader_class=leader_class,
            leader_name=leader_name,
            leader_agenda=leader_agenda,
            game_id=self.game.game_id,
            gender=leader_gender,
            date_hired=date_hired,
            date_born=date_born,
        )
        self._session.add(leader)
        return leader

    def _initialize_enclave_trade_info(self):
        trade_level_1 = [10, 20]
        trade_level_2 = [25, 50]
        trade_level_3 = [50, 100]
        trade_for_minerals = ["mineral_income_enclaves", "energy_spending_enclaves"]
        trade_for_energy = ["energy_income_enclaves", "mineral_spending_enclaves"]
        self._enclave_trade_modifiers = {
            "enclave_mineral_trade_1_mut": dict(zip(trade_for_minerals, trade_level_1)),
            "enclave_mineral_trade_1_rig": dict(zip(trade_for_minerals, trade_level_1)),
            "enclave_mineral_trade_1_xur": dict(zip(trade_for_minerals, trade_level_1)),
            "enclave_mineral_trade_2_mut": dict(zip(trade_for_minerals, trade_level_2)),
            "enclave_mineral_trade_2_rig": dict(zip(trade_for_minerals, trade_level_2)),
            "enclave_mineral_trade_2_xur": dict(zip(trade_for_minerals, trade_level_2)),
            "enclave_mineral_trade_3_mut": dict(zip(trade_for_minerals, trade_level_3)),
            "enclave_mineral_trade_3_rig": dict(zip(trade_for_minerals, trade_level_3)),
            "enclave_mineral_trade_3_xur": dict(zip(trade_for_minerals, trade_level_3)),
            "enclave_energy_trade_1_mut": dict(zip(trade_for_energy, trade_level_1)),
            "enclave_energy_trade_1_rig": dict(zip(trade_for_energy, trade_level_1)),
            "enclave_energy_trade_1_xur": dict(zip(trade_for_energy, trade_level_1)),
            "enclave_energy_trade_2_mut": dict(zip(trade_for_energy, trade_level_2)),
            "enclave_energy_trade_2_rig": dict(zip(trade_for_energy, trade_level_2)),
            "enclave_energy_trade_2_xur": dict(zip(trade_for_energy, trade_level_2)),
            "enclave_energy_trade_3_mut": dict(zip(trade_for_energy, trade_level_3)),
            "enclave_energy_trade_3_rig": dict(zip(trade_for_energy, trade_level_3)),
            "enclave_energy_trade_3_xur": dict(zip(trade_for_energy, trade_level_3)),
        }

    def _reset_state(self):
        self._country_starbasecount_dict = None
        self._current_gamestate = None
        self._gamestate_dict = None
        self._player_country = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None
        self._session = None
        self._date_in_days = None
