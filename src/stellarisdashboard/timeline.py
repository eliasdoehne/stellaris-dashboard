import logging
from typing import Dict, Any

from stellarisdashboard import models

logger = logging.getLogger(__name__)


class TimelineExtractor:
    """ Process data from parsed save file dictionaries and add it to the database. """

    def __init__(self):
        self.gamestate_dict = None
        self.game = None
        self._session = None
        self._player_country = None
        self._current_gamestate = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None

    def process_gamestate(self, game_name: str, gamestate_dict: Dict[str, Any]):
        date_str = gamestate_dict["date"]
        logger.debug(f"Processing {game_name}, {date_str}")
        self.gamestate_dict = gamestate_dict
        if len({player["country"] for player in self.gamestate_dict["player"]}) != 1:
            logger.warning("Player country is ambiguous!")
            return None
        self._player_country = self.gamestate_dict["player"][0]["country"]
        player_country_name = self.gamestate_dict["country"][self._player_country]["name"]
        self._session = models.SessionFactory()
        try:
            self.game = self._session.query(models.Game).filter_by(game_name=game_name).first()
            if self.game is None:
                logger.info(f"Adding new game {game_name} to database.")
                self.game = models.Game(game_name=game_name, player_country_name=player_country_name)
                self._session.add(self.game)
            days = models.date_to_days(self.gamestate_dict["date"])
            game_states_by_date = {gs.date: gs for gs in self.game.game_states}
            if days in game_states_by_date:
                logger.info(f"Gamestate for {self.game.game_name}, date {date_str} exists! Replacing...")
                self._session.delete(game_states_by_date[days])
            self._process_gamestate()
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            logger.error(e)
            raise e
        finally:
            self._session.close()
        self._current_gamestate = None
        self.gamestate_dict = None
        self._player_country = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._player_monthly_trade_info = None
        self._session = None

    def _process_gamestate(self):
        days = models.date_to_days(self.gamestate_dict["date"])
        self._extract_player_trade_agreements()
        player_economy = self._extract_player_economy(self.gamestate_dict["country"][self._player_country])
        self._current_gamestate = models.GameState(
            game=self.game, date=days,
            **player_economy
        )
        self._session.add(self._current_gamestate)

        for country_id, country_data in self.gamestate_dict["country"].items():
            if not isinstance(country_data, dict):
                continue  # can be "none", apparently
            if country_data["type"] != "default":
                continue  # Enclaves, Leviathans, etc ....
            country_state = self._extract_country_info(country_id, country_data)

            debug_name = country_data.get('name', 'Unnamed Country') if country_state.attitude_towards_player.is_known() else 'Unknown'
            logger.debug(f"Extracting country data: {country_id}, {debug_name}")
            self._extract_country_pop_info(country_data, country_state)

    def _extract_country_info(self, country_id, country_dict):
        is_player = (country_id == self._player_country)
        country = self._session.query(models.Country).filter_by(game=self.game, country_name=country_dict["name"]).one_or_none()
        if country is None:
            country = models.Country(is_player=is_player, game=self.game, country_name=country_dict["name"])
            self._session.add(country)
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
            military_power=country_dict["military_power"],
            fleet_size=country_dict["fleet_size"],
            tech_progress=len(country_dict["tech_status"]["technology"]),
            exploration_progress=len(country_dict["surveyed"]),
            owned_planets=len(country_dict["owned_planets"]),
            has_research_agreement_with_player=has_research_agreement_with_player,
            has_sensor_link_with_player=has_sensor_link_with_player,
            attitude_towards_player=attitude_towards_player,
            **economy_data,
        )
        self._session.add(country_data)
        self._extract_factions(country_data)
        return country_data

    def _extract_player_economy(self, country_dict):
        base_economy_data = self._extract_economy_data(country_dict)

        last_month = self._get_last_month_budget(country_dict)
        economy_dict = {}
        energy_budget = last_month.get("values", {})
        economy_dict.update(
            energy_income_base=energy_budget.get("base_income", 0),
            energy_income_production=energy_budget.get("produced_energy", 0),
            energy_income_trade=self._player_monthly_trade_info["energy_trade_income"],
            energy_income_mission=energy_budget.get("mission_expense", 0),
            energy_spending_army=energy_budget.get("army_maintenance", 0),
            energy_spending_colonization=energy_budget.get("colonization", 0),
            energy_spending_starbases=energy_budget.get("starbase_maintenance", 0),
            energy_spending_trade=self._player_monthly_trade_info["energy_trade_spending"],
            energy_spending_building=energy_budget.get("buildings", 0),
            energy_spending_pop=energy_budget.get("pops", 0),
            energy_spending_ship=energy_budget.get("ship_maintenance", 0),
            energy_spending_station=energy_budget.get("station_maintenance", 0),
            mineral_income_production=base_economy_data.get("mineral_income", 0),
            mineral_income_trade=self._player_monthly_trade_info["mineral_trade_income"],
            mineral_spending_pop=last_month.get("pop_mineral_maintenance", 0),
            mineral_spending_ship=last_month.get("ship_mineral_maintenances", 0),
            mineral_spending_trade=self._player_monthly_trade_info["mineral_trade_spending"],
            food_income_production=base_economy_data.get("food_income", 0),
            food_income_trade=self._player_monthly_trade_info["food_trade_income"],
            food_spending=base_economy_data.get("food_spending", 0),
            food_spending_trade=self._player_monthly_trade_info["food_trade_spending"],
        )
        return economy_dict

    def _get_last_month_budget(self, country_dict):
        if "budget" in country_dict and country_dict["budget"] != "none" and country_dict["budget"]:
            return country_dict["budget"].get("last_month", {})
        return {}

    def _extract_economy_data(self, country_dict):
        economy = {}
        if "modules" in country_dict and "standard_economy_module" in country_dict["modules"] and "last_month" in country_dict["modules"]["standard_economy_module"]:
            economy_module = country_dict["modules"]["standard_economy_module"]["last_month"]
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
                val_list = economy_module.get(dict_key, default)
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
            mineral_trade_income=0.0,
            mineral_trade_spending=0.0,
            energy_trade_income=0.0,
            energy_trade_spending=0.0,
            food_trade_income=0.0,
            food_trade_spending=0.0,
        )
        trades = self.gamestate_dict.get("trade_deal", {})
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
            self._player_monthly_trade_info["mineral_trade_spending"] += player_resources.get("minerals", 0.0)
            self._player_monthly_trade_info["energy_trade_spending"] += player_resources.get("energy", 0.0)
            self._player_monthly_trade_info["food_trade_spending"] += player_resources.get("food", 0.0)
            other_resources = second.get("monthly_resources", {})
            self._player_monthly_trade_info["mineral_trade_income"] += other_resources.get("minerals", 0.0)
            self._player_monthly_trade_info["energy_trade_income"] += other_resources.get("energy", 0.0)
            self._player_monthly_trade_info["food_trade_income"] += other_resources.get("food", 0.0)

    def _extract_factions(self, country_data: models.CountryData):
        for faction_id, faction_data in self.gamestate_dict.get("pop_factions", {}).items():
            if not faction_data or faction_data == "none":
                continue
            faction_name = faction_data["name"]
            faction_country_name = self.gamestate_dict["country"][faction_data["country"]]["name"]
            if faction_country_name != country_data.country.country_name:
                continue
            # If the faction is in the database, get it, otherwise add a new faction
            faction = self._session.query(models.PoliticalFaction).filter_by(faction_name=faction_name, country=country_data.country).one_or_none()
            if faction is None:
                ethics = models.PopEthics.from_str(faction_data["type"])
                if ethics == models.PopEthics.other:
                    print(f"Found faction with unknown Ethics: {faction_data}")
                faction = models.PoliticalFaction(
                    country=country_data.country,
                    faction_name=faction_name,
                    ethics=ethics,
                )
                self._session.add(faction)
            members = len(faction_data.get("members", []))
            self._session.add(models.FactionSupport(
                faction=faction,
                country_data=country_data,
                members=members,
                support=faction_data.get("support", 0),
                happiness=faction_data.get("happiness", 0),
            ))

    def _extract_country_pop_info(self, country_dict: Dict[str, Any], country_data: models.CountryData):
        species_demographics = {}
        pop_data = self.gamestate_dict["pop"]
        for planet_id in country_dict["owned_planets"]:
            planet_data = self.gamestate_dict["planet"][planet_id]
            for pop_id in planet_data.get("pop", []):
                if pop_id not in pop_data:
                    logger.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                pop_species_index = pop_data[pop_id]["species_index"]
                if pop_species_index not in species_demographics:
                    species_demographics[pop_species_index] = 0
                if pop_data[pop_id]["growth_state"] == 1:
                    species_demographics[pop_species_index] += 1

        for pop_species_index, pop_count in species_demographics.items():
            species_name = self.gamestate_dict["species"][pop_species_index]["name"]
            species = self._session.query(models.Species).filter_by(game=self.game, species_name=species_name).one_or_none()
            if species is None:
                species = models.Species(game=self.game, species_name=species_name)
                self._session.add(species)

            pop_count = models.PopCount(
                country_data=country_data,
                species=species,
                pop_count=pop_count,
            )
            self._session.add(pop_count)
